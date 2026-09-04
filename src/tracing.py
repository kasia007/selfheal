"""LangSmith 추적 설정 — **기본은 꺼져 있습니다.**

15단계에서 LLM 호출을 LCEL 체인으로 바꿔 둔 덕에, 추적은 별도 계측 코드 없이 `Runnable`
단위로 자동 수집됩니다. 이 모듈이 하는 일은 "켤지 말지" 를 판단하는 것뿐입니다.

**왜 옵트인인가**

추적을 켜면 프롬프트가 외부 서비스(LangSmith)로 전송됩니다. 그 프롬프트에는 **사용자의
소스 코드 전문**이 들어 있습니다. 이 프로젝트의 핵심 약속이 "승인 전까지 원본을 건드리지
않는다"(G6)인데, 코드를 조용히 외부로 보내는 것은 그 약속의 정신에 어긋납니다. 그래서
사용자가 두 값을 **모두** 설정해야 켜지고, 켜지면 화면에 알립니다.

**왜 CLI 플래그로 덮어쓸 수 있는가**

``.env`` 는 한 번 적어 두면 잊히는 설정입니다. 그런데 이 에이전트는 git 훅으로 걸어
**커밋마다 자동 실행**될 수 있습니다(``install-hooks.sh``). 그 경로에서 잊힌 설정 하나 때문에
매 커밋 사용자 코드가 외부로 나가면 안 됩니다. 그래서 ``--no-trace`` 로 그 실행에서만 끌 수
있게 하고, 반대로 ``--trace`` 로 ``.env`` 를 건드리지 않고 한 번만 켤 수도 있게 했습니다.
기본값은 여전히 ``.env`` 입니다.

**왜 키가 없으면 아예 켜지 않는가**

키 없이 ``LANGSMITH_TRACING=true`` 만 켜고 체인을 돌려 보면, 호출 자체는 정상 동작하지만
``Failed to send compressed multipart ingest: ... 401 Unauthorized`` 오류 덤프가 트레이스
ID 수십 개와 함께 stderr 로 쏟아집니다. 이 프로젝트의 화면 출력은 "에이전트가 무엇을
근거로 판단했는지 보이게 하는" 산출물인데, 그 위에 덮이면 읽을 수 없게 됩니다.
그래서 키가 없으면 환경변수를 **대신 설정해 주지 않고** 조용히 통과합니다.
"""

from __future__ import annotations

import os

DEFAULT_PROJECT = "selfheal"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def tracing_requested() -> bool:
    """사용자가 추적을 요청했는가. (키 유무는 보지 않습니다)"""
    return _truthy(os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2"))


def api_key_present() -> bool:
    return bool(
        (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY") or "").strip()
    )


def configure_tracing(console=None, *, override: bool | None = None) -> bool:
    """추적을 켤 수 있으면 켜고, 활성 여부를 돌려줍니다.

    ``LANGSMITH_TRACING`` 과 ``LANGSMITH_API_KEY`` 가 **둘 다** 있어야 켜집니다.
    하나만 있으면 켜지 않습니다 — 요청만 있고 키가 없으면 401 덤프가 쏟아지고,
    키만 있고 요청이 없으면 사용자가 원한 것이 아니기 때문입니다.

    ``override`` 는 CLI 의 ``--trace`` / ``--no-trace`` 입니다. ``None`` 이면 플래그가
    없었다는 뜻이라 ``.env`` 를 따릅니다. **키 검사는 덮어쓰지 않습니다** — ``--trace`` 를
    주었어도 키가 없으면 켜지 않습니다. 그렇게 하지 않으면 401 덤프가 화면을 덮습니다.
    """
    requested = tracing_requested() if override is None else override
    if not (requested and api_key_present()):
        # 요청이 있었는데 키가 없는 경우에만, 왜 안 켜졌는지 알려 줍니다.
        if requested and console is not None:
            console.detail(
                "LANGSMITH_API_KEY 가 없어 추적을 켜지 않았습니다. (.heal/trace.log 는 그대로 남습니다)"
            )
        # 켜지 않는 쪽으로 명시적으로 고정합니다. 다른 곳에서 켜 두었을 수 있습니다.
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        return False

    project = os.environ.get("LANGSMITH_PROJECT") or DEFAULT_PROJECT
    os.environ["LANGSMITH_PROJECT"] = project
    os.environ["LANGSMITH_TRACING"] = "true"
    # 레거시 변수도 함께 맞춥니다. --trace 로 켰는데 이 값이 "false" 로 남아 있으면
    # langchain 이 그쪽을 보고 끌 수 있습니다.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"

    _install_redaction()

    if console is not None:
        # 코드가 외부로 나가는 실행임을 사용자가 모르고 지나칠 수 없게 합니다.
        console.step(
            "🔭",
            f"LangSmith 추적 활성 — 프롬프트가 외부로 전송됩니다 (프로젝트: {project}, 비밀값 마스킹 적용)",
        )
    return True


def _install_redaction() -> None:
    """LangSmith 로 **업로드되는** 입출력에서 비밀값을 지웁니다 (체크리스트 #6).

    모델이 실제로 받는 프롬프트는 그대로입니다 — Bedrock 은 사용자 자신의 AWS 계정에서
    처리되고 저장되지 않으므로, 여기서 치환하면(``mask_secrets`` 가 파일 재생성 응답을
    바꿔 버릴 위험, ``src/guardrails.py`` 참고) 얻는 것 없이 위험만 생깁니다. 반면
    LangSmith 는 **제3자 SaaS 에 영구 저장**되고 조직 구성원이 열람하므로, 거기로 나가는
    사본만 가립니다.

    ``langsmith.Client`` 는 정확히 이 용도의 ``hide_inputs``/``hide_outputs`` 콜백을
    제공합니다. 다만 클라이언트는 프로세스당 **한 번만 생성되는 전역 싱글턴**
    (``langsmith.run_trees.get_cached_client``)이라, 다른 코드가 먼저 만들어 버리면 여기서
    다시 만들어도 반영되지 않습니다. 추적을 켜는 이 함수가 그 시점보다 앞서므로 안전합니다.
    """
    try:
        from langsmith import run_trees
    except ImportError:
        return  # 없으면 마스킹할 대상도 없습니다.

    if run_trees._CLIENT is not None:
        # 이미 다른 코드가 기본 클라이언트를 만들어 버렸습니다. 지금 다시 만들어도
        # 이미 참조를 가진 트레이서에는 반영되지 않으므로, 조용히 포기합니다 — 마스킹이
        # 안 되는 것이 실행을 막을 이유는 아닙니다.
        return

    run_trees.get_cached_client(hide_inputs=_redact, hide_outputs=_redact)


def _redact(payload: dict) -> dict:
    """LangSmith 런의 입력/출력 딕셔너리를 재귀적으로 마스킹합니다."""
    from .guardrails import mask_secrets

    def walk(value):
        if isinstance(value, str):
            masked, _ = mask_secrets(value)
            return masked
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(payload) if isinstance(payload, dict) else payload
