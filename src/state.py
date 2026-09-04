"""그래프가 들고 다니는 상태입니다.

원본 노트북의 ``State`` 는 ``function: Callable`` 로 **함수 객체 자체**를 담았습니다.
그래야 ``exec`` 로 만든 새 함수를 그 자리에 꽂아 넣을 수 있었기 때문입니다.
하지만 그 순간 파이썬 전용이 됩니다.

여기서는 함수 객체 대신 **파일 경로와 테스트 출력**을 담습니다.
그래서 상태만 봐도 언어에 의존하는 값이 하나도 없습니다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryHit(BaseModel):
    """벡터DB 에서 찾아온 과거 버그 패턴 한 건입니다."""

    id: str
    document: str
    """저장된 요약. 형식은 ``# 패턴명 ## 증상 ### 해결`` 입니다."""

    distance: float
    """Chroma 가 준 거리. 작을수록 유사합니다."""

    occurrences: int = 1
    """이 패턴이 지금까지 몇 번 발생했는지. metadata 에 누적해 둔 카운터입니다."""

    languages: str = ""
    """이 패턴이 관측된 언어들. 콤마 구분 문자열입니다. (Chroma metadata 는 스칼라만 됩니다)"""

    first_seen: str = ""
    last_seen: str = ""
    injected: bool = False
    """수정 프롬프트에 실제로 주입되었는지. 리포트에 그대로 나갑니다."""

    pattern: str = ""
    """구조화 출력(``BugPattern.pattern``)에서 온 패턴 이름입니다.

    metadata 에 따로 저장하므로 문서 문자열을 다시 파싱할 필요가 없습니다.
    구조화 출력 도입 이전에 쌓인 문서는 이 값이 비어 있어 ``title`` 이 폴백합니다."""

    # ── 하이브리드 검색 기여도 (체크리스트 #3) ──
    # 어느 검색기가 이 문서를 찾아냈는지 남깁니다. **하이브리드가 실제로 기여했는지를
    # 나중에 숫자로 판단**하기 위한 것입니다 — 검증되지 않은 장치를 얹지 않는다는 원칙
    # (--merge-threshold 0.3 에서 겪은 문제)에 따릅니다.
    vector_rank: int | None = None
    """벡터 검색에서의 순위. ``None`` 이면 벡터가 못 찾고 BM25 만 찾은 문서입니다."""

    bm25_rank: int | None = None
    """BM25 키워드 검색에서의 순위. ``None`` 이면 겹치는 단어가 없었다는 뜻입니다."""

    final_rank: int | None = None
    """융합·리랭킹을 거친 최종 순위입니다."""

    boost: float = 1.0
    """도메인 리랭킹 보정 계수 (발생 횟수·언어 일치·최근성)."""

    @property
    def similarity(self) -> float:
        """사람에게 보여줄 값입니다.

        "거리 0.18" 은 직관에 반하므로 화면에는 항상 유사도로 환산해서 씁니다.
        """
        return max(0.0, 1.0 - self.distance)

    @property
    def title(self) -> str:
        """패턴 이름입니다.

        구조화 출력으로 저장된 것이면 metadata 의 ``pattern`` 을 그대로 씁니다.
        그 값이 없는(구조화 도입 이전에 쌓인) 문서는 예전처럼 문서 첫 줄에서 뽑습니다 —
        이미 쌓인 ``chroma_db`` 를 그대로 쓸 수 있게 하려는 폴백입니다.
        """
        if self.pattern.strip():
            return self.pattern.strip()
        # 폴백 — ``# 패턴명 ## 증상 ### 원인과 해결`` 은 **한 줄**이므로, 첫 줄에서 ``#`` 만
        # 벗기면 문장 전체가 제목이 되어 버립니다. 다음 구분자(``##``) 앞까지만 씁니다.
        for line in self.document.splitlines():
            head = line.strip().lstrip("#").split("##")[0].strip()
            if head:
                return head
        return "(제목 없음)"


class Attempt(BaseModel):
    """수정 시도 한 번의 기록입니다. 실패했을 때 '왜 못 고쳤는지' 를 보여주는 재료입니다."""

    index: int
    kind: str
    """``build`` (컴파일 깨짐) 또는 ``test`` (테스트 실패) 또는 ``passed``."""

    summary: str
    """실패 출력에서 뽑은 한 줄 요약입니다."""


class State(BaseModel):
    """LangGraph 가 노드 사이로 넘기는 상태입니다."""

    # ── 사용자 입력에서 추론된 것 (전부 그래프 진입 전에 채워집니다) ──
    workdir: str
    language: str
    test_cmd: list[str]

    # ── 실행 결과 ──
    error: bool = False
    """마지막 테스트가 실패했는가. 원본의 error 플래그와 같은 역할입니다."""

    test_output: str = ""
    """테스트 stdout+stderr. 원본의 error_description 을 대체합니다."""

    is_build_failure: bool = False
    """빌드가 깨진 경우. 프롬프트 지시가 달라집니다."""

    # ── 수정 대상 ──
    target_file: str = ""
    target_line: int | None = None
    original_source: str = ""
    """최초 원본. 재시도가 실패로 끝나면 이 내용으로 되돌립니다."""

    current_source: str = ""
    new_source: str = ""

    newline: str = "\n"
    """원본 파일의 줄바꿈 방식(``\\n`` 또는 ``\\r\\n``).

    파이썬의 기본 텍스트 모드는 읽을 때 CRLF 를 LF 로 바꾸고 쓸 때 LF 로 씁니다.
    수정안은 파일 **전체**를 재생성하므로, 이 값을 들고 다니지 않으면 CRLF 파일은
    모든 줄이 바뀐 것으로 diff 에 나와 검수(G6)가 불가능해집니다.

    **최초 1회만 감지해야 합니다.** 재시도 2회차에는 샌드박스 파일이 이미 LF 로
    덮여 있어 그때 감지하면 늦습니다."""

    # ── 메모리 ──
    bug_report: str = ""
    memory_query: str = ""
    """저장 포맷과 **같은 템플릿으로 요약한** 검색 질의입니다.
    저장할 때와 찾을 때의 표현을 맞춰야 임베딩이 서로 맞물립니다. (원본 노트북의 좋은 아이디어)"""

    memory_queries: list[str] = Field(default_factory=list)
    """쿼리 확장 결과 — 검색에 던질 질의 목록입니다 (체크리스트 #3).

    정상 경로에서는 ``BugPattern`` 의 세 면(패턴명·증상·원인과해결)을, 축약 경로에서는
    규칙 기반 재료(예외 이름·파일명·언어)를 담습니다. 비어 있으면 ``memory_query``
    하나로 검색합니다."""

    memory_pattern: str = ""
    """``BugPattern.pattern`` — 저장 시 metadata 에 함께 넣을 패턴 이름입니다.
    구조화 출력이 실패하면 빈 문자열로 남고, 그때는 제목을 문서에서 뽑습니다."""

    memory_degraded_query: bool = False
    """질의어를 LLM 없이(원시 실패 출력으로) 만들었는가.

    저장된 문서와 표현 형태가 어긋나 임베딩 거리의 신뢰도가 낮습니다. 그래서 검색 결과를
    "참고 사례" 로만 제시하고, 화면·리포트에 축약 질의였음을 명시합니다."""

    memory_hits: list[MemoryHit] = Field(default_factory=list)
    memory_ids_to_update: list[str] = Field(default_factory=list)

    pending_memory_writes: list[dict] = Field(default_factory=list)
    """이번 시도에서 검증되면 실제로 저장/병합할 내용입니다. 시도가 실패하면 버려집니다.
    매 시도(``memory_search_node`` 진입)마다 새로 채워집니다 — 이전 시도의 진단은 이번
    시도와 다를 수 있어서, 최종 판정은 항상 **마지막 시도**의 내용을 기준으로 합니다.
    "실제로 고쳐진 것만 기억한다" 는 원칙을 지키기 위한 자리입니다 — 라우터 미등록처럼
    한 번도 안 고쳐지는 진단이 메모리에 계속 쌓이는 것을 막습니다."""

    # ── 루프 제어 ──
    attempts: int = 0
    max_attempts: int = 3
    attempt_log: list[Attempt] = Field(default_factory=list)

    guardrail_blocks: list[dict[str, Any]] = Field(default_factory=list)
    """가드레일이 수정안을 막은 기록입니다 (``{guardrail, attempt, detail}``).

    G9 처럼 "테스트는 통과하지만 받아들일 수 없는 수정안" 을 거부한 사실이 여기 남습니다.
    콘솔 한 줄로 흘려보내면 여러 실행에 걸친 패턴을 볼 수 없어 리포트에 담습니다."""

    injection_flags: list[str] = Field(default_factory=list)
    """탐지된 프롬프트 인젝션 의심 규칙 이름입니다 (체크리스트 #6, G11).

    **실행을 막지 않습니다.** 소스·테스트 출력은 저장소에서 온 신뢰할 수 없는 입력이라
    탐지만 하고 기록으로 남깁니다. 진짜 방어는 G1(테스트 파일 불변)·G6(승인 없이 미적용)·
    G9(무관한 심볼 보호)입니다."""

    # ── 최종 판정 ──
    status: str = "running"
    """``fixed`` | ``failed`` | ``nothing_to_fix`` | ``llm_unavailable`` | ``running``"""

    llm_error: str = ""
    """모델을 쓸 수 없었던 사유 (``ThrottlingException: ...`` 등).

    비어 있지 않으면 축약 경로로 끝난 실행입니다 — 규칙 기반 진단과 메모리 참고 사례만
    내고 수정은 시도하지 않았습니다. 원인 구분은 사용자에게 필요하므로 예외 종류와
    메시지를 그대로 담습니다."""

    model_id: str = ""
    """이번 실행에 쓴 Bedrock 모델 ID.

    기본값은 AGENTS.md 의 Sonnet 4.5 이지만 ``--model`` 로 바꿀 수 있습니다. 기본이 아닌
    모델로 낸 결과를 기본 스택의 결과로 착각하지 않도록 리포트에 남깁니다."""

    model_config = {"arbitrary_types_allowed": True}

    def injected_hits(self) -> list[MemoryHit]:
        """수정 프롬프트에 실제로 넣은 메모리만 돌려줍니다."""
        return [h for h in self.memory_hits if h.injected]

    def to_report(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """``.heal/report.json`` 으로 나갈 기계가 읽는 요약입니다."""
        report: dict[str, Any] = {
            "language": self.language,
            "target": self.target_file,
            "status": self.status,
            "model_id": self.model_id,
            "llm_error": self.llm_error,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "memory_degraded_query": self.memory_degraded_query,
            "memory_hits": [
                {
                    "similarity": round(h.similarity, 3),
                    "occurrences": h.occurrences,
                    "pattern": h.title,
                    "languages": h.languages,
                    "injected": h.injected,
                    # 하이브리드 기여도 — 벡터 단독 순위를 함께 남겨 효과를 측정합니다.
                    "vector_rank": h.vector_rank,
                    "bm25_rank": h.bm25_rank,
                    "final_rank": h.final_rank,
                    "boost": round(h.boost, 3),
                }
                for h in self.memory_hits
            ],
            "attempt_log": [a.model_dump() for a in self.attempt_log],
            "guardrail_blocks": self.guardrail_blocks,
            "injection_flags": self.injection_flags,
        }
        if extra:
            report.update(extra)
        return report
