# 7단계 — 수정 대상 추론 (S7, 찾기·표시만)

**범위: 찾기 + 화면 표시까지만.** 여러 파일을 실제로 순서대로 고치는 루프(상태에
`targets` · `completed` · `current_index` 추가, `attempts` 파일별 리셋 등)는 범위가
크므로 별도 단계로 미룬다.

## 조사 결과

- **이미 있음**: `selfheal/adapters.py:230 locate_targets` — 스택트레이스에서 후보를
  **전부** 규칙 기반으로 뽑는다. G1(테스트 아님) · G2(workdir 안) · G3(제외 경로) 필터
  포함, LLM 안 씀. 안쪽 프레임이 먼저 오도록 정렬된다.
- **아직 안 됨**: `selfheal/nodes.py:167 locate_node` 는 여전히 **단수** `locate_target`
  을 호출해 `state.target_file` 하나에 고정한다(`nodes.py:176`). 그래서 화면에
  `📍 수정 대상 추론: boundary.py:11` 하나만 나오고 나머지 후보는 버려진다.

## 바꿀 것 — 전부 찾아서 개수만 보여주고, 처리는 지금처럼 첫 번째만

**1단계와의 연결점 — 사용자가 파일을 지정했을 때는 그 파일이 우선이다.** 1단계 결정에
따르면 "파일을 줬는데 스택트레이스가 다른 파일을 가리키면 사용자 입력을 따른다." 1단계가
지정 파일을 `State.target_file` 에 미리 채워 넘기므로(`step1.md` P1-5), `locate_node` 의
기존 첫 줄(`if state.target_file: ... return state`)이 **이미 이 값을 그대로 존중한다** —
사용자가 지정한 파일이 있으면 `locate_targets` 로 고르지 않고 곧장 그 파일을 쓴다.
다만 스택트레이스와 다르다는 사실은 알려야 하므로, 이 경우에도 `locate_targets` 를
**비교·안내용으로 한 번 호출**한다.

```python
if state.target_file:
    # 1단계에서 파일을 지정한 경우 — 그 값이 우선이다. 재시도 중 고정값 재사용도 같은 경로.
    path = Path(state.target_file)
    targets = locate_targets(self.adapter, state.test_output, workdir)
    if targets and targets[0][0] != path:
        other_rel = targets[0][0].relative_to(workdir)
        self.console.detail(
            f"스택트레이스: {other_rel}:{targets[0][1]} (지정 파일과 다름)"
        )
    state.current_source = path.read_text(encoding="utf-8")
    return state

targets = locate_targets(self.adapter, state.test_output, workdir)
if not targets:
    path = self._locate_with_llm(state, workdir)   # 기존 LLM 폴백 그대로
    line_no = None
else:
    path, line_no = targets[0]                      # 폴더 입력 — 가장 안쪽 프레임을 우선 처리

...
if len(targets) > 1:
    others = ", ".join(f"{p.relative_to(workdir)}:{n}" for p, n in targets[1:])
    self.console.step(
        "📍", f"수정 대상 {len(targets)}개: {loc}, {others} (이번 실행은 1번만 처리)"
    )
else:
    self.console.step("📍", f"수정 대상 추론: {loc}")
```

- **상태(`state`)는 새 필드를 추가하지 않는다.** `target_file` 하나가 "재시도 중
  고정값"과 "사용자가 애초에 지정한 파일" 두 역할을 겸한다 — 1단계가 그래프 진입 전에
  미리 채워 넣으면 `locate_node` 입장에서는 둘을 구분할 필요가 없다.
- 폴더 입력일 때만 `locate_targets` 의 결과(`targets[0]`)로 실제 대상을 고른다. 나머지
  후보는 로그 표시에만 쓰고 버린다 — 다음 단계(다중 파일 루프)에서 이 값을 어떻게 들고
  갈지 다시 설계한다.
- **LLM 폴백 경로는 그대로 단일 결과만 낸다.** `_locate_with_llm` 은 규칙 기반이 완전히
  실패했을 때만 쓰는 보루라서, 지금 범위에서 복수 결과로 확장하지 않는다.

## 할 일 (계획이 전부 완료된 뒤 구현)

| # | 할 일 | 파일 |
|---|---|---|
| P7-1 | `locate_node` 이 `locate_targets`(복수) 를 호출하도록 교체. 폴더 입력이면 실제 처리는 지금처럼 첫 번째 결과만 | `selfheal/nodes.py` |
| P7-2 | 후보가 2개 이상이면 `📍 수정 대상 N개: ...(이번 실행은 1번만 처리)` 로 표시 | `selfheal/nodes.py` |
| P7-3 | `state.target_file` 이 이미 채워져 있으면(1단계 지정 파일) 그 값을 우선하고, `locate_targets` 결과와 다르면 `스택트레이스: ... (지정 파일과 다름)` 안내만 표시 | `selfheal/nodes.py` |

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 단일 대상 | `heal.py ./samples/py-index` (원인 파일 1개) | 지금과 동일하게 `📍 수정 대상 추론: boundary.py:11` |
| 복수 대상 | 소스 2개가 동시에 깨진 임시 샘플 | `📍 수정 대상 2개: a.py:N, b.py:M (이번 실행은 1번만 처리)`, 실제로는 1개만 진행 |
| 지정 파일 우선 | `boundary.py` 지정, 스택트레이스는 `helper.py:4` 가 가장 안쪽 | 실제 처리 대상은 `boundary.py`, 화면에 `스택트레이스: helper.py:4 (지정 파일과 다름)` 안내 |
| LLM 폴백 | 정규식이 못 찾는 출력으로 실행 | 기존과 동일하게 LLM 폴백 1건만 사용 |
| 회귀 | `.venv/Scripts/python -m pytest tests/ -q` | 기존 20건 전부 통과 |
