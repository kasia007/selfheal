"""LLM 이 돌려줄 구조화 출력 스키마입니다.

예전에는 다섯 곳의 LLM 호출이 전부 **순수 텍스트**를 받아 문자열로 파싱했습니다.
그래서 두 가지가 취약했습니다.

1. **대상 특정** — 모델 응답에서 후보 경로를 *부분 문자열 대조* 로 찾았습니다.
   모델이 경로에 설명을 덧붙이거나 살짝 다르게 쓰면 대상 특정에 실패했습니다.
2. **메모리 형식** — ``# 패턴명 ## 증상 ### 원인과 해결`` 을 프롬프트로 *부탁* 할 뿐
   검증하지 않았습니다. 모델이 형식을 어기면 저장 문서가 깨지고 임베딩 정렬이 무너집니다.

여기 정의한 모델을 ``ChatPromptTemplate | llm | PydanticOutputParser`` 체인의 출력으로
쓰면, 형식이 **파싱 시점에 검증**됩니다.

``Field(description=...)`` 은 장식이 아닙니다. ``PydanticOutputParser`` 가 이 설명으로
``format_instructions`` 를 만들어 프롬프트에 넣으므로, **지시문을 스키마 한 곳에서
관리하는 것**이 됩니다.

파일 전체 소스를 받는 ``code_update_node`` 는 여기 없습니다. 소스를 JSON 문자열 필드에
담으면 줄바꿈·따옴표·백슬래시가 모두 이스케이프 대상이 되어 파싱이 깨지기 쉽고 토큰도
늘어납니다. 그쪽의 구조 검증은 G9(심볼 비교)가 코드 수준에서 합니다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TargetChoice(BaseModel):
    """규칙 기반 탐색이 실패했을 때, 어느 파일을 고칠지 고른 결과입니다."""

    path: str = Field(
        description="고쳐야 할 파일 경로. 반드시 후보 목록에 있는 경로를 그대로 적으십시오."
    )
    reason: str = Field(
        description="그 파일을 고른 근거를 한 문장으로. 테스트 출력의 어느 단서를 보았는지 밝히십시오."
    )


class BugReport(BaseModel):
    """무엇이 왜 터졌는지 정리한 결과입니다.

    메모리 검색의 질의어 원재료이자 저장 재료이므로, **코드를 담지 않습니다.**
    코드가 섞이면 저장되는 패턴이 특정 파일에 종속돼 재사용성이 떨어집니다.
    """

    symptom: str = Field(description="테스트가 무엇을 보고 실패했는가. 관측된 현상만 적으십시오.")
    root_cause: str = Field(description="왜 그렇게 됐는가. 코드의 어떤 판단이 빠졌거나 잘못되었는지.")
    fix_strategy: str = Field(
        description="어떻게 고쳐야 하는가. 방향만 적고 실제 코드는 쓰지 마십시오."
    )

    def render(self) -> str:
        """이후 단계(메모리 검색·수정 프롬프트)에 넘길 문단으로 만듭니다."""
        return f"{self.symptom} 원인: {self.root_cause} 해결 방향: {self.fix_strategy}"


class BugPattern(BaseModel):
    """메모리에 저장되는 버그 패턴 한 건입니다.

    Chroma 에 저장되고 임베딩되는 것은 **문자열**이어야 하므로, 이 모델은 파싱·검증
    계층으로만 쓰고 저장 직전에 ``render()`` 로 기존 형식으로 되돌립니다. 그래서 이미
    쌓인 ``chroma_db`` 와 호환되고, 저장과 질의가 같은 렌더러를 통과하므로 임베딩 정렬도
    보장됩니다.
    """

    pattern: str = Field(
        description="패턴 이름. 특정 파일·변수명에 매이지 않는 일반적인 표현으로 짧게 (예: 경계 검사 누락)."
    )
    symptom: str = Field(description="이 패턴이 겉으로 드러나는 증상 (예: IndexError).")
    cause_and_fix: str = Field(description="원인과 해결 방법을 한 문장으로.")

    def render(self) -> str:
        """저장·검색에 쓰는 한 줄 형식입니다. ``MEMORY_FORMAT`` 과 같은 모양입니다."""
        return f"# {self.pattern} ## {self.symptom} ### {self.cause_and_fix}"
