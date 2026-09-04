# 14단계 — 판정 · 결과 출력 · 승인 반영 (S14, 그래프 밖)

여기부터는 **노드가 아니다.** 그래프가 END 에 도달한 뒤 `src/agent.py:906-954` 의 CLI 가
한다. 13단계까지가 "샌드박스에서 고치고 검증하는 일" 이었고, 14단계는 "그 결과를 사용자
세계로 내보내는 일" 이다.

```python
if state.status == "running":
    state.status = "failed" if state.error else "fixed"

if state.status == "fixed" and state.target_file:
    patched = Path(state.target_file)
    new_source = patched.read_text(encoding="utf-8")
    rel = patched.relative_to(sandbox.resolve())
    diff = make_diff(state.original_source, new_source, str(rel))

    if args.apply:
        target = workdir / rel            # 승인받은 경우에만 원본에 씁니다
        target.write_text(new_source, encoding="utf-8")
        applied_to = target
    else:
        state.status = "proposed"         # 기본 동작: 제안만 하고 판단을 기다립니다

    state.target_file = str(workdir / rel)  # 리포트에는 사용자가 아는 경로를 씁니다
```

## 순서와 그 이유

| # | 하는 일 | 왜 이 순서인가 |
|---|---|---|
| 1 | 상태 확정 (`running` → `fixed`/`failed`) | 그래프가 끝났는데 상태가 미정인 경우를 여기서 닫는다 |
| 2 | diff 생성 (`make_diff`) | **이 시점에 이미 "이 패치는 테스트를 통과한다" 가 검증돼 있다**(`agent.py:857`). 그래서 사용자가 보는 diff 는 항상 검증된 패치다 |
| 3 | `--apply` 면 원본에 쓰기, 없으면 `proposed` | 원본에 쓰는 것은 **맨 마지막 한 번뿐**이다 (G6) |
| 4 | 경로 치환 (사본 → 원본) | 리포트에 임시 폴더 경로가 나가면 사용자가 못 쓴다 |
| 5 | 화면 출력 (`render_result`) | 진행 로그 + diff. 전체 소스가 아니라 diff 여야 리뷰가 된다 |
| 6 | 산출물 저장 (`write_artifacts`) | **대상 디렉터리의 `.heal/`** 에 남긴다 — 검수할 diff 가 그 폴더에 있어야 하기 때문 |
| 7 | 샌드박스 삭제 (`finally`) | 성공·실패 무관하게 임시물을 남기지 않는다 |
| 8 | exit code 반환 (`STATUS_TO_EXIT`) | 0 / 1 / 2 / 3 / 4 로 CI 가 분기할 수 있게 |

## 확인한 것 — "원본 복원" 은 실제로 삭제로 해결된다

`code_patching_node` 의 docstring 은 "최종 실패 시 **호출자가 복원합니다**" 라고 적고
있지만, CLI 에는 복원 코드가 없다. **필요가 없기 때문이다** — 쓰기는 전부 샌드박스에서만
일어났으므로 `shutil.rmtree(sandbox_root)` 로 사본을 버리는 것이 곧 복원이다. 원본은
애초에 한 번도 바뀌지 않았다. 5단계의 샌드박스 설계가 복원 로직 자체를 없앤 셈이다.

주석 표현만 실제 구조와 어긋나 있어 오해를 부른다.

## 확인한 것 — 승인은 별개의 실행이다

한 실행 안에서 `y/N` 을 묻지 않는다. 기본 동작은 exit 4 로 끝나고, 사용자가 diff 를 검수한
뒤 **같은 명령에 `--apply` 를 붙여 다시 실행**하는 것이 승인이다. 물어보면 CI 가 멈추기
때문이다(1단계의 비대화형 원칙과 같은 이유).

## 결정 — 백업과 적용 이력은 만들지 않는다

처음에는 `--apply` 가 이력(`.heal/history.json`)도 백업(`<대상>/.heal/backup/`)도 남기지
않아 0단계의 원복 기능이 동작할 수 없다는 것을 결함으로 적었다. **논의 끝에 결함이 아니라
불필요한 기능이라고 결론 내렸다** — `--apply` 는 사용자가 승인한 변경이고, 되돌리려면
git 이 있다. 자세한 사유는 `step0.md` 에 있다.

따라서 **P14-1(백업)·P14-2(적용 이력)는 폐기**하고, 이미 구현돼 있던 원복 코드
(`_load_history` · `_attempt_revert` · `HISTORY_PATH`)도 제거했다.

## 발견 — 고쳐야 할 것

**1. `--apply` 쓰기에 줄바꿈 문제가 있다.** `target.write_text(new_source,
encoding="utf-8")` 는 13단계 P13-2 와 같은 문제다. 원본이 CRLF 면 승인 시점에 파일 전체의
줄바꿈이 바뀐다.

**2. 다른 저장소를 대상으로 하면 `.heal/` 이 그 저장소에 남는다.** 이 저장소는
`.gitignore` 에 `.heal/` 이 있어 문제가 없지만, 사용자가 자기 프로젝트를 대상으로 실행하면
추적되지 않은 파일이 생긴다.

| # | 할 일 | 파일 |
|---|---|---|
| P14-1 | (폐기) 백업 — `step0.md` 참고 | — |
| P14-2 | (폐기) 적용 이력 — `step0.md` 참고 | — |
| P14-3 | `--apply` 쓰기도 P13-1 에서 감지한 줄바꿈으로 복원해 쓰기 | `src/agent.py` |
| P14-4 | `code_patching_node` docstring 의 "호출자가 복원합니다" 를 실제 구조(사본 삭제로 해결됨)에 맞게 수정 | `src/agent.py` |
| P14-5 | 산출물을 처음 만들 때 대상 폴더가 git 저장소이고 `.heal/` 이 무시되지 않으면 안내 한 줄 표시 (`.gitignore` 에 추가하십시오) | `src/agent.py` 또는 `src/report.py` |

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 제안 흐름 | `./run.sh data/samples/py-index` | 화면에 diff, exit 4, `.heal/patch.diff`·`report.json`·`trace.log` 생성, 원본 불변 |
| 승인 흐름 | 이어서 `--apply` 로 재실행 | exit 0, 원본 파일 실제 변경 |
| 되돌리기 | `--apply` 후 `git checkout -- <파일>` | 원본으로 복구됨 (에이전트가 아니라 git 이 한다) |
| 줄바꿈 (P14-3) | CRLF 소스로 `--apply` | 고친 줄만 바뀌고 파일은 CRLF 유지 |
| 샌드박스 정리 | 성공·실패 각각 실행 후 OS 임시 폴더 확인 | `selfheal-*` 잔여물 없음 |
| exit code | 5가지 경우 각각 실행 | 0 / 1 / 2 / 3 / 4 가 규약대로 반환 |
| 회귀 | `python -m pytest tests/ -q` | 기존 20건 전부 통과 |
