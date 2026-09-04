# 11단계 — 메모리 병합·신규 저장 (S11)

10단계에서 갈라진 두 갈래가 각각 여기서 끝난다. `src/agent.py:373 memory_modification_node`
(병합)와 `src/agent.py:396 memory_generation_node`(신규 저장) 모두 이미 구현되어 있다.

```python
# 병합 — 대상이 여러 건이면 라우터가 자기 자신으로 되돌려 하나씩 처리한다
memory_id = state.memory_ids_to_update.pop(0)
prior = self.memory.get_document(memory_id)
merged = self._ask(
    "기존 버그 패턴 기록에 새 사례를 합치십시오.\n"
    f"[기존 기록] {prior}\n"
    f"[새 사례] {state.memory_query or state.bug_report}\n\n"
    f"형식: {MEMORY_FORMAT}\n"
    "두 사례를 아우르는 하나의 기록으로, 다른 말 없이 이 형식만 출력하십시오."
)
count = self.memory.merge(memory_id, merged, state.language, Path(state.target_file).name)
```

## 확인한 것 — 두 갈래의 분기 지점

`memory_generation_router`(`agent.py:498`)가 `memory_ids_to_update`의 유무로 갈린다.
10단계에서 "새 패턴으로 남아야 한다"고 확인한 요건은 이 라우터로 이미 충족돼 있다.

| 조건 | 가는 곳 | 결과 |
|---|---|---|
| 병합 대상 있음 (거리 < 임계값) | `memory_modification_node` | 기존 기록에 누적 + `occurrences` 1 증가 → `🧠 기존 패턴에 병합 → 누적 N회 발생` |
| 병합 대상 없음 | `memory_generation_node` | `occurrences=1`로 새 패턴 저장 → `💾 새 패턴으로 저장 (1회 발생)` |

병합 대상이 여러 건이면 `memory_update_router`(`agent.py:507`)가 `memory_modification_node`를
자기 자신으로 되돌려 `pop(0)`으로 하나씩 소진하고, 다 비면 `code_update_node`로 넘어간다.
LangGraph의 조건부 엣지로 루프를 만드는 방식이고, 목록이 매번 줄어들므로 종료가 보장된다.

## 확인한 것 — 덮어쓰기가 아니라 누적

`retriever.py merge`는 문서를 교체하지만, 그 문서 내용이 LLM 이 **두 사례를 아우르도록
합친 결과**다. 그리고 metadata 는 덮어쓰지 않고 누적한다:

- `occurrences` +1 — "이 버그가 N번째다"를 말할 수 있게 하는 값. 원본 노트북이 못 했던 것.
- `first_seen` 유지 / `last_seen` 갱신
- `language_primary`는 **최초 관측 언어를 유지**해 격리 기준을 안정시킨다
- `languages`·`targets`는 콤마 CSV 로 중복 없이 추가 (Chroma metadata 가 스칼라만 담기 때문)

## 결정 — 병합 직전 기록을 `.heal/memory_history.jsonl` 에 남긴다

지금 `merge()`는 `collection.update()`로 기존 문서를 교체하면서 **교체 전 문서를 아무
데도 남기지 않는다.** LLM 이 두 사례를 합칠 때 한쪽의 핵심 정보를 빠뜨리면 그 패턴
기록은 영구 손실이다. 이 프로젝트가 코드에는 "승인 전까지 원본 불변"(G6)을 걸고 백업까지
만드는데 메모리에는 같은 보호가 없는 비대칭을 메운다.

- 0단계에서 세운 `.heal/history.json` 관례와 같은 위치·같은 발상이라 새 개념이 없다.
- 부수 효과로 **요약 손실을 관측할 자료**가 생긴다. N번째 병합은 이미 N-1번 압축된 한 줄을
  다시 압축하므로, 반복될수록 기록이 두꺼워지는 게 아니라 일반적이고 정보 없는 문장으로
  수렴할 가능성이 있다. 로그가 있으면 1→2→3회차 문서를 나란히 비교해 확인할 수 있다.
- **요약 손실 대책 자체는 지금 만들지 않는다.** 실재하는지 관측한 뒤에 정한다. 근거 없이
  프롬프트를 고치면 0.3 임계값처럼 검증되지 않은 값이 하나 더 생긴다. 관측 시점은
  `--merge-threshold` 검증과 같다 — 자격증명을 넣고 평가를 온라인 실행할 때
  py-index → py-dict 순으로 돌려 로그를 비교한다.

| # | 할 일 | 파일 |
|---|---|---|
| P11-1 | `merge()`가 `collection.update()` 호출 전에 교체될 문서·metadata·시각·memory_id 를 `.heal/memory_history.jsonl` 에 한 줄(append-only) 기록 | `src/retriever.py` |
| P11-2 | 9단계 결정에 따라 `memory_generation_node`의 `self.memory.enabled` 조건 제거 | `src/agent.py` |

## 결론 — 나머지는 새 코드가 필요 없다

병합·신규 저장의 기존 동작 자체는 설계와 정확히 일치한다. 위 두 항목만 추가·정리한다.

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 신규 저장 | 빈 메모리로 `./run.sh data/samples/py-index` | `💾 새 패턴으로 저장 (1회 발생)`, `--stats`에 1회로 표시 |
| 병합 + 카운터 | 위 실행 후 같은 계열 `./run.sh data/samples/py-dict` | `🧠 기존 패턴에 병합 → 누적 2회 발생` |
| 다건 병합 루프 | 임계값 안에 2건 이상 들어오는 상태로 실행 | `🧠 ...` 로그가 건수만큼 반복되고 무한 루프 없이 `code_update_node`로 진행 |
| 언어 격리 안정성 | Python 패턴에 `--cross-language`로 JS 사례를 병합 | `language_primary`는 `python` 유지, `languages`는 `python,javascript` |
| 누적 리포트 | `./run.sh --stats` | 발생 횟수 내림차순으로 패턴 목록 출력 |
| 회귀 | `python -m pytest tests/ -q` | 기존 20건 전부 통과 |
