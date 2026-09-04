"""가드레일 준수 검증 — SERVICE.md 4절의 G1~G9 를 실제로 지키는지 확인합니다.

**실제 LLM 을 호출하지 않습니다.** 가짜 모델을 꽂아 자격증명 없이 돌아갑니다.

여기서 쓰는 가짜 모델 중 일부는 **일부러 나쁜 짓을 하려 듭니다.**
(테스트를 통과시키려고 엉뚱한 내용을 뱉거나, 절대 안 고쳐지는 코드를 계속 내놓거나)
가드레일이 구조로 막혀 있다면 LLM 이 무엇을 출력하든 위반이 발생하지 않아야 합니다.
**주석으로 약속한 가드레일이 아니라 구조로 강제한 가드레일인지를 보는 시험입니다.**
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SELFHEAL_ROOT = Path(__file__).resolve().parents[1]
if str(SELFHEAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SELFHEAL_ROOT))

from src import agent as heal  # noqa: E402
from src.report import (  # noqa: E402
    EXIT_FIXED,
    EXIT_NOTHING_TO_FIX,
    EXIT_NOT_FIXED,
    EXIT_PRECONDITION,
    EXIT_PROPOSED,
)
from src.tools import ADAPTERS, extract_symbols, locate_target  # noqa: E402

from conftest import ScriptedChatModel  # noqa: E402

SAMPLES = SELFHEAL_ROOT / "data" / "samples"
PY = ADAPTERS["python"]

# 테스트를 통과시키는 '정답' 소스입니다.
FIXED_SOURCE = (
    "def double_at(items, index):\n"
    "    if items is None:\n"
    "        return 0\n"
    "    if not isinstance(index, int) or index < 0 or index >= len(items):\n"
    "        return 0\n"
    "    return items[index] * 2\n"
)

# 절대 통과하지 못하는 소스입니다. 재시도를 소진시킬 때 씁니다.
NEVER_FIXES = "def double_at(items, index):\n    return items[index] * 2\n"


# 가짜 모델은 conftest.py 의 ScriptedChatModel 을 씁니다.
# 구조화 출력이 LCEL 체인이므로 모델이 Runnable(BaseChatModel) 이어야 합니다.
def ScriptedLLM(source: str) -> ScriptedChatModel:
    return ScriptedChatModel(source=source)


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    dest = tmp_path / "py-index"
    shutil.copytree(SAMPLES / "py-index", dest)
    return dest


def _read_report(workdir: Path) -> dict:
    return json.loads((workdir / ".heal" / "report.json").read_text(encoding="utf-8"))


# ── G1. 테스트 파일을 수정하지 않는다 ────────────────────────────
def test_G1_테스트파일은_수정후보에서_구조적으로_제외된다():
    assert PY.is_source_file(Path("boundary.py")) is True
    assert PY.is_source_file(Path("test_boundary.py")) is False
    assert PY.is_source_file(Path("boundary_test.py")) is False
    assert PY.is_source_file(Path("conftest.py")) is False


def test_G1_실행_후에도_테스트파일이_그대로다(workdir: Path, memory):
    """LLM 이 무엇을 뱉든 테스트 파일에는 도달할 수 없어야 합니다."""
    before = (workdir / "test_boundary.py").read_text(encoding="utf-8")
    llm = ScriptedLLM(NEVER_FIXES)
    heal.main([str(workdir), "--max-attempts", "2", "--apply"], llm=llm, memory=memory)
    assert (workdir / "test_boundary.py").read_text(encoding="utf-8") == before


# ── G2. 작업 디렉터리 밖을 수정하지 않는다 ───────────────────────
def test_G2_workdir_밖의_트레이스백_프레임은_버린다(workdir: Path):
    """표준 라이브러리 프레임을 고치려 들면 안 됩니다."""
    outside = workdir.parent / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    output = (
        f'  File "{outside}", line 1, in <module>\n'
        "IndexError: list index out of range\n"
    )
    path, _ = locate_target(PY, output, workdir)
    assert path is None


# ── G3. 의존성·빌드 산출물을 수정하지 않는다 ─────────────────────
def test_G3_의존성_디렉터리는_제외된다(workdir: Path):
    vendored = workdir / "site-packages" / "lib.py"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("def f():\n    return []\n", encoding="utf-8")
    output = f'  File "{vendored}", line 2, in f\nIndexError\n'
    path, _ = locate_target(PY, output, workdir)
    assert path is None


# ── G4. 설정·매니페스트 파일을 수정하지 않는다 ───────────────────
def test_G4_설정파일은_소스가_아니다():
    """테스트 설정을 무력화해 통과시키는 우회로를 막습니다."""
    for name in ("pyproject.toml", "package.json", "go.mod", "pytest.ini"):
        assert PY.is_source_file(Path(name)) is False


# ── G5. 재시도 상한을 넘기지 않는다 ──────────────────────────────
def test_G5_재시도_상한을_넘지_않는다(workdir: Path, memory):
    """원본 노트북에는 이 제동장치가 없어 무한 루프가 가능했습니다."""
    llm = ScriptedLLM(NEVER_FIXES)
    code = heal.main([str(workdir), "--max-attempts", "2"], llm=llm, memory=memory)
    assert _read_report(workdir)["attempts"] == 2
    assert code == EXIT_NOT_FIXED


# ── 못 고친 진단은 메모리에 쌓이지 않는다 (실제로 고쳐진 것만 기억한다) ──
def test_실패로_끝나면_메모리에_아무것도_안_남긴다(workdir: Path, memory):
    """재시도를 다 써도 못 고치면, 그동안의 진단·병합 시도는 전부 버려져야 한다.

    라우터 미등록처럼 한 번도 안 고쳐지는 진단이 시도할 때마다 메모리에 쌓여
    발생 횟수만 계속 올라가던 문제(실사용 중 발견)를 구조적으로 막는 가드레일이다.
    """
    llm = ScriptedLLM(NEVER_FIXES)
    code = heal.main([str(workdir), "--max-attempts", "2"], llm=llm, memory=memory)
    assert code == EXIT_NOT_FIXED
    assert memory.stats() == []


# ── exit 1(못 고침)이면 일반 결함 제안(notice.html)을 참고용으로 남긴다 ──
def test_실패하면_notice_html에_일반_결함_제안을_남긴다(
    workdir: Path, memory, monkeypatch: pytest.MonkeyPatch
):
    """수정은 자동 적용되지 않는다 — 제안만 HTML로 남기고 원본은 그대로여야 한다."""
    opened: list[str] = []
    monkeypatch.setattr(heal.webbrowser, "open", lambda url: opened.append(url) or True)
    before = (workdir / "boundary.py").read_text(encoding="utf-8")
    llm = ScriptedLLM(NEVER_FIXES)
    code = heal.main([str(workdir), "--max-attempts", "1"], llm=llm, memory=memory)

    assert code == EXIT_NOT_FIXED
    notice_path = workdir / ".heal" / "notice.html"
    assert notice_path.exists()
    assert opened == [notice_path.resolve().as_uri()]
    # 원본은 여전히 그대로여야 한다 — 이 제안은 절대 자동 적용되지 않는다.
    assert (workdir / "boundary.py").read_text(encoding="utf-8") == before


# ── G6. 원본은 사용자 승인 없이 수정하지 않는다 ──────────────────
def test_G6_기본실행은_원본을_수정하지_않는다(workdir: Path, memory):
    """**이 프로젝트에서 가장 중요한 가드레일입니다.**

    수정에 성공하더라도 사용자가 ``--apply`` 로 승인하기 전에는 원본을 건드리지 않습니다.
    모든 작업은 샌드박스 사본에서 이뤄지고, 사용자는 검증이 끝난 diff 만 받습니다.
    """
    before = (workdir / "boundary.py").read_text(encoding="utf-8")
    llm = ScriptedLLM(FIXED_SOURCE)
    code = heal.main([str(workdir)], llm=llm, memory=memory)

    assert code == EXIT_PROPOSED, "승인 대기 상태여야 합니다."
    assert (workdir / "boundary.py").read_text(encoding="utf-8") == before
    # 검수할 diff 는 산출물로 남아 있어야 합니다.
    assert (workdir / ".heal" / "patch.diff").exists()
    assert _read_report(workdir)["applied"] is False


def test_G6_apply를_붙이면_실제로_적용된다(workdir: Path, memory):
    """승인이 있으면 그때 원본에 씁니다."""
    llm = ScriptedLLM(FIXED_SOURCE)
    code = heal.main([str(workdir), "--apply"], llm=llm, memory=memory)

    assert code == EXIT_FIXED
    assert (workdir / "boundary.py").read_text(encoding="utf-8") == FIXED_SOURCE
    assert _read_report(workdir)["applied"] is True


# ── 제안(exit 4)이 나오면 patch.diff 를 HTML 로 열어 준다 (항상 켜짐, 옵션 없음) ──
def test_제안이_나오면_항상_patch_html을_만들고_브라우저를_연다(
    workdir: Path, memory, monkeypatch: pytest.MonkeyPatch
):
    """실제 브라우저는 띄우지 않고, ``webbrowser.open`` 이 patch.html 경로로 호출됐는지만 봅니다."""
    opened: list[str] = []
    monkeypatch.setattr(heal.webbrowser, "open", lambda url: opened.append(url) or True)
    llm = ScriptedLLM(FIXED_SOURCE)
    code = heal.main([str(workdir)], llm=llm, memory=memory)

    assert code == EXIT_PROPOSED
    html_path = workdir / ".heal" / "patch.html"
    assert html_path.exists()
    assert opened == [html_path.resolve().as_uri()]


def test_G6_실패하면_apply여도_원본은_그대로다(workdir: Path, memory):
    """반쯤 고친 코드를 남기는 것이 가장 나쁜 결과입니다.
    샌드박스에서만 작업하므로 복원할 것도 없이 애초에 변하지 않습니다."""
    before = (workdir / "boundary.py").read_text(encoding="utf-8")
    llm = ScriptedLLM(NEVER_FIXES)
    heal.main(
        [str(workdir), "--max-attempts", "1", "--apply"], llm=llm, memory=memory
    )
    assert (workdir / "boundary.py").read_text(encoding="utf-8") == before


# ── G7. 전제 불충족이면 LLM 을 호출하지 않고 즉시 중단한다 ───────
def test_G7_테스트가_없으면_LLM을_한번도_부르지_않는다(tmp_path: Path, memory):
    """툴체인이나 테스트가 없으면 테스트는 무조건 실패합니다.
    그 상태로 그래프에 들어가면 멀쩡한 코드를 계속 고치려 들며 토큰만 태웁니다."""
    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    llm = ScriptedLLM("irrelevant")
    code = heal.main([str(tmp_path)], llm=llm, memory=memory)
    assert code == EXIT_PRECONDITION
    assert llm.calls == 0


def test_G7_툴체인이_없으면_즉시_중단한다(workdir: Path, memory):
    llm = ScriptedLLM("irrelevant")
    # 이 환경에는 Go 툴체인이 없습니다. --lang 으로 강제하면 preflight 에서 걸려야 합니다.
    code = heal.main([str(workdir), "--lang", "go"], llm=llm, memory=memory)
    assert code == EXIT_PRECONDITION
    assert llm.calls == 0


# ── G8. --apply 없이는 어떤 파일도 쓰지 않는다 ──────────────────
def test_G8_dry_run은_기본동작과_같다(workdir: Path, memory):
    """``--dry-run`` 은 이제 기본 동작과 같습니다. 명시적으로 써도 결과가 같아야 합니다."""
    before = (workdir / "boundary.py").read_text(encoding="utf-8")
    llm = ScriptedLLM(FIXED_SOURCE)
    code = heal.main([str(workdir), "--dry-run"], llm=llm, memory=memory)
    assert code == EXIT_PROPOSED
    assert (workdir / "boundary.py").read_text(encoding="utf-8") == before


# ── G9. 무관한 심볼을 지우거나 시그니처를 바꾸지 않는다 ──────────
def test_G9_심볼_추출이_이름과_인자수를_함께_본다():
    """시그니처가 바뀌면 호출자가 깨지므로 인자 수까지 봅니다."""
    assert extract_symbols(PY, "def f(a, b):\n    pass\n") == {"f/2"}
    assert extract_symbols(PY, "def f(a):\n    pass\n") == {"f/1"}
    assert extract_symbols(PY, "class C:\n    def m(self):\n        pass\n") == {"C"}
    # 파싱이 안 되면 판정을 포기합니다 — 검증 장치가 수정을 막아서는 안 됩니다.
    assert extract_symbols(PY, "def f(:\n") is None


def test_G9_무관한_심볼을_지우는_수정안은_거부된다(workdir: Path, memory):
    """수정안은 파일 전체를 재생성합니다.

    고치라고 하지 않은 함수를 LLM 이 조용히 지워도, 테스트가 그 부분을 덮지 않으면
    그대로 통과해 버립니다. **프롬프트가 아니라 구조로** 막혀야 합니다.
    """
    source = workdir / "boundary.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + "\n\ndef unrelated_helper(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    # FIXED_SOURCE 에는 unrelated_helper 가 없습니다. 즉 지워 버리는 수정안입니다.
    llm = ScriptedLLM(FIXED_SOURCE)
    code = heal.main(
        [str(workdir), "--max-attempts", "1", "--apply"], llm=llm, memory=memory
    )

    assert code == EXIT_NOT_FIXED, "거부된 수정안이 성공으로 처리되었습니다."
    assert "unrelated_helper" in source.read_text(encoding="utf-8")


# ── 줄바꿈 보존 ─────────────────────────────────────────────────
def test_CRLF_원본의_줄바꿈이_유지된다(workdir: Path, memory):
    """수정안은 파일 **전체**를 재생성합니다.

    줄바꿈을 되돌려 쓰지 않으면 CRLF 파일은 모든 줄이 바뀐 것으로 diff 에 나와,
    "검증된 diff 를 사람이 검수한다" 는 승인 흐름(G6)이 무력화됩니다.
    """
    source = workdir / "boundary.py"
    source.write_bytes(source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

    llm = ScriptedLLM(FIXED_SOURCE)
    code = heal.main([str(workdir), "--apply"], llm=llm, memory=memory)

    after = source.read_bytes()
    assert code == EXIT_FIXED
    assert b"\r\n" in after, "CRLF 가 LF 로 정규화되었습니다."
    assert after.replace(b"\r\n", b"").count(b"\n") == 0, "LF 가 섞여 들어갔습니다."


# ── exit code 규약 ──────────────────────────────────────────────
def test_고칠게_없으면_exit_2(workdir: Path, memory):
    """'버그가 없어서 안 고친 것'(2)과 '못 고친 것'(1)은 다른 결과입니다."""
    (workdir / "boundary.py").write_text(FIXED_SOURCE, encoding="utf-8")
    llm = ScriptedLLM("irrelevant")
    code = heal.main([str(workdir)], llm=llm, memory=memory)
    assert code == EXIT_NOTHING_TO_FIX
    assert llm.calls == 0, "고칠 게 없으면 LLM 을 부를 이유가 없습니다."
