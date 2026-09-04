"""배선 검증 — **실제 LLM 을 호출하지 않습니다.**

AGENTS.md 규약대로 가짜 모델을 꽂아, API 키나 Bedrock 자격증명 없이도
그래프가 끝까지 도는지 확인합니다. 확인하는 것은 세 가지입니다.

1. 언어 감지 · preflight · 테스트 존재 확인이 맞게 동작하는가
2. 테스트 실패 출력에서 수정 대상 파일을 스스로 찾아내는가
3. 수정 → 재테스트 → 통과 루프가 실제로 닫히는가

메모리는 켜 둔 채로 돌리되, **가짜 임베딩**(conftest.py) 을 꽂아 실호출을 피합니다.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

SELFHEAL_ROOT = Path(__file__).resolve().parents[1]
if str(SELFHEAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SELFHEAL_ROOT))

from src.agent import HealingNodes, build_graph, commit_pending_memory_writes  # noqa: E402
from src.report import Console  # noqa: E402
from src.state import State  # noqa: E402
from src.tools import (  # noqa: E402
    ADAPTERS,
    detect_language,
    has_tests,
    link_or_copy_dir,
    locate_target,
)

from conftest import ScriptedChatModel  # noqa: E402

SAMPLES = SELFHEAL_ROOT / "data" / "samples"

# 가짜 모델이 돌려줄 '정답' 소스입니다. 실제로는 LLM 이 만들어 냅니다.
FIXED_BOUNDARY = '''"""교육용 더미 데이터입니다."""


def double_at(items, index):
    """리스트의 index 번째 값을 두 배로 돌려줍니다."""
    if items is None:
        return 0
    if not isinstance(index, int) or index < 0 or index >= len(items):
        return 0
    return items[index] * 2
'''


# 가짜 모델은 conftest.py 의 ScriptedChatModel 을 씁니다.
def FakeLLM(fixed_source: str) -> ScriptedChatModel:
    return ScriptedChatModel(source=fixed_source)


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """샘플을 임시 디렉터리로 복사합니다. 원본 샘플을 더럽히지 않기 위해서입니다."""
    dest = tmp_path / "py-index"
    shutil.copytree(SAMPLES / "py-index", dest)
    return dest


def test_언어를_마커파일로_감지한다(workdir: Path):
    assert detect_language(workdir).name == "python"


def test_테스트_파일_존재를_확인한다(workdir: Path):
    assert has_tests(ADAPTERS["python"], workdir) is True


def test_테스트_파일은_수정_대상에서_제외된다():
    """열어 주면 LLM 이 테스트를 고쳐서 통과시키므로 반드시 막혀 있어야 합니다."""
    adapter = ADAPTERS["python"]
    assert adapter.is_source_file(Path("boundary.py")) is True
    assert adapter.is_source_file(Path("test_boundary.py")) is False


def test_실패출력에서_수정대상을_스스로_찾는다(workdir: Path):
    """사용자는 디렉터리만 줍니다. 파일은 트레이스백에서 뽑아야 합니다."""
    output = (
        f'  File "{workdir / "test_boundary.py"}", line 20, in test_x\n'
        f'  File "{workdir / "boundary.py"}", line 11, in double_at\n'
        "IndexError: list index out of range\n"
    )
    path, line_no = locate_target(ADAPTERS["python"], output, workdir)
    assert path is not None and path.name == "boundary.py"
    assert line_no == 11


def test_link_or_copy_dir로_원본을_재사용하고_사본을_지워도_원본은_남는다(tmp_path: Path):
    """node_modules 처럼 큰 디렉터리를 매번 복사하지 않고 링크로 재사용할 때, 샌드박스
    청소(``shutil.rmtree``) 가 원본까지 지워 버리면 "원본 불변" 약속이 깨집니다."""
    original = tmp_path / "node_modules"
    original.mkdir()
    (original / "package_marker.txt").write_text("keep me", encoding="utf-8")

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    link_target = sandbox / "node_modules"

    how = link_or_copy_dir(original, link_target)
    assert how in ("junction", "symlink", "copy")
    assert (link_target / "package_marker.txt").read_text(encoding="utf-8") == "keep me"

    shutil.rmtree(sandbox)

    assert original.exists(), "샌드박스를 지웠는데 원본까지 사라지면 안 됩니다."
    assert (original / "package_marker.txt").read_text(encoding="utf-8") == "keep me"


def test_is_build_failure는_실패구간_밖의_컴파일에러에_속지_않는다():
    """여러 테스트 파일을 한 번에 돌리면 output 에 무관한 실패까지 섞여 들어온다.

    이번 대상과 상관없는 다른 파일의 SyntaxError 때문에, 지금 다루는 실패가
    (실은 그냥 assertion 실패인데도) '컴파일 에러' 로 잘못 분류되면 안 된다.
    """
    adapter = ADAPTERS["javascript"]
    output = (
        "무관한 다른 파일에서 이미 난 실패:\n"
        "SyntaxError: The requested module does not provide an export named 'x'\n"
        "\n"
        "✖ failing tests:\n"
        "\n"
        "test at test/real.test.js:1:1\n"
        "✖ GET /api/x returns 200 (10ms)\n"
        "  AssertionError: 404 !== 200\n"
    )
    assert adapter.is_build_failure(output) is False


def test_is_build_failure는_실패구간_안의_컴파일에러는_잡아낸다():
    adapter = ADAPTERS["javascript"]
    output = (
        "✖ failing tests:\n"
        "\n"
        "test at test/real.test.js:1:1\n"
        "✖ real.test.js (10ms)\n"
        "  SyntaxError: Unexpected token\n"
    )
    assert adapter.is_build_failure(output) is True


def test_그래프가_수정_루프를_닫는다(workdir: Path, memory):
    """실호출 없이 전 구간을 돕니다: 실패 → 대상 추론 → 수정 → 재테스트 → 통과."""
    adapter = ADAPTERS["python"]
    llm = FakeLLM(FIXED_BOUNDARY)
    nodes = HealingNodes(
        adapter=adapter,
        memory=memory,
        console=Console(quiet=True),
        llm=llm,
    )
    graph = build_graph(nodes)

    result = graph.invoke(
        State(
            workdir=str(workdir),
            language="python",
            test_cmd=list(adapter.test_cmd),
            max_attempts=2,
        ),
        {"recursion_limit": 50},
    )
    state = State.model_validate(result)

    assert state.status == "fixed"
    assert state.attempts == 1, "정답을 주면 1회에 끝나야 합니다. (메모리는 비어 있음)"
    assert Path(state.target_file).name == "boundary.py"
    # 테스트 파일은 그대로여야 합니다.
    assert "assert double_at([1, 2, 3], 5) == 0" in (workdir / "test_boundary.py").read_text(
        encoding="utf-8"
    )


def test_구조화_출력으로_수정대상을_고른다(workdir: Path, memory):
    """규칙 기반 탐색이 실패했을 때의 LLM 폴백입니다.

    예전에는 모델 응답에서 후보 경로를 **부분 문자열 대조**로 찾았습니다. 그래서 모델이
    경로만 정확히 말해도 후보에 없는 파일을 지어내면 걸러지지 않거나, 반대로 설명을
    덧붙이면 매칭에 실패했습니다. 지금은 `TargetChoice.path` 를 후보 목록과 대조합니다.
    """
    adapter = ADAPTERS["python"]
    # 후보가 하나뿐이면 LLM 을 부르지 않고 그것을 씁니다. 고르게 하려면 둘 이상 필요합니다.
    (workdir / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    llm = ScriptedChatModel(source=FIXED_BOUNDARY, target_path="./boundary.py")
    nodes = HealingNodes(
        adapter=adapter, memory=memory, console=Console(quiet=True), llm=llm
    )
    # 규칙 기반 정규식이 아무것도 못 찾는 출력입니다 → LLM 폴백으로 넘어갑니다.
    state = State(
        workdir=str(workdir),
        language="python",
        test_cmd=list(adapter.test_cmd),
        test_output="어떤 경로도 담기지 않은 실패 출력입니다.",
        error=True,
    )
    picked = nodes._locate_with_llm(state, workdir)

    # './' 접두어가 붙어 있어도 후보의 실제 경로로 정확히 특정해야 합니다.
    assert picked is not None
    assert picked.name == "boundary.py"


def test_후보에_없는_경로는_받아들이지_않는다(workdir: Path, memory):
    """모델이 존재하지 않는 파일을 지어내면 구조가 걸러야 합니다."""
    adapter = ADAPTERS["python"]
    (workdir / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    llm = ScriptedChatModel(source=FIXED_BOUNDARY, target_path="does_not_exist.py")
    nodes = HealingNodes(
        adapter=adapter, memory=memory, console=Console(quiet=True), llm=llm
    )
    state = State(
        workdir=str(workdir),
        language="python",
        test_cmd=list(adapter.test_cmd),
        test_output="어떤 경로도 담기지 않은 실패 출력입니다.",
        error=True,
    )
    assert nodes._locate_with_llm(state, workdir) is None


def test_구조화_출력이_실제로_파싱된다(workdir: Path, memory):
    """LCEL 체인(prompt | llm | PydanticOutputParser) 이 Pydantic 객체를 돌려주는지 봅니다.

    구조화가 성공하면 패턴 이름이 metadata 에 저장되므로, 문서 문자열을 다시 파싱하지
    않고도 제목을 알 수 있습니다.
    """
    adapter = ADAPTERS["python"]
    nodes = HealingNodes(
        adapter=adapter, memory=memory, console=Console(quiet=True),
        llm=FakeLLM(FIXED_BOUNDARY),
    )
    result = build_graph(nodes).invoke(
        State(
            workdir=str(workdir),
            language="python",
            test_cmd=list(adapter.test_cmd),
            max_attempts=2,
        ),
        {"recursion_limit": 50},
    )
    state = State.model_validate(result)

    assert state.status == "fixed"
    # BugPattern.render() 형식이어야 합니다 — 저장과 질의가 같은 렌더러를 통과합니다.
    assert state.memory_query.startswith("# 경계 검사 누락 ## ")
    assert state.memory_pattern == "경계 검사 누락"
    # 그래프는 예약만 남깁니다 — 실제로 고쳐진 게 확인된 뒤에만 저장되므로,
    # main() 이 하는 것과 같은 커밋을 여기서도 명시적으로 해 줘야 합니다.
    commit_pending_memory_writes(memory, state, Console(quiet=True))
    rows = memory.stats()
    assert rows and rows[0]["pattern"] == "경계 검사 누락"


def test_구조화_실패하면_텍스트_경로로_폴백한다(workdir: Path, memory):
    """구조화는 품질 장치이지 관문이 아닙니다.

    모델이 깨진 JSON 을 계속 돌려줘도(재시도 1회 포함) 파이프라인은 멈추지 않고
    예전 텍스트 경로로 수정을 끝내야 합니다.
    """
    adapter = ADAPTERS["python"]
    llm = ScriptedChatModel(source=FIXED_BOUNDARY, break_structured=True)
    nodes = HealingNodes(
        adapter=adapter, memory=memory, console=Console(quiet=True), llm=llm
    )
    result = build_graph(nodes).invoke(
        State(
            workdir=str(workdir),
            language="python",
            test_cmd=list(adapter.test_cmd),
            max_attempts=2,
        ),
        {"recursion_limit": 50},
    )
    state = State.model_validate(result)

    assert state.status == "fixed", "구조화 실패가 수정을 막아서는 안 됩니다."
    # 텍스트 폴백이 낸 예전 형식 문자열입니다.
    assert state.memory_query.startswith("# 경계 검사 누락")
    # 구조화가 실패했으므로 metadata 의 패턴명은 비어 있습니다.
    assert state.memory_pattern == ""
    commit_pending_memory_writes(memory, state, Console(quiet=True))
    # 그래도 제목은 나와야 합니다 — 문서 문자열에서 `##` 앞까지를 뽑는 폴백이 동작합니다.
    assert memory.stats()[0]["pattern"] == "경계 검사 누락"


def test_고칠게_없으면_nothing_to_fix(workdir: Path, memory):
    """이미 통과하는 코드에 손대지 않아야 합니다. '못 고침' 과 구분되는 결과입니다."""
    (workdir / "boundary.py").write_text(FIXED_BOUNDARY, encoding="utf-8")
    adapter = ADAPTERS["python"]
    nodes = HealingNodes(
        adapter=adapter,
        memory=memory,
        console=Console(quiet=True),
        llm=FakeLLM(FIXED_BOUNDARY),
    )
    result = build_graph(nodes).invoke(
        State(
            workdir=str(workdir),
            language="python",
            test_cmd=list(adapter.test_cmd),
        ),
        {"recursion_limit": 50},
    )
    state = State.model_validate(result)
    assert state.status == "nothing_to_fix"
    assert state.attempts == 0


def test_재시도를_소진하면_실패로_끝난다(workdir: Path, memory):
    """무한 루프 방지. 원본 노트북에는 이 제동장치가 없었습니다."""
    adapter = ADAPTERS["python"]
    # 계속 원본과 같은(=여전히 깨진) 코드를 돌려주는 가짜 모델입니다.
    broken = (workdir / "boundary.py").read_text(encoding="utf-8")
    nodes = HealingNodes(
        adapter=adapter,
        memory=memory,
        console=Console(quiet=True),
        llm=FakeLLM(broken),
    )
    result = build_graph(nodes).invoke(
        State(
            workdir=str(workdir),
            language="python",
            test_cmd=list(adapter.test_cmd),
            max_attempts=2,
        ),
        {"recursion_limit": 60},
    )
    state = State.model_validate(result)
    assert state.error is True
    assert state.attempts == 2
