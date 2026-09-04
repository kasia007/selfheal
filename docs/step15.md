# 15단계 — LCEL 구조화 출력 (체크리스트 #1, 필수)

12개 패턴 체크리스트의 **필수 항목 #1**이다. 진단 결과 이 프로젝트는 다섯 곳에서 LLM 을
부르는데, 전부 `self._ask()` 로 **순수 텍스트**를 받아 문자열로 파싱한다
(`with_structured_output` 도, 출력 파서도 쓰지 않는다).

## 현재 LLM 호출 다섯 곳과 파싱 방식

| # | 호출 | 지금 받는 것 | 지금 파싱 방식 | 취약점 |
|---|---|---|---|---|
| 1 | `_locate_with_llm` | 파일 경로 한 줄 | 후보 목록과 **부분 문자열 대조** | 모델이 설명을 덧붙이거나 경로를 살짝 다르게 쓰면 매칭 실패 → 대상 특정 실패 |
| 2 | `bug_report_node` | 산문 문단 | 파싱 없음 (그대로 저장·주입) | 형식이 없어 품질 편차가 그대로 다음 단계로 전달됨 |
| 3 | `memory_search_node` | `MEMORY_FORMAT` 문자열 | 없음 (그대로 임베딩) | 모델이 형식을 안 지키면 저장 문서 형태가 깨지고, 임베딩 정렬이 무너짐 |
| 4 | `memory_modification_node` | `MEMORY_FORMAT` 문자열 | 없음 | 위와 같음. 병합될수록 형식이 흐트러짐 |
| 5 | `code_update_node` | 파일 전체 소스 | `_strip_fence` 로 펜스 제거 | (아래 "구조화하지 않는 것" 참고) |

`MEMORY_FORMAT = "# 패턴명 ## 증상 ### 원인과 해결"` 은 형식을 **프롬프트로 부탁**할 뿐
검증하지 않는다. `MemoryHit.title` 도 첫 줄에서 `#` 을 벗겨 내는 문자열 처리다
(`state.py:47-54`) — 형식이 깨지면 조용히 엉뚱한 제목이 나온다.

## 결정 1 — 방식: 진짜 LCEL 체인 + `PydanticOutputParser`

```python
chain = ChatPromptTemplate.from_template(...) | self.llm | PydanticOutputParser(...)
```

`with_structured_output(Model)` 을 쓰는 대안이 있지만, **오프라인 검증이 원리적으로
불가능하다는 것을 실측으로 확인했다** (langchain-core 1.6.0):

```
GenericFakeChatModel().with_structured_output(BugPattern)
→ NotImplementedError: with_structured_output is not implemented for this model.
```

즉 LangChain 이 제공하는 공식 가짜 채팅 모델조차 그 메서드를 구현하지 않는다. 그 경로로
가면 **tool-use 를 우리가 상상해서 흉내낸 가짜**를 네 종류 만들어야 하고, 그 흉내가 실제
Bedrock 동작과 어긋나면 테스트가 거짓 안심을 준다. 이 프로젝트에서 가짜 모델은 편의가
아니라 가드레일 검증의 핵심 도구다(일부러 나쁘게 구는 모델로 G1·G5·G9 를 시험한다).

반면 LCEL 체인은 확인 결과 **가짜 모델로 그대로 돈다.**

- 공식 `GenericFakeChatModel` 로 체인을 돌려 `BugPattern` 객체가 파싱되는 것을 확인했다.
- 프롬프트를 보고 답을 고르는 우리 대본형 가짜도, `BaseChatModel` 을 상속해 `_generate` 를
  구현하면 체인에서 동작한다(구조화 체인·텍스트 체인 둘 다 확인). 이것이 LangChain 이
  공식적으로 제공하는 확장 지점이다.

체크리스트 부합 관점에서도 이쪽이 명확하다 — #1 이 요구하는 것이 "**LCEL 체인** + Pydantic
구조화 출력"이고, `prompt | llm | parser` 가 정확히 그 패턴이다. `with_structured_output`
은 체인을 쓰지 않고 모델 메서드를 직접 부르는 방식이라 오히려 "LCEL" 쪽이 약하다.

**남는 트레이드오프는 하나** — 모델이 JSON 형식을 어길 수 있다. 파싱 실패 시 **한 번
재시도하고, 그래도 실패하면 기존 텍스트 경로로 폴백한다.** 구조화가 수정 자체를 막아서는
안 된다 — G9 검증과 메모리 검색에 이미 적용한 원칙이다.

### 이 결정이 테스트에 미치는 영향

가짜 모델 4종(`tests/test_wiring.py FakeLLM`, `tests/test_guardrails.py ScriptedLLM`,
`evaluation/run_inout.py ScriptedLLM`·`CountingLLM`)은 지금 `.invoke()` 만 있는 평범한
객체다. 체인에 넣으려면 `BaseChatModel` 상속으로 바꿔야 한다 — 동작은 그대로 유지하고
(프롬프트를 보고 대본을 고름) 구조화 호출에는 JSON 을 돌려주게 한다.

## 결정 2 — 무엇을 구조화하는가

**구조화한다 (1~4번)**

```python
class TargetChoice(BaseModel):        # 1번
    path: str      # 후보 목록에 있는 경로 그대로
    reason: str    # 왜 이 파일인가 (trace.log 에만 남김)

class BugReport(BaseModel):           # 2번
    symptom: str        # 테스트가 무엇을 보고 실패했는가
    root_cause: str     # 왜 그렇게 됐는가
    fix_strategy: str   # 어떻게 고쳐야 하는가 (코드는 쓰지 않음)

class BugPattern(BaseModel):          # 3·4번 — 메모리에 저장되는 단위
    pattern: str            # 패턴명 (MemoryHit.title 이 이 값을 그대로 씀)
    symptom: str
    cause_and_fix: str
```

**구조화하지 않는다 (5번 `code_update_node`)**

파일 **전체 소스**를 JSON 문자열 필드에 담으면 줄바꿈·따옴표·백슬래시가 모두 이스케이프
대상이 되고, 모델이 한 글자만 틀려도 파싱이 깨져 재시도를 낭비한다. 토큰도 늘어난다.
지금은 `_strip_fence` 로 펜스만 벗기면 되고, 구조 검증은 이미 G9(심볼 비교)가 코드
수준에서 한다. **텍스트가 더 안전한 유일한 지점**이므로 그대로 둔다.

## 결정 3 — 저장 형식은 문자열을 유지한다

Chroma 에 저장되고 임베딩되는 것은 **문자열**이어야 한다. 그래서 `BugPattern` 은
**파싱·검증 계층**으로만 쓰고, 저장 직전에 지금과 같은 한 줄 형식으로 렌더링한다.

```python
def render(self) -> str:   # BugPattern
    return f"# {self.pattern} ## {self.symptom} ### {self.cause_and_fix}"
```

이렇게 하면 세 가지가 동시에 성립한다.

- **기존 `chroma_db` 와 호환된다.** 이미 쌓인 문서도 같은 형태라 검색이 그대로 동작한다.
- **`MemoryHit.title` 의 문자열 처리를 없앨 수 있다** — 저장 시점에 `pattern` 을 metadata
  에도 함께 넣어, 제목을 문자열에서 다시 추출하지 않는다.
- **임베딩 정렬이 보장된다.** 저장과 질의가 같은 렌더러를 통과하므로 형태가 어긋날 수 없다.

## 할 일

| # | 할 일 | 파일 |
|---|---|---|
| P15-1 | `src/schemas.py` 신설 — `TargetChoice` · `BugReport` · `BugPattern` (+ `render()`). 한국어 docstring 과 `Field(description=...)` 로 프롬프트 지시를 스키마에 둔다 | `src/schemas.py` (신규) |
| P15-2 | `HealingNodes._ask_structured(template, model, **vars)` 추가 — `ChatPromptTemplate \| llm \| PydanticOutputParser` 체인을 만들어 호출하고, 파싱 실패 시 1회 재시도 후 `None` 반환. 프롬프트·응답은 지금처럼 `trace.log` 에 남긴다 | `src/agent.py` |
| P15-3 | `_locate_with_llm` 을 `TargetChoice` 로 교체. 부분 문자열 대조는 폴백으로만 남긴다 | `src/agent.py` |
| P15-4 | `bug_report_node` 를 `BugReport` 로 교체. `state.bug_report` 에는 렌더링한 문단을 담아 이후 단계 호환을 지킨다 | `src/agent.py` |
| P15-5 | `memory_search_node` · `memory_modification_node` 를 `BugPattern` 으로 교체하고 `render()` 결과를 저장·질의에 쓴다 | `src/agent.py` |
| P15-6 | `MemoryStore.add`·`merge` 가 `pattern` 을 metadata 에 저장, `MemoryHit.title` 이 metadata 값을 우선 사용 (없으면 기존 문자열 처리로 폴백 — 이미 쌓인 문서 호환) | `src/retriever.py`, `src/state.py` |
| P15-7 | 가짜 모델 4종을 `BaseChatModel` 상속으로 바꾸고(체인에서 동작하려면 Runnable 이어야 함) 구조화 호출에는 JSON 을 돌려주게 갱신 | `tests/test_wiring.py`, `tests/test_guardrails.py`, `evaluation/run_inout.py` |
| P15-8 | 구조화 파싱 실패 시 폴백이 동작하는지 검증하는 테스트 추가 | `tests/test_wiring.py` |
| P15-9 | `SERVICE.md`·`README.md` 에 구조화 출력 계층을 반영 | `SERVICE.md`, `README.md` |

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 스키마 파싱 | 가짜 모델이 정상 JSON 반환 | 각 노드가 Pydantic 객체를 받고 그래프가 끝까지 돔 |
| 파싱 실패 폴백 | 가짜 모델이 깨진 JSON 반환 | 재시도 1회 후 텍스트 경로로 폴백, 파이프라인 계속 진행 |
| 대상 특정 견고함 | 모델이 경로에 설명을 덧붙여 반환 | `TargetChoice.path` 로 정확히 특정 (기존에는 실패했을 입력) |
| 메모리 형식 | 저장된 문서 확인 | `# 패턴명 ## 증상 ### 원인과 해결` 형태 유지, metadata 에 `pattern` 존재 |
| 기존 메모리 호환 | 이전 형식 문서가 있는 `chroma_db` 로 검색 | 검색·병합·`--stats` 모두 동작, `title` 폴백이 작동 |
| 회귀 | `python -m pytest tests/ -q` · `python evaluation/run_inout.py` | 23건 + 12/12 그대로 통과 |
