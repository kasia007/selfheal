# 16단계 — Observability · 트레이싱 (체크리스트 #11, 필수)

12개 패턴 체크리스트의 **필수 항목 #11**이다. 지금은 `src/report.py` 의 `Console.record`
가 프롬프트·응답 전문을 모아 `.heal/trace.log` 로 남기는 **자체 기록**만 있고,
LangSmith·LangFuse 연동이 없다.

15단계에서 LLM 호출을 LCEL 체인으로 바꿔 둔 것이 여기서 값을 한다 — LangChain 의 추적은
`Runnable` 단위로 자동 수집되므로, 체인화된 호출은 별도 계측 코드 없이 트레이스에 잡힌다.

## 결정 1 — LangSmith 를 쓴다 (LangFuse 아님)

`langsmith` 는 **이미 설치돼 있다**(0.11.1). langchain-core 의 의존성이라 `requirements.txt`
에 새 줄을 더할 필요조차 없다. 반면 `langfuse` 는 설치돼 있지 않아 의존성이 하나 늘어난다.
체크리스트가 "LangSmith/LangFuse" 중 하나를 요구하므로 전자로 충족한다.

## 결정 2 — **키가 있을 때만** 추적을 켠다 (실측 근거)

키 없이 `LANGSMITH_TRACING=true` 를 켜고 체인을 돌려 본 결과:

- 체인 자체는 **예외 없이 정상 동작한다** (결과 반환 확인)
- 그러나 `LangSmithMissingAPIKeyWarning` 과 함께 **401 오류 덤프가 stderr 로 쏟아진다** —
  `Failed to send compressed multipart ingest: ... 401 Unauthorized` 뒤에 트레이스 ID 가
  수십 개 딸려 나온다

이 프로젝트의 화면 출력은 "에이전트가 무엇을 근거로 판단했는지 보이게 하는" 산출물이다.
그 위에 401 덤프가 얹히면 읽을 수 없게 된다. 따라서 **`LANGSMITH_API_KEY` 가 실제로
있을 때만** 추적을 활성화하고, 없으면 아예 켜지 않는다(환경변수를 대신 설정해 주지 않는다).

## 결정 3 — 기본은 꺼져 있고, 사용자가 명시적으로 켠다

**추적을 켜면 프롬프트가 외부 서비스로 나간다. 그 프롬프트에는 사용자의 소스 코드 전문이
들어 있다.** 이 프로젝트의 핵심 약속이 "승인 전까지 원본을 건드리지 않는다"(G6)인데,
코드를 조용히 외부로 보내는 것은 그 약속의 정신에 어긋난다.

그래서 두 가지를 지킨다.

1. **옵트인** — `.env` 에 `LANGSMITH_TRACING=true` 와 `LANGSMITH_API_KEY` 를 **둘 다**
   넣어야 켜진다. 하나만 있으면 켜지지 않는다.
2. **켜졌으면 화면에 알린다** — `🔭 LangSmith 추적 활성 (프로젝트: selfheal)` 한 줄을
   찍어, 코드가 외부로 나가는 실행임을 사용자가 모르고 지나칠 수 없게 한다.

## 결정 4 — `trace.log` 는 그대로 유지한다

둘은 역할이 다르므로 공존한다.

| | `.heal/trace.log` | LangSmith |
|---|---|---|
| 자격증명 | 불필요 | API 키 필요 |
| 범위 | 이번 실행 하나 | 실행 간 비교·집계 |
| 용도 | 제출 산출물·오프라인 검수 | 재시도 원인 분석·회귀 추적 |

`trace.log` 를 없애면 자격증명 없는 채점 환경에서 아무 근거도 남지 않는다.

## 결정 5 — 무엇을 트레이스에 담는가

단순히 켜는 것만으로는 "관측 가능"하다고 하기 어렵다. **트레이스를 보고 "왜 이 시도가
실패했는가"에 답할 수 있어야** 값이 있다. 그래서 그래프 실행 전체를 하나의 루트 런으로
묶고, 판단 근거를 메타데이터로 붙인다.

- **루트 런** — `graph.invoke(state, config=...)` 에 `run_name`(대상 폴더 이름)과
  `tags`(언어, `selfheal`), `metadata`(테스트 명령·재시도 상한·병합 임계값)를 넘긴다.
  LangGraph 도 `Runnable` 이라 그래프 한 번 실행이 트레이스 하나로 잡힌다.
- **노드별 런** — 15단계의 체인 호출에 `run_name`(`bug_report` 등)과 `metadata`
  (시도 횟수, 주입한 메모리 건수)를 붙인다.
- **가드레일 판정** — G9 가 수정안을 거부하면 그 사유(사라진 심볼 목록)를 메타데이터로
  남긴다. **이것이 이 프로젝트에서 관측이 가장 필요한 지점이다** — 지금은 콘솔 한 줄로
  흘러가 버려서 여러 실행에 걸친 패턴을 볼 수 없다.

## 할 일

| # | 할 일 | 파일 |
|---|---|---|
| P16-1 | `src/tracing.py` 신설 — `configure_tracing(console)` 이 `LANGSMITH_TRACING`·`LANGSMITH_API_KEY` 가 **둘 다** 있을 때만 활성화하고, 활성 시 화면에 한 줄 알린다. 활성 여부를 bool 로 돌려준다 | `src/tracing.py` (신규) |
| P16-2 | `main()` 이 `.env` 로드 직후 `configure_tracing` 을 호출. 키가 없으면 조용히 통과 | `src/agent.py` |
| P16-3 | `graph.invoke` 에 `config`(run_name·tags·metadata) 전달 — 실행 하나가 트레이스 하나로 잡히게 | `src/agent.py` |
| P16-4 | `_ask` · `_ask_structured` 가 호출 이름과 메타데이터(시도 횟수·주입 건수)를 `config` 로 전달 | `src/agent.py` |
| P16-5 | G9 거부 사유를 트레이스 메타데이터로 남김 | `src/agent.py` |
| P16-6 | `report.json` 에 `trace_enabled` 를 기록 — 리포트만 보고도 그 실행이 외부로 나갔는지 알 수 있게 | `src/state.py` |
| P16-7 | `.env.example` 에 LangSmith 항목과 **코드가 외부로 나간다는 주의**를 명시 | `.env.example` |
| P16-8 | 키 없이는 추적이 켜지지 않고 401 덤프가 나오지 않는지 검증하는 테스트 | `tests/test_wiring.py` |
| P16-9 | `SERVICE.md`·`README.md` 에 관측 계층과 옵트인 정책 반영 | `SERVICE.md`, `README.md` |

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 기본 꺼짐 | 환경변수 없이 실행 | 추적 안내 없음, 401 덤프 없음, 동작 동일 |
| 키만 있음 | `LANGSMITH_API_KEY` 만 설정 | 켜지지 않음 (명시적 옵트인 필요) |
| 플래그만 있음 | `LANGSMITH_TRACING=true` 만 설정 | 켜지지 않음 — **401 덤프가 없어야 한다** |
| 둘 다 있음 | 둘 다 설정 | `🔭 LangSmith 추적 활성` 표시, `report.json` 의 `trace_enabled: true` |
| 테스트 격리 | `python -m pytest tests/ -q` | 추적이 켜지지 않고 27건 통과 |
| 회귀 | `python evaluation/run_inout.py` | 12/12 그대로 |
