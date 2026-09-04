# 3단계 — 툴체인 확인 (S3)

`selfheal/adapters.py:210 preflight`:

- `adapter.probe_cmd`(예: `python --version`)의 실행 파일이 있는지 `shutil.which` 로
  먼저 확인
- 있으면 실제로 실행해 보고, 실패하면 exit 3
- **LLM 호출 전**에 이 검사를 통과해야만 다음 단계로 감

`heal.py:174-181` 화면 출력도 구현되어 있다:

```
🔍 언어 감지: python (pyproject.toml)
   툴체인: Python 3.14.7          # preflight 통과 후
❌ 'python' 실행 파일을 찾지 못했습니다. ...  # exit 3
```

2단계와 마찬가지로 1단계·2단계가 넘겨주는 `workdir`/`adapter` 를 그대로 받아쓰는
소비자다. 새 코드가 필요 없다.

## 결정 — 지연 안내는 하지 않는다

`probe_cmd` 실행에 최대 30초 타임아웃이 걸려 있다(`adapters.py:221`). 하지만 실제로는
거의 항상 1초 이내로 끝나는 검사이고, 30초는 극단적인 경우의 안전망이다. 평소에 경고를
띄우면 오히려 불필요한 신호가 되므로 **화면에는 지연 안내를 추가하지 않는다.**

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 정상 통과 | `heal.py ./samples/py-index` | `툴체인: Python 3.14.7` 출력, 다음 단계로 진행 |
| 실행 파일 없음 | `probe_cmd` 실행 파일을 PATH 에서 제거하고 실행 | exit 3, LLM 호출 0건 |
| 실행은 되지만 실패 | `probe_cmd` 가 0이 아닌 종료 코드를 내도록 조작 | exit 3, 사유 메시지 표시 |
