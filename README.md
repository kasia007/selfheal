# selfheal · 스스로 고치는 코드 에이전트

> **디렉터리 하나만 주면, 깨진 테스트를 스스로 찾아 고칠 방법을 제안합니다.**
> 원본은 **사용자가 승인하기 전까지 건드리지 않습니다.**

```bash
./run.sh ./data/samples/py-index            # 제안만 (원본 불변)
./run.sh ./data/samples/py-index --apply    # 검수 후 실제 적용
```

```
🔍 언어 감지: python (pyproject.toml)
   툴체인: Python 3.14.7
🧪 테스트 실행 ... (python -m pytest -q)
   FAIL — IndexError: list index out of range
📍 수정 대상 추론: boundary.py:11
📝 버그 리포트 작성
🔎 유사 버그 검색 ... 1건
   [1] 유사도 0.81   2회 발생
       # 경계 검사 누락 ## IndexError ### 범위 확인 후 기본값 반환
       언어: python   |   최초 2026-08-21  최근 2026-08-30
🧠 기존 패턴에 병합 → 누적 3회 발생
🩹 패치 제안 [시도 1/3]
   → 과거 사례 1건을 수정 프롬프트에 주입
🧪 테스트 실행 ... PASS

📋 수정안 준비 완료 (샌드박스에서 테스트 통과, 시도 1회)
   원본 파일은 수정하지 않았습니다. 아래 diff 를 검수하십시오.

--- a/boundary.py
+++ b/boundary.py
@@ -7,4 +7,8 @@
 def double_at(items, index):
+    if items is None:
+        return 0
+    if index < 0 or index >= len(items):
+        return 0
     return items[index] * 2

   적용하려면 같은 명령에 --apply 를 붙여 다시 실행하십시오.
```

아래 1~8절은 제출 규약(§4-5)이 요구하는 템플릿 순서입니다. 그 뒤 부록에 같은 내용을
더 깊이 다루는 절이 이어집니다.

---

## 1. 무엇을 푸나

경계 검사 누락·None 역참조·부재 키 접근 같은 **정형화된 실수**를, 디렉터리(또는 파일)
경로 하나만 받아 스스로 찾아 고치고, 같은 실수가 반복되는지 기억해 알려 주는 에이전트다.
자세한 문제·가치 정의는 [SERVICE.md](SERVICE.md) 1절 참고.

## 2. 활용한 패턴 (Day 1~7)

체크리스트 12개 패턴 중 **확실히 충족 6개**(필수 4개 포함, 권장 기준 만족).

| # | 패턴 | Day | 이 프로젝트에서 |
|---|---|---|---|
| 1 | LCEL chain (Pydantic 구조화 출력) | Day 1 | `src/schemas.py` — `ChatPromptTemplate \| llm \| PydanticOutputParser` 로 4곳 구조화 |
| 3 | RAG (하이브리드·리랭킹·쿼리 확장) | Day 2 | `src/retriever.py` — 벡터+BM25 RRF 융합 + 도메인 리랭킹 |
| 6 | 가드레일 (비밀값·프롬프트 인젝션) | Day 5 | `src/guardrails.py` — G10(마스킹)·G11(데이터/지시 분리) |
| 10 | 장기 메모리 | Day 7 | `src/retriever.py` — Chroma `PersistentClient` + `occurrences` 누적 |
| 11 | Observability · Trace | Day 7 | `src/tracing.py` — LangSmith 옵트인, 실행 1개 = 트레이스 1개 |
| 12 | 평가 (RAGAS) | Day 7 | `evaluation/run_ragas.py` |

부분 충족(미포함): #4 도구 다중(그래프 고정 순서라 LLM 자율 결합 아님), #7 HITL
(`--apply` 재실행이지 `interrupt()` 아님), #8 미들웨어(재시도만 있고 요약·마스킹
추상화 없음). 상세 진단은 `docs/step15.md`~`step19.md` 참고.

## 3. 아키텍처

```
run.sh                  src/agent.py 를 호출하는 얇은 실행 스크립트
src/
  agent.py              메인 에이전트 그래프 + CLI 진입점 (감지·preflight·판정,
                        그래프 배선, 노드 9개 — code_update_node 가 핵심 기여)
  tools.py              도메인 도구 — 언어별 지식 전부 (여기만 고치면 언어 추가)
  retriever.py          RAG 파이프라인 — 하이브리드(벡터+BM25) · RRF 융합 · 도메인 리랭킹
  schemas.py            LLM 구조화 출력 스키마 (LCEL 체인 + Pydantic)
  tracing.py            LangSmith 추적 설정 (옵트인 — 기본 꺼짐)
  guardrails.py         비밀값 마스킹 · 프롬프트 인젝션 탐지 (G10·G11)
  state.py              그래프 상태 (언어 의존 값 없음)
  report.py             로그 · diff · report.json · exit code
data/samples/
  py-index/             1회차 학습용 (경계 검사 누락)
  py-dict/              2회차 평가용 (같은 계열, 다른 버그)
  js-head/              언어 전이 실험용
```

그래프 흐름과 노드별 역할은 [SERVICE.md](SERVICE.md) 3절, 입력 추론·LLM 호출·검색
파이프라인의 세부 근거는 부록 B·C·D 참고.

## 4. 실행 방법

스택은 이 저장소 지침(`AGENTS.md`)을 따른다 — **Bedrock Claude Sonnet 4.5 + Titan
임베딩**, `us-east-1`(원본 노트북의 OpenAI 가 아님). 따라서 AWS 자격증명이 필요하다.
API 서버(`POST /query`)는 만들지 않았다 — 이 CLI 의 입력(디렉터리·파일 경로)이 이미
유효한 사용자 입력이라 초기 설계 단계에서 범위를 CLI 로 확정했다(`docs/step0.md`).

```bash
# 배선 + 가드레일 검증 (자격증명 불필요)
python -m pytest tests/ -q                # 79 passed

# 인-아웃 세트 — 가드레일·전제·no_fix 는 자격증명 없이 채점됩니다
python evaluation/run_inout.py --round 1              # → round1_report.md
python evaluation/run_inout.py --online --round 2     # fix 케이스까지 → round2_report.md

# RAGAS 평가 — run_inout.py 뒤에 RAGAS 절을 덧붙입니다 (같은 --round 사용)
python evaluation/run_ragas.py --round 2

# 실제 수정 (자격증명 필요)
./run.sh ./data/samples/py-index            # 제안만 (원본 불변)
./run.sh ./data/samples/py-index --apply    # 검수 후 실제 적용
./run.sh ./data/samples/py-index --open-diff  # 제안된 patch.diff 를 브라우저로 바로 확인
./run.sh --stats                            # 누적 버그 패턴 리포트
```

오프라인 채점에서는 임베딩 실호출을 피하려고 `src/retriever.py` 의 `HashEmbeddings` 를
주입한다. 언어 추가는 `src/tools.py` 의 `ADAPTERS` 에 항목 하나만 넣으면 된다 — Go
어댑터는 이미 들어 있어 툴체인만 설치하면 그대로 동작한다.

## 5. RAGAS 평가 결과

`evaluation/round2_report.md` 의 도메인 지표(실제로 고쳤는가)는 **3/3 fixed, 평균 시도
1.00회, 주입 2건·2건·1건**으로 채워져 있다.

**RAGAS 정량 지표(context precision/recall·faithfulness·relevancy) 자체는 미실행이다.**
`ragas` 의 의존성인 `scikit-network` 가 이 개발 환경(Python 3.14·Windows)에서 사전
빌드 wheel 이 없어 소스 빌드가 필요한데, Visual C++ 빌드 도구가 없어 설치가 실패한다.
코드·설정 문제가 아니라 환경 제약이며, 수치를 임의로 채우지 않고 `round2_report.md`
에 그 사유를 그대로 남겼다. 빌드 도구가 있는 환경에서 `python evaluation/run_ragas.py
--round 2` 로 재실행하면 채워진다.

## 6. 인-아웃 세트 통과율 (1차 → 2차 개선폭)

| 지표 | 1차 (`round1_report.md`, Day 9) | 2차 (`round2_report.md`, Day 10) | 개선폭 |
|---|---|---|---|
| 채점 모드 | 오프라인 (자격증명 없음) | **온라인** (Bedrock+Titan 실제 호출) | 최초로 실제 검증 |
| 통과율 | 15/15 (오프라인 가능분) | **18/18** (전체) | +3건 채점 대상 확대 |
| fix 케이스 (실제 버그 수정) | ⏭️ 미채점 3건 | ✅ **3/3 통과** | +3건 |
| 메모리 주입 건수 | 측정 불가 | **2건·2건·1건** | 0 → 실제 주입 확인 |
| `--merge-threshold` | 0.3 (미검증 초기값) | **0.55** (실측 재보정) | 데이터 기반 재보정 완료 |
| 가드레일 위반 | 0건 | 0건 | 유지 |

**가장 중요한 개선은 "메모리 주입이 실제로 동작하는 것을 처음 확인했다"는 점이다.**
1차 시점까지는 `TitanEmbeddingFunction` 에 `embed_query` 가 없어 검색이 한 번도
동작한 적이 없었다(7절 참고). 2차에서 그 버그를 고치고 임계값을 재보정한 뒤, 처음으로
실제 Bedrock+Titan 조합으로 fix 케이스가 통과하고 메모리가 주입되는 것을 확인했다.

## 7. 트라이앤에러 회고

- **검색이 한 번도 동작하지 않았던 버그** — `TitanEmbeddingFunction` 에 `embed_query`
  가 없어 chromadb 의 질의 임베딩이 항상 실패했고, `except Exception: return []` 가
  그것을 조용히 삼켰다. 저장은 되고 검색만 실패해 메모리 주입(이 프로젝트의 핵심 기여)
  이 침묵 속에 꺼져 있었다. 축약 경로(exit 5) 검증 중 발견해 고쳤다(`docs/step17.md`).
- **`--merge-threshold` 0.3 은 검증되지 않은 초기값이었다** — 실측 결과 진짜 같은
  계열 버그의 임베딩 거리가 0.49~0.73 사이였고, 8개 측정 전부 0.3을 넘어 단 한 번도
  병합되지 않았다. 0.55 로 재보정했지만 이마저 완벽하지 않다 — LLM 이 매번 요약을
  다시 생성하므로 거리가 실행마다 흔들린다(6절 실험 파라미터 참고).
- **배포 스크립트의 비밀값 유출 구멍** — `package.py` 가 `.env`(실제 AWS·LangSmith
  키)를 디렉터리 이름 필터로만 걸러서, 파일인 `.env` 는 그대로 zip 에 담기고
  있었다. "다른 사람에게 공유하려면 무엇을 보내야 하는가" 를 점검하다 발견해 즉시
  고쳤다(`EXCLUDE_FILES`).
- **`with_structured_output()` 대신 LCEL 체인을 택한 이유** — 처음엔 더 견고해
  보였지만, LangChain 공식 가짜 채팅 모델조차 그 메서드를 구현하지 않아
  (`NotImplementedError`) 오프라인 가드레일 검증이 불가능했다. `ChatPromptTemplate |
  llm | PydanticOutputParser` 로 바꿔 기존 검증 구조를 지켰다(`docs/step15.md`).
- **exit code 를 5개까지 늘린 이유** — 모델 사용 불가를 "못 고침(1)"과 뭉치면 CI 가
  코드 결함과 인프라 제약을 구분하지 못한다. 축약 경로를 만들고, 훅이 exit 5 를
  푸시 차단하지 않도록 함께 고쳤다(`docs/step17.md`).

## 8. 핵심 코드 위치

| 위치 | 내용 |
|---|---|
| `src/agent.py` | 메인 그래프 배선 + CLI. `HealingNodes` 가 노드 9개, `_ask_structured` 가 LCEL 체인 호출 |
| `src/tools.py` | 언어 어댑터(`ADAPTERS`), `extract_symbols`(G9 구조 검증) |
| `src/retriever.py` | `MemoryStore.search` — 쿼리 확장·하이브리드·리랭킹 3층 |
| `src/schemas.py` | 구조화 출력 스키마 (`TargetChoice`·`BugReport`·`BugPattern`) |
| `src/guardrails.py` | `mask_secrets`·`detect_injection`·`wrap_untrusted` (G10·G11) |
| `src/tracing.py` | `configure_tracing` — LangSmith 옵트인 판단 |
| `src/report.py` | exit code 상수, `Console`(마스킹 단일 지점), `write_artifacts` |
| `evaluation/` | `run_inout.py`·`run_ragas.py`(채점기), `test_queries.csv`(§4-4 스키마) |
| `docs/step0.md`~`step19.md` | 단계별 설계 결정과 근거 (이 프로젝트의 전체 개발 이력) |

---
---

# 부록 — 원본 대비 변경점과 구현 세부

## 부록 A. 산출물

| 파일 | 내용 |
|---|---|
| [SERVICE.md](SERVICE.md) | **설계 명세** — 도메인 · 인/아웃 세트 · 측정 지표 · 가드레일 G1~G11 · 성공 기준 |
| [Dockerfile](Dockerfile) | 실행 환경 (에이전트 런타임 + 대상 언어 툴체인) |
| [run.sh](run.sh) | 실행 스크립트 — `src/agent.py` 를 호출하는 얇은 래퍼 |
| [install-hooks.sh](install-hooks.sh) | git 훅 설치 — `pre-push` 로 푸시 전 검증, `post-commit` 으로 커밋 후 알림 |
| [USAGE.md](USAGE.md) | 실사용 가이드 (설치·기본 사용법·문제 해결) |
| [evaluation/test_queries.csv](evaluation/test_queries.csv) | 제출 규약 스키마의 인-아웃 세트 16케이스 |
| [evaluation/eval_set.json](evaluation/eval_set.json) | 위와 같은 케이스의 실행기용 내부 사양 |
| [evaluation/round1_report.md](evaluation/round1_report.md) | 1차 자체 평가 — Day 9 시점 (개선 전) |
| [evaluation/round2_report.md](evaluation/round2_report.md) | 2차 자체 평가 — Day 10 시점 (개선 후 · 개선폭 명시) |
| `tests/` | 배선·구조화 13건 + 가드레일 16건 + 관측 8건 + 축약 경로 7건 + 검색 19건 + 마스킹·인젝션 15건 (**실호출 없음**) |

```bash
python package.py     # dist/selfheal.zip 생성
```

## 부록 B. 원본과 다른 점

출발점은 [NirDiamant/GenAI_Agents 의 self_healing_code.ipynb](https://github.com/NirDiamant/GenAI_Agents/blob/main/all_agents_tutorials/self_healing_code.ipynb) 다.
그래프 골격은 그대로 두고, 실제로 쓸 수 없게 만들던 지점 여섯 개를 고쳤다.

| # | 원본 | 여기 |
|---|---|---|
| 1 | 메모리를 검색해 놓고 **수정 프롬프트에 넣지 않음** → 벡터DB가 결과에 기여 안 함 | 검색·필터링한 과거 사례를 **실제로 주입**. 그래야 효과를 측정할 수 있음 |
| 2 | `exec` 로 함수를 바꿔치기 → **파이썬 전용** | 파일 수정 + 테스트 재실행 → **언어 어댑터로 확장** |
| 3 | "에러 메시지를 리턴하라"는 프롬프트가 성공 기준 → 에러를 **삼키는** 것 | **테스트 통과**가 유일한 성공 기준 |
| 3b | `exec` 로 **즉시 런타임에 반영** → 사람이 검수할 틈 없음 | **제안 → 검수 → `--apply`**. 원본은 승인 전까지 불변 |
| 4 | 패치→재실행 루프에 **재시도 제한 없음** | `--max-attempts` (기본 3) 로 제동 |
| 5 | `chromadb.Client()` — 세션 끝나면 기억 소멸 | `PersistentClient` — 회차 간 비교 가능 |
| 6 | 같은 버그가 몇 번 터졌는지 **셀 수 없음** | metadata 에 `occurrences` 누적 → `--stats` 리포트 |

추가로, LLM 이 만든 코드를 우리 프로세스에서 `exec` 하지 않고 **서브프로세스에서
테스트로만** 돌리므로 샌드박싱도 부수적으로 해결된다.

## 부록 C. 입력 추론 · LLM 구조화 출력

사용자가 주는 것은 **디렉터리 하나**다. 나머지는 전부 추론한다.

| | 어떻게 |
|---|---|
| 언어 | 마커 파일(`go.mod`, `package.json`, `pyproject.toml`) → 없으면 확장자 최빈값 |
| 테스트 명령 | 어댑터 기본값 → `package.json` 의 `scripts.test` 가 있으면 그쪽 |
| **고칠 파일** | **테스트 실패 출력의 스택트레이스에서 뽑음** (규칙 기반 → 실패 시 LLM 폴백) |

핵심은 마지막 줄이다. 실패 출력에는 이미 답이 들어 있다.

```
--- FAIL: TestGet
    slice_util.go:12: index out of range [5] with length 3
```

다섯 번의 LLM 호출 중 **네 번은 구조화 출력**이다 —
`ChatPromptTemplate | llm | PydanticOutputParser` 체인으로 받아 Pydantic 으로 검증한다
(`src/schemas.py`).

| 호출 | 스키마 | 왜 |
|---|---|---|
| 수정 대상 고르기 (LLM 폴백) | `TargetChoice` | 예전에는 응답에서 **부분 문자열 대조**로 경로를 찾아, 모델이 설명을 덧붙이면 특정에 실패했다 |
| 버그 리포트 | `BugReport` | 증상·원인·해결 방향을 분리해 품질 편차를 줄인다 |
| 메모리 질의어 압축 · 병합 | `BugPattern` | 저장 형식을 프롬프트로 *부탁* 하는 대신 파싱 시점에 **검증**한다 |

**수정안 생성만 텍스트로 받는다.** 파일 전체 소스를 JSON 필드에 담으면 줄바꿈·따옴표가
모두 이스케이프 대상이 되어 파싱이 깨지기 쉽고 토큰도 늘어난다. 그쪽 구조 검증은
G9(심볼 비교)가 코드 수준에서 한다.

구조화가 실패하면 **한 번 재시도하고, 그래도 실패하면 예전 텍스트 경로로 폴백**한다.
구조화는 품질 장치이지 관문이 아니다.

## 부록 D. 과거 사례를 어떻게 찾는가

벡터 검색 하나로 끝내지 않는다. 세 층이다(`src/retriever.py`).

**① 쿼리 확장** — `BugPattern` 의 세 면(패턴명·증상·원인과해결)을 **각각** 질의로 던진다.
한 문장으로 합쳐 던지면 세 면이 평균되어 흐려지는데, 나눠 던지면 "증상으로 찾기"와
"해결 방법으로 찾기"가 각각 살아난다. LLM 호출은 늘지 않는다 — 구조화 출력을 재사용하기
때문이다. 모델을 못 쓰는 축약 경로에서는 규칙 기반으로 확장한다(테스트 출력의 예외
이름·파일명·언어).

**② 하이브리드** — 벡터 검색과 BM25 키워드 검색을 각각 돌려 **RRF**로 합친다.
벡터는 의미가 비슷하면 잡지만 `IndexError` 같은 **정확한 단어 일치에는 약하다.**
점수를 가중 합하지 않는 이유는 코사인 거리와 BM25 점수가 비교 불가능한 척도라서다 —
가중치를 두면 검증되지 않은 상수가 하나 더 생기고 코퍼스마다 어긋난다. RRF 는 순위만
쓰므로 조정할 값이 없다.

**③ 리랭킹** — 새 모델 없이 **도메인 신호**로 보정한다. 핵심은 `occurrences` 다 —
이 코드베이스가 **반복하는 실수**일수록 먼저 볼 만하고, 이는 원본 노트북이 셀 수 없었던
이 구현만의 신호다. 여기에 언어 일치와 최근성을 곱셈으로 얹는다.

기여도는 화면과 `report.json` 에 남는다 — `벡터 2위 · BM25 1위 → 최종 1위 (보정 ×1.10)`.
**벡터 단독 순위를 함께 기록하는 이유**는 하이브리드가 실제로 도움이 됐는지를 주장이 아니라
숫자로 판단하기 위해서다.

## 부록 E. 절대 하지 않는 것

- **테스트 파일을 수정하지 않는다.** 열어 주면 LLM 이 테스트를 고쳐서 통과시킨다. 실제로 자주 터지는 실패 모드라 어댑터 수준에서 막았다.
- **`workdir` 밖을 건드리지 않는다.** `node_modules`, `vendor`, `site-packages` 등도 제외한다.
- **승인 없이 원본에 쓰지 않는다.** 수정과 검증은 임시 사본에서 하고, `--apply` 가 있을 때만 원본에 반영한다. 그래서 실패해도 "반쯤 고친 코드"가 남지 않는다.

## 부록 F. 출력 상세

**화면** — 진행 로그 + diff (전체 소스가 아니라 diff 여야 리뷰가 된다)

**`.heal/`** — `report.json`, `patch.diff`, `trace.log`(프롬프트·응답 전문)

**LangSmith** (선택) — `.env` 에 `LANGSMITH_TRACING=true` 와 `LANGSMITH_API_KEY` 를 **둘 다**
넣으면 실행 하나가 트레이스 하나로 잡힌다. 재시도별 시도 횟수·메모리 주입 건수와
**G9 거부 사유**가 메타데이터로 붙어, 여러 실행에 걸친 실패 패턴을 볼 수 있다.

> ⚠️ 추적을 켜면 프롬프트가 외부로 전송되고, 그 안에는 **검수 대상 소스 코드 전문**이
> 들어 있다. 그래서 기본은 꺼져 있고, 켜지면 `🔭 LangSmith 추적 활성` 을 화면에 찍고
> `report.json` 의 `trace_enabled` 에 기록한다. 키가 없으면 켜지지 않는다 —
> 켜 두면 401 오류 덤프가 화면을 뒤덮기 때문이다.

```json
{
  "language": "python", "target": "boundary.py",
  "status": "fixed", "attempts": 1,
  "memory_hits": [
    {"similarity": 0.81, "occurrences": 3, "pattern": "경계 검사 누락", "injected": true}
  ]
}
```

exit code 상세 표는 [SERVICE.md](SERVICE.md) 부록 B 참고.

## 부록 G. 메모리 효과를 측정하는 법 (실행 예시)

메모리는 **항상 켜져 있다.** 가치는 **2회차부터** 나타난다 — 1회차에는 기억이 비어
있어 주입할 것이 없기 때문이다. 그리고 **같은 버그를 다시 돌리는 것은 캐시일 뿐 학습이
아니다.** 그래서 샘플을 "1회차 학습용 / 2회차 평가용" 쌍으로 설계해 두었다.

```bash
./run.sh ./data/samples/py-index                  # 1회차 — 패턴 축적 (주입 0건)
./run.sh ./data/samples/py-dict                   # 2회차 — 같은 계열의 다른 버그. 주입 발생
./run.sh ./data/samples/js-head --cross-language  # 언어를 넘는 전이 실험
./run.sh --stats                                  # 누적 리포트
```

`.heal/report.json` 의 `attempts` 와 `memory_hits[].injected` 를 함께 모으면 발표용 표가
나온다. **두 값을 같이 봐야 한다** — 주입이 0건인데 시도가 적다면 메모리와 무관하게
쉬운 버그였다는 뜻이다. 실제 실측값은 6절 표 참고.

## 부록 H. 실험 파라미터

`--merge-threshold` (기본 0.55, 2026-09-03 온라인 실측으로 0.3에서 조정) 는 하드코딩해
두면 안 되는 값이다.
- 너무 빡빡하면: 같은 버그가 별개 패턴으로 쪼개져 전부 "1회"로 남는다. 실제로 0.3에서는
  세 실행(py-index·py-dict·js-head)의 거리 8개가 전부 0.3을 넘어 단 한 번도 병합되지
  않았다.
- 너무 느슨하면: 무관한 버그가 한 덩어리로 뭉쳐 발생 횟수가 부풀려진다. 0.55는 같은
  계열의 근접 거리(0.49~0.52)와 다른 계열의 거리(0.63 이상) 사이에 있다. 표본이
  8개뿐이라 확정값이 아니라 갱신된 실험값이다 — 더 쌓이면 다시 조정하십시오.
