# 10단계 — 메모리 필터 (S10)

`src/agent.py:361 memory_filter_node` 에 이미 구현되어 있다 — LLM 을 쓰지 않는 순수
필터링 노드다. 9단계에서 찾은 후보 중 **충분히 가까운 것만** "같은 패턴"으로 인정해
병합 대상에 올린다.

```python
state.memory_ids_to_update = [
    h.id for h in state.memory_hits if h.distance < self.memory.merge_threshold
]
if state.memory_ids_to_update:
    self.console.detail(
        f"→ {len(state.memory_ids_to_update)}건이 기존 패턴과 동일 (병합 대상)"
    )
```

## 확인한 것 — 기준이 되는 `merge_threshold`

기준값은 `retriever.py DEFAULT_MERGE_THRESHOLD = 0.3` (CLI `--merge-threshold`로 조정
가능). 코사인 거리가 이 값보다 작으면 "같은 버그"로 보고 11단계(메모리 병합)에서
누적하고, 그렇지 않으면 그냥 참고만 하고 새 패턴으로 남는다(추후 저장 단계에서 `add`).

이 값은 하드코딩해 두면 안 되는 실험 파라미터라고 이미 `retriever.py` 주석에 명시돼
있다 — 너무 빡빡하면 같은 버그가 별개 패턴으로 쪼개지고, 너무 느슨하면 무관한 버그가
뭉쳐 발생 횟수가 부풀려진다. 9단계에서 `--no-memory`를 없앤 것과는 별개로, 이 파라미터
자체는 그대로 CLI 옵션으로 남긴다 — "메모리를 끄는 것"과 "메모리 안에서 병합 민감도를
조절하는 것"은 다른 종류의 옵션이라 이번 결정의 영향을 받지 않는다.

## 결론 — 새 코드가 필요 없다

기존 동작이 설계와 정확히 일치한다. `memory_hits`가 이미 언어 격리된 상태로 넘어오므로
(9단계), 이 노드는 거리 기준 필터링 하나에만 집중하면 된다.

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 병합 대상 판정 | 유사도 0.3 미만 사례가 있는 상태로 실행 | `→ N건이 기존 패턴과 동일 (병합 대상)` 표시 |
| 병합 대상 없음 | 유사도 0.3 이상 사례만 있는 상태로 실행 | 위 로그 안 뜨고 바로 다음 노드로 진행 |
| 임계값 조정 | `--merge-threshold 0.1` 로 실행 | 같은 사례라도 병합 대상에서 빠짐 (더 엄격) |
| 회귀 | `python -m pytest tests/ -q` | 기존 20건 전부 통과 |
