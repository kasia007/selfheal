"""축약 경로 검증 — 모델을 쓸 수 없을 때(17단계).

Bedrock 일일 토큰 한도에 걸리면 예전에는 **raw traceback 으로 죽고 산출물도 남지
않았습니다.** 관측(#11)을 구현한 직후인데 정작 실패하면 아무 근거가 없는 모순이라
축약 경로를 만들었습니다.

핵심은 **메모리 검색은 살아 있을 수 있다**는 점입니다 — 검색은 ``ChatBedrock``(Sonnet)이
아니라 Titan 임베딩을 쓰므로 별도 한도를 갖습니다. 그래서 고치지는 못하더라도 "과거에 이
계열 버그를 어떻게 고쳤는지" 는 찾아서 단서로 남길 수 있습니다.
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
from src.report import EXIT_LLM_UNAVAILABLE  # noqa: E402

from conftest import ScriptedChatModel, ThrottledChatModel  # noqa: E402

SAMPLES = SELFHEAL_ROOT / "data" / "samples"

FIXED_SOURCE = (
    "def double_at(items, index):\n"
    "    if items is None:\n"
    "        return 0\n"
    "    if not isinstance(index, int) or index < 0 or index >= len(items):\n"
    "        return 0\n"
    "    return items[index] * 2\n"
)


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    dest = tmp_path / "py-index"
    shutil.copytree(SAMPLES / "py-index", dest)
    return dest


def _report(workdir: Path) -> dict:
    return json.loads((workdir / ".heal" / "report.json").read_text(encoding="utf-8"))


def test_모델을_못_쓰면_exit_5로_끝난다(workdir: Path, memory):
    """'못 고침(1)' 과 구분되어야 합니다.

    이건 코드 결함이 아니라 인프라 제약이라 재실행하면 고쳐질 수 있습니다. 그래서 훅에서
    푸시를 막아서도 안 되고, exit code 로 구분되어야 합니다.
    """
    code = heal.main([str(workdir), "--quiet"], llm=ThrottledChatModel(), memory=memory)
    assert code == EXIT_LLM_UNAVAILABLE


def test_크래시하지_않고_산출물을_남긴다(workdir: Path, memory):
    """예전에는 traceback 만 남기고 죽어 `.heal/` 에 아무것도 쓰이지 않았습니다."""
    heal.main([str(workdir), "--quiet"], llm=ThrottledChatModel(), memory=memory)

    report = _report(workdir)
    assert report["status"] == "llm_unavailable"
    assert "ThrottlingException" in report["llm_error"], "사유가 리포트에 남아야 합니다."
    # 규칙 기반으로 알아낸 것은 남아 있어야 합니다.
    assert report["language"] == "python"
    assert report["target"].endswith("boundary.py")


def test_원본은_건드리지_않는다(workdir: Path, memory):
    """--apply 를 붙여도 마찬가지입니다. 수정 자체를 시도하지 않기 때문입니다."""
    before = (workdir / "boundary.py").read_text(encoding="utf-8")
    heal.main(
        [str(workdir), "--apply", "--quiet"], llm=ThrottledChatModel(), memory=memory
    )
    assert (workdir / "boundary.py").read_text(encoding="utf-8") == before


def test_실패_후_LLM을_다시_부르지_않는다(workdir: Path, memory):
    """어차피 실패할 호출을 반복하면 한도 초과 상황에서 대기 시간만 늘어납니다.

    ``ChatBedrock`` 이 이미 4회 재시도한 뒤 던지는 예외라, 그 위에 우리 재시도를 얹지
    않습니다. 구조화 파싱 실패와 달리 텍스트 폴백도 시도하지 않습니다.
    """
    llm = ThrottledChatModel()
    heal.main([str(workdir), "--quiet"], llm=llm, memory=memory)
    assert llm.calls == 1, f"LLM 을 {llm.calls}회 호출했습니다. 첫 실패로 끝나야 합니다."


def test_과거_사례를_찾아_참고로_제시한다(workdir: Path, tmp_path: Path, memory):
    """모델이 죽어도 검색은 Titan 임베딩만 쓰므로 살아 있을 수 있습니다."""
    # 먼저 정상 실행으로 패턴을 하나 쌓습니다.
    warmup = tmp_path / "warmup"
    shutil.copytree(SAMPLES / "py-index", warmup)
    heal.main(
        [str(warmup), "--quiet"],
        llm=ScriptedChatModel(source=FIXED_SOURCE),
        memory=memory,
    )
    assert memory.stats(), "warmup 이 패턴을 쌓지 못했습니다."

    # 이제 모델이 죽은 상태로 같은 계열 버그를 만납니다.
    code = heal.main([str(workdir), "--quiet"], llm=ThrottledChatModel(), memory=memory)

    assert code == EXIT_LLM_UNAVAILABLE
    report = _report(workdir)
    assert report["memory_hits"], "참고 사례를 찾아 리포트에 남겨야 합니다."
    # 축약 질의였다는 사실이 드러나야 합니다 — 정확도가 평소보다 낮습니다.
    assert report["memory_degraded_query"] is True


def test_메모리에_쓰레기를_저장하지_않는다(workdir: Path, memory):
    """요약을 만들 수 없으니 저장하면 이후 검색 품질만 망칩니다."""
    heal.main([str(workdir), "--quiet"], llm=ThrottledChatModel(), memory=memory)
    assert memory.stats() == [], "축약 경로에서는 저장하지 않아야 합니다."


def test_임베딩까지_실패해도_정상_종료한다(workdir: Path, memory, monkeypatch):
    """메모리는 있으면 좋은 것이지 필수가 아닙니다."""

    def boom(*args, **kwargs):
        raise RuntimeError("ThrottlingException: embeddings")

    monkeypatch.setattr(memory, "search", boom)
    # search 가 예외를 던지므로, 이것까지 잡히는지 봅니다.
    code = heal.main([str(workdir), "--quiet"], llm=ThrottledChatModel(), memory=memory)

    assert code == EXIT_LLM_UNAVAILABLE
    assert _report(workdir)["memory_hits"] == []
