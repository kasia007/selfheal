# 9단계 — 메모리 검색 (S9)

`src/agent.py:339 memory_search_node` 에 이미 구현되어 있다 — LLM 호출 1회로 질의어를
압축한 뒤, `src/retriever.py MemoryStore.search` 로 Chroma 벡터 검색을 한다.

```python
state.memory_query = self._ask(
    "다음 버그 리포트를 나중에 찾아 쓰기 좋게 압축하십시오.\n"
    f"[버그 리포트] {state.bug_report}\n\n"
    f"형식: {MEMORY_FORMAT}\n"
    "다른 말 없이 이 형식만 출력하십시오."
)
state.memory_hits = self.memory.search(state.memory_query, state.language)
render_memory_hits(self.console, state.memory_hits)
```

## 확인한 것 — 질의어를 그대로 안 쓰는 이유

8단계의 `bug_report`를 검색어로 바로 쓰지 않고, **저장할 때와 같은 템플릿
(`MEMORY_FORMAT`)으로 한 번 더 압축**한다. 저장된 문서와 질의의 표현 형태가 다르면
임베딩 거리가 실제 유사도를 반영하지 못하기 때문이다 — 이 프로젝트가 원본 노트북 대비
고친 "검색 결과를 실제로 주입한다" 개선(README 1절 #1)과 짝을 이루는 부분이다.

`retriever.py MemoryStore.search`가 실제로 하는 일:

- `--no-memory`(`enabled=False`)면 즉시 빈 리스트 — LLM·벡터DB 호출 둘 다 안 함.
- **언어별 격리**: 기본적으로 `where={"language_primary": state.language}`로 같은 언어
  안에서만 찾는다. Python `IndexError` 기억이 Go 슬라이스 수정에 끌려오는 오염을 막는다.
  `--cross-language`로 격리를 풀면 "언어를 넘는 패턴 전이" 실험이 된다.
- 벡터DB 예외는 삼키고 빈 리스트를 반환한다 — "메모리는 있으면 좋은 것이지 필수가
  아니다"라는 원칙(`retriever.py:134-137`)에 따라, DB 문제로 코드 수정 자체가 멈추지
  않게 한다.
- 결과는 `MemoryHit`(유사도·발생 횟수·최초/최근 관측일)로 화면에 표시된다
  (README 예시의 `🔎 유사 버그 검색 ... 1건` 블록).

## 결정 — `--no-memory` 완전 제거

9단계가 의미를 가지려면 메모리가 항상 켜져 있어야 한다는 논의 끝에, `--no-memory` 옵션과
그것이 뒷받침하던 "OFF 기준선 vs ON" A/B 비교 구조를 **없애기로 결정했다.** 메모리는
이제 상시 활성이고, 이 노드의 `if not self.memory.enabled: ...` 조기 반환 분기는 죽은
코드가 된다.

이 결정은 9단계 하나에 그치지 않고 여러 파일에 걸쳐 있다 — 실제 구현은 각 파일이 속한
단계(또는 별도 정리 단계)에서 다루고, 여기서는 영향 범위만 남겨 둔다.

| 파일 | 영향 |
|---|---|
| `src/retriever.py MemoryStore.__init__` | `enabled` 파라미터·분기 제거 검토 |
| `src/agent.py` | `memory_search_node`의 비활성 분기 제거, CLI `--no-memory` 인자 제거 |
| `evaluation/run_inout.py`, `evaluation/eval_set.json` | `--no-memory` 로 도는 케이스·플래그 정리 |
| `evaluation/round1_report.md` / `round2_report.md` | OFF 조건이 없어져 1차/2차로 나눌 근거도 사라짐 → **지금은 두 리포트를 하나로 합친다** (예: `evaluation/report.md`), `run_inout.py`/`run_ragas.py`의 `--round` 인자도 함께 정리. 단, 2회 대조 자체는 완전히 버리지 않는다 — 나중에 다른 기준(예: merge-threshold 값 차이, warmup 전/후)으로 두 라운드를 다시 도입할지는 **추가 기능으로 보류**하고, 진행 여부는 그때 다시 판단한다 |
| `README.md` 5절, `SERVICE.md` 3절 | OFF/ON 비교 실험 설명을 새 구조에 맞게 수정 |

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 정상 검색 | 사례가 쌓인 뒤 `./run.sh data/samples/py-dict` | `🔎 유사 버그 검색 ... N건` + 유사도·발생 횟수 표시 |
| 언어 격리 | Python 패턴을 쌓은 뒤 `./run.sh data/samples/js-head` (기본, `--cross-language` 없이) | Python 패턴이 검색 결과에 안 나옴 |
| 언어 전이 실험 | `./run.sh data/samples/js-head --cross-language` | Python 패턴도 후보에 포함됨 |
| DB 오류 내구성 | (수동) `collection.query` 가 예외를 던지도록 임시 패치 후 실행 | 빈 결과로 처리되고 파이프라인은 계속 진행 |
| 회귀 | `python -m pytest tests/ -q` | 기존 20건 전부 통과 (`--no-memory` 케이스 제거 후 재계산) |
