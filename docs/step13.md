# 13단계 — 패치 적용 (S13, 샌드박스에만)

`src/agent.py:458 code_patching_node` 에 이미 구현되어 있다. LLM 을 쓰지 않고 파일에
쓰기만 하는 노드다. **여기서 쓰는 대상은 언제나 샌드박스 사본이고, 원본이 아니다** —
5단계에서 `mkdtemp` + `copytree` 로 만든 사본 안의 경로가 `state.target_file` 이다.

```python
if state.dry_run:
    self.console.step("🧪", "dry-run: 파일을 수정하지 않습니다.")
    state.status = "failed" if state.error else state.status
    return state

Path(state.target_file).write_text(state.new_source, encoding="utf-8")
state.current_source = state.new_source
```

## 확인한 것 — 왜 `exec` 가 아니라 파일 쓰기인가

원본 노트북은 `exec` 로 런타임의 함수를 바꿔치기했다. 이 프로젝트가 파일 쓰기로 바꾼
이유는 두 가지다(README 1절 #2·#3b).

- **언어에 무관해진다.** `exec` 는 파이썬 전용이고, 파일 쓰기는 어댑터만 있으면 어떤
  언어든 된다.
- **검증이 진짜 빌드·테스트가 된다.** 우리 프로세스 안에서 LLM 이 만든 코드를 실행하지
  않고, 서브프로세스의 테스트로만 확인하므로 샌드박싱도 부수적으로 해결된다.

## 확인한 것 — 화면 출력과 원본 반영은 이 노드가 하지 않는다

`code_patching_node` 는 **아무것도 출력하지 않는다**(dry-run 안내 제외). diff 생성·표시와
`.heal/` 산출물 저장, `--apply` 의 원본 반영은 모두 그래프가 끝난 뒤 CLI(`main()`)가 한다
— `make_diff`(`agent.py:917`) → `render_result`(`agent.py:931`) → 산출물 저장
(`agent.py:937`). 이 순서여야 하는 이유는 `agent.py:857` 주석에 있다: **diff 를 낼 때
이미 "이 패치는 테스트를 통과한다" 가 검증된 상태**여야 한다.

그래서 이 노드 다음은 END 가 아니라 6단계로 돌아간다
(`builder.add_edge("code_patching_node", "test_execution_node")`, `agent.py:579`).
"고쳤다고 믿지 않고 반드시 다시 테스트한다 — 판정 기준은 언제나 테스트다."

| 흐름 | 결과 |
|---|---|
| 재테스트 통과 | `error_router` → `done` → END, 상태는 성공 |
| 재테스트 실패, 시도 남음 | `locate_node` 로 돌아가 12→13단계 반복 |
| 재테스트 실패, 시도 소진 | `exhausted` → END (G5). 실패해도 원본은 불변 |

## 확인한 것 — `--dry-run` 은 기본 동작의 별칭이다

기본 동작(`--apply` 없음)이 이미 원본을 건드리지 않으므로 결과가 같다.
`test_queries.csv` 13번 케이스가 이를 G8 로 명시해 두었다. (아래 "정정" 절 참고 — 이
별칭 관계는 의도된 설계이기도 하고, 실제 구현 상태이기도 했다.)

## 결정 — 원본의 줄바꿈을 보존한다

소스를 읽을 때 `read_text(encoding="utf-8")`, 쓸 때 `write_text(...)` 를 쓰는데 Python 의
기본 텍스트 모드는 읽을 때 CRLF 를 LF 로 바꾸고 쓸 때 LF 로 쓴다. 즉 **원본이 CRLF 인
파일은 파일 전체의 줄바꿈이 바뀐다.** 12단계에서 파일 전체를 재생성하기 때문에 한 줄도
예외가 없다.

- diff 가 "모든 줄이 변경됨" 으로 나와 검수(G6)가 사실상 불가능해진다 — README 4절이
  "전체 소스가 아니라 diff 여야 리뷰가 된다" 고 강조한 것과 정면으로 충돌한다.
- `--apply` 하면 원본 파일 전체의 줄바꿈이 바뀐다.
- 개발·실행 환경이 Windows 라 실제로 마주칠 가능성이 높다.

**최초 1회만 감지해야 한다.** 재시도 2회차에는 샌드박스 파일이 이미 LF 로 덮여 있어 그때
감지하면 늦는다. LLM 프롬프트에는 지금처럼 LF 로 정규화된 텍스트를 주고, 복원은 쓰기
시점에만 한다 — 모델이 줄바꿈 문자를 신경 쓸 이유가 없다. 인코딩은 utf-8 고정을 유지한다
(다중 인코딩까지 다루면 범위가 커지고 검증되지 않은 추측이 늘어난다).

## 정정 — dry-run 분기는 애초에 죽은 코드였다

이 문서는 처음에 "`state.status = "failed"` 를 세팅해 7단계 `locate_router` 가 흐름을
끊는다" 고 적었고, 그것을 명시적 라우터로 바꾸자고 제안했다. **구현하려고 확인해 보니
사실이 아니었다.**

`state.dry_run` 을 **설정하는 코드가 어디에도 없었다.** CLI 의 `args.dry_run` 을 `State` 로
넘기지 않으므로 그 필드는 항상 기본값 `False` 였고, `code_patching_node` 의 dry-run 분기는
**한 번도 실행된 적이 없다.** `🧪 dry-run: 파일을 수정하지 않습니다.` 로그도 출력된 적이
없다.

그래서 `--dry-run` 은 자기 도움말이 말한 그대로 **기본 동작의 완전한 별칭**이었다. 기본
동작도 원본을 건드리지 않으므로(G6) 결과가 같고, G8 케이스가 통과해 온 이유도 이것이다.

**라우터를 새로 만들 대상이 없으므로, 죽은 분기와 `State.dry_run` 필드를 제거했다.**
`--dry-run` 플래그 자체는 남긴다 — CI 스크립트에서 "쓰지 않는다" 는 의도를 드러내는
용도이고, 도움말에 아무 동작도 바꾸지 않는 별칭임을 명시했다.

이것이 P13-3 을 대체한다.

| # | 할 일 | 파일 |
|---|---|---|
| P13-1 | 원본 줄바꿈(`\r\n`/`\n`)과 마지막 줄 개행 유무를 최초 1회 감지해 `State` 에 담기 | `src/state.py`, `src/agent.py` |
| P13-2 | `code_patching_node` 이 `write_text` 대신 `open(..., "w", newline=state.newline)` 으로 써서 줄바꿈을 복원 | `src/agent.py` |
| P13-3 | (대체됨) 죽은 dry-run 분기와 `State.dry_run` 필드 제거. `--dry-run` 은 아무 동작도 바꾸지 않는 별칭임을 도움말에 명시 | `src/agent.py`, `src/state.py` |

## 결론 — 나머지는 새 코드가 필요 없다

샌드박스 쓰기·재테스트 루프·dry-run 의 동작 자체는 설계와 일치한다. 위 세 항목만 고친다.

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 샌드박스에만 쓰기 | `./run.sh data/samples/py-index` 실행 후 `git status --short data/` | 원본 소스 변경 0건, exit 4 |
| 재테스트 루프 | 1회로 못 고치는 샘플로 실행 | `🩹 패치 제안 [시도 2/3]` 처럼 12→13→6단계가 반복됨 |
| 시도 소진 | `--max-attempts 2` + 절대 고쳐지지 않는 가짜 모델 | exit 1, 시도 정확히 2회, 원본 불변 (G5) |
| 승인 후 반영 | `./run.sh data/samples/py-index --apply` | exit 0, 원본 파일 실제 변경 (G6) |
| dry-run | `./run.sh data/samples/py-index --dry-run` | `🧪 dry-run: 파일을 수정하지 않습니다.`, 어떤 파일도 쓰지 않음 (G8) |
| 테스트 파일 불변 | 위 모든 케이스 후 테스트 파일 확인 | `test_boundary.py` 불변 (G1) |
| 줄바꿈 보존 (P13-1·2) | CRLF 로 저장된 소스 파일로 실행 후 diff 확인 | 실제로 고친 줄만 diff 에 나오고, 나머지 줄은 변경으로 뜨지 않음. `--apply` 후에도 파일이 CRLF 유지 |
| dry-run 별칭 (P13-3) | `./run.sh data/samples/py-index --dry-run` | 기본 동작과 완전히 동일 — exit 4 · 시도 1회 · 원본 불변 (G8) |
| 회귀 | `python -m pytest tests/ -q` | 기존 20건 전부 통과 |
