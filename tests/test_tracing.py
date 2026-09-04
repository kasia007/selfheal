"""관측(LangSmith) 설정 검증 — 체크리스트 #11.

**네트워크로 아무것도 보내지 않습니다.** ``configure_tracing`` 이 환경변수를 보고
켤지 말지 판단하는 로직만 확인합니다. 그래서 자격증명이나 실제 LangSmith 계정 없이도
"연동이 코드에 들어 있다" 를 검증할 수 있습니다.

특히 중요한 케이스는 **요청은 있고 키는 없는 경우**입니다. 그 상태로 추적을 켜면
``Failed to send compressed multipart ingest: ... 401 Unauthorized`` 덤프가 화면을
뒤덮으므로, 반드시 켜지지 않아야 합니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SELFHEAL_ROOT = Path(__file__).resolve().parents[1]
if str(SELFHEAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SELFHEAL_ROOT))

from src.report import Console  # noqa: E402
from src.tracing import DEFAULT_PROJECT, configure_tracing  # noqa: E402

TRACE_VARS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_PROJECT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """실제 환경의 LangSmith 설정이 테스트에 새어 들어오지 않게 합니다."""
    for name in TRACE_VARS:
        monkeypatch.delenv(name, raising=False)


def test_환경변수가_없으면_켜지지_않는다():
    assert configure_tracing(Console(quiet=True)) is False


def test_키만_있으면_켜지지_않는다(monkeypatch: pytest.MonkeyPatch):
    """명시적 옵트인이 없으면 사용자가 원한 것이 아닙니다."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-fake-키")
    assert configure_tracing(Console(quiet=True)) is False


def test_요청만_있고_키가_없으면_켜지지_않는다(monkeypatch: pytest.MonkeyPatch):
    """**401 오류 덤프가 화면을 뒤덮는 것을 막는 가장 중요한 케이스입니다.**"""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert configure_tracing(Console(quiet=True)) is False
    # 추적이 켜진 상태로 남아 있으면 이후 체인 호출에서 덤프가 나옵니다.
    import os

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_둘_다_있으면_켜지고_프로젝트가_설정된다(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-fake-키")

    assert configure_tracing(Console(quiet=True)) is True

    import os

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == DEFAULT_PROJECT


def test_프로젝트_이름을_지정할_수_있다(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-fake-키")
    monkeypatch.setenv("LANGSMITH_PROJECT", "내-프로젝트")

    assert configure_tracing(Console(quiet=True)) is True

    import os

    assert os.environ["LANGSMITH_PROJECT"] == "내-프로젝트"


def test_켜지면_사용자에게_알린다(monkeypatch: pytest.MonkeyPatch):
    """코드가 외부로 나가는 실행임을 모르고 지나칠 수 없어야 합니다."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-fake-키")

    console = Console(quiet=True)
    configure_tracing(console)
    # quiet 모드여도 trace 버퍼에는 남아 .heal/trace.log 로 나갑니다.
    logged = "\n".join(console.trace)
    assert "LangSmith 추적 활성" in logged
    assert "외부로 전송" in logged, "코드가 외부로 나간다는 사실이 드러나야 합니다."


# ── CLI 플래그 (--trace / --no-trace) ─────────────────────────
# .env 는 한 번 적으면 잊히는 설정인데, 이 에이전트는 git 훅으로 커밋마다 자동 실행될 수
# 있습니다. 잊힌 설정 하나로 매 커밋 소스가 외부로 나가지 않게 하는 것이 --no-trace 이고,
# .env 를 건드리지 않고 한 번만 켜 보는 것이 --trace 입니다.


def test_no_trace_는_env_가_켜_있어도_끈다(monkeypatch: pytest.MonkeyPatch):
    """훅처럼 자동 실행되는 경로에서 쓰는 안전장치입니다."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-fake-키")

    assert configure_tracing(Console(quiet=True), override=False) is False

    import os

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_trace_는_env_가_없어도_켠다(monkeypatch: pytest.MonkeyPatch):
    """키는 있어야 합니다. 켜는 스위치만 플래그로 대신합니다."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-fake-키")

    assert configure_tracing(Console(quiet=True), override=True) is True


def test_trace_를_주어도_키가_없으면_켜지지_않는다():
    """플래그가 401 덤프 방지 규칙을 뚫지는 못합니다."""
    assert configure_tracing(Console(quiet=True), override=True) is False


def test_플래그가_없으면_env_를_따른다(monkeypatch: pytest.MonkeyPatch):
    """기본값은 여전히 .env 입니다. override=None 이 '플래그 없음' 입니다."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-fake-키")

    assert configure_tracing(Console(quiet=True), override=None) is True


def test_CLI_가_플래그를_그대로_전달한다():
    """--trace / --no-trace / 미지정 세 상태가 argparse 에서 구분되어야 합니다."""
    from src.agent import parse_args

    assert parse_args(["."]).trace is None
    assert parse_args([".", "--trace"]).trace is True
    assert parse_args([".", "--no-trace"]).trace is False
