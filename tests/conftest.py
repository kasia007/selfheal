"""테스트 공용 장치 — **실호출 없이** 전 구간을 돌리기 위한 것입니다.

두 가지를 제공합니다.

1. **가짜 메모리** — 예전에는 ``--no-memory`` 로 메모리를 꺼서 오프라인을 보장했습니다.
   그 옵션을 없앤 뒤에는 ``src/retriever.py`` 의 ``HashEmbeddings`` 를 꽂은 저장소를
   주입해 같은 목적을 달성합니다. Chroma 자체는 로컬에서 동작하므로 임베딩만 대체하면
   네트워크가 필요 없습니다.

2. **가짜 채팅 모델** — ``ScriptedChatModel``. 구조화 출력을 LCEL 체인
   (``ChatPromptTemplate | llm | PydanticOutputParser``) 으로 구현했으므로, 가짜 모델도
   체인에 들어갈 수 있는 **Runnable** 이어야 합니다. 그래서 LangChain 이 공식적으로
   제공하는 확장 지점인 ``BaseChatModel`` 을 상속해 ``_generate`` 를 구현합니다.
   (평범한 ``.invoke()`` 객체는 ``|`` 연산자에 쓸 수 없습니다.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

SELFHEAL_ROOT = Path(__file__).resolve().parents[1]
if str(SELFHEAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SELFHEAL_ROOT))

from src.retriever import HashEmbeddings, MemoryStore  # noqa: E402

# 구조화 출력 스키마별 기본 응답입니다. 프롬프트에 실린 format_instructions 의
# 스키마 이름을 보고 고릅니다.
PATTERN_JSON = json.dumps(
    {
        "pattern": "경계 검사 누락",
        "symptom": "IndexError: list index out of range",
        "cause_and_fix": "범위를 확인한 뒤 기본값을 반환한다",
    },
    ensure_ascii=False,
)
REPORT_JSON = json.dumps(
    {
        "symptom": "범위를 벗어난 인덱스 접근으로 테스트가 실패합니다",
        "root_cause": "인덱스가 리스트 길이 안에 있는지 검사하지 않습니다",
        "fix_strategy": "범위를 먼저 확인하고 벗어나면 기본값을 반환합니다",
    },
    ensure_ascii=False,
)


class ScriptedChatModel(BaseChatModel):
    """프롬프트 종류만 보고 정해진 답을 돌려주는 가짜 모델입니다.

    ``source`` 는 수정안 요청(파일 전체 내용)에 돌려줄 소스입니다.
    ``calls`` 로 호출 횟수를 세어 "전제 불충족이면 LLM 을 부르지 않는다"(G7) 를 검증합니다.

    ``break_structured=True`` 면 구조화 요청에 깨진 JSON 을 돌려줍니다 — 파싱 실패 시
    텍스트 경로로 폴백하는지 확인하는 용도입니다.
    """

    source: str = ""
    calls: int = 0
    break_structured: bool = False
    target_path: str = ""
    """``TargetChoice`` 응답에 넣을 경로. 비어 있으면 첫 후보를 알 수 없으므로 그대로 둡니다."""

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        prompt = str(messages[-1].content)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self._answer(prompt)))]
        )

    def _answer(self, prompt: str) -> str:
        """프롬프트를 보고 무엇을 물었는지 판별합니다.

        구조화 요청에는 `PydanticOutputParser` 가 만든 JSON 스키마가 프롬프트에 실려
        오므로, 그 안의 필드 이름으로 어느 스키마인지 알 수 있습니다.
        """
        # 수정안 요청 — 구조화하지 않는 유일한 호출입니다(소스를 그대로 돌려줍니다).
        if "수정된 파일의 **전체 내용**" in prompt:
            return self.source

        if '"cause_and_fix"' in prompt:      # BugPattern
            return "{깨진 JSON" if self.break_structured else PATTERN_JSON
        if '"root_cause"' in prompt:          # BugReport
            return "{깨진 JSON" if self.break_structured else REPORT_JSON
        if '"path"' in prompt:                # TargetChoice
            if self.break_structured:
                return "{깨진 JSON"
            return json.dumps(
                {"path": self.target_path, "reason": "테스트 출력이 이 파일을 가리킵니다"},
                ensure_ascii=False,
            )

        # 구조화가 아닌 텍스트 요청(폴백 경로) — 예전 형식 그대로입니다.
        return "# 경계 검사 누락 ## IndexError ### 범위 확인 후 기본값 반환"


class ThrottledChatModel(BaseChatModel):
    """호출하면 언제나 실패하는 가짜 모델입니다.

    Bedrock 일일 토큰 한도(``ThrottlingException: Too many tokens per day``)를 흉내내
    축약 경로를 검증합니다. 실제로 그 상황을 재현하려면 한도를 소진해야 하므로,
    가드레일 검증과 같은 이유로 가짜 모델이 필요합니다.
    """

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "throttled-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        raise RuntimeError("ThrottlingException: Too many tokens per day")


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch: pytest.MonkeyPatch):
    """**테스트는 절대 외부로 트레이스를 보내지 않습니다.**

    ``heal.main()`` 은 실행 시 ``.env`` 를 읽습니다. 개발자의 ``.env`` 에
    ``LANGSMITH_TRACING=true`` 와 실제 API 키가 들어 있으면, 테스트를 돌리는 것만으로
    프롬프트(=검수 대상 소스 코드)가 외부 서비스로 전송됩니다. 테스트가 사용자 데이터를
    내보내는 일은 없어야 하므로 여기서 원천 차단합니다.

    ``configure_tracing`` 이 키와 요청을 **둘 다** 보므로, 요청 플래그만 지워도 충분하지만
    키까지 함께 지워 이중으로 막습니다.
    """
    for name in (
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    # .env 에서 다시 올라오는 것도 막습니다 (load_dotenv 는 기존 값을 덮지 않습니다).
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    """테스트마다 비어 있는 임시 메모리를 줍니다. 회차 간 오염을 막습니다."""
    return MemoryStore(tmp_path / "chroma_db", embeddings=HashEmbeddings())
