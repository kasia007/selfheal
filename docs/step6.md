# 6단계 — 테스트 실행 (S6)

`selfheal/nodes.py:113 test_execution_node` 에 이미 구현되어 있다.

- 서브프로세스로 테스트를 실행한다 (`subprocess.run`). **LLM 을 쓰지 않는다.**
- 통과하면 `state.status = "nothing_to_fix"` → `report.py:24 EXIT_NOTHING_TO_FIX` (exit 2)
- 실패하면 마지막 줄을 요약해 출력한다:

```
🧪 테스트 실행 ... (python -m pytest -q)
   FAIL — 3 failed, 1 passed
```

이 노드는 초기 실행(시도 0회)과 이후 재시도 검증(S11)에서 **같은 함수를 재사용**한다.
`state.attempts > 0` 여부로 로그를 남길지만 구분한다.

## 확인한 것 — 타임아웃

`TEST_TIMEOUT_SEC = 180`(`nodes.py:29`) 으로 고정되어 있다. **그대로 둔다.** 지금
샘플 규모(파일 1~수 개)에서는 충분하고, 바꿀 이유가 생기면 그때 옵션화한다.

## 결론 — 새 코드가 필요 없다

기존 동작이 설계와 정확히 일치한다.

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 실패 감지 | `heal.py ./samples/py-index` | `🧪 테스트 실행 ... FAIL — N failed, M passed` |
| 통과 시 종료 | 이미 통과하는 폴더로 실행 | exit 2, LLM 호출 0건 |
| 타임아웃 | 무한 루프 테스트로 실행 (수동 확인용, 회귀 테스트 아님) | 180초 뒤 실패로 처리, 프로세스 종료 |
