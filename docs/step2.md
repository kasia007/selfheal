# 2단계 — 언어 감지 (S2)

`selfheal/adapters.py:150 detect_language`:

- 1순위: `ADAPTERS` 의 각 어댑터 `markers`(예: `pyproject.toml`, `package.json`, `go.mod`)
  로 찾는다.
- 2순위: 마커가 없으면 `iter_source_files` 로 소스 파일을 훑어 확장자 최빈값으로 정한다.
- 둘 다 실패하면 `LanguageDetectionError` — 추측하지 않는다.

`heal.py:162-172` 가 이미 화면 출력까지 구현되어 있다:

```
🔍 언어 감지: python (pyproject.toml)      # 마커로 찾음
🔍 언어 감지: python (확장자 최빈값)         # 마커 없어서 확장자로 찾음
🔍 언어 감지: python (--lang 지정)          # 사용자가 --lang 으로 override
❌ <workdir> 에서 언어를 특정하지 못했습니다  # exit 3
```

**1단계(파일 입력)와의 연결점** — 1단계에서 파일을 입력하면 "상위로 올라가 마커를
찾은 폴더" 를 테스트 루트로 정하는데, `detect_language` 는 `workdir` 하나만 보고
판단하므로, 1단계가 이미 올바른 루트를 `workdir` 로 넘겨주기만 하면 `detect_language`
는 **코드 변경 없이 그대로 재사용**된다. 즉 2단계는 1단계가 찾아준 루트를 그대로
받아쓰는 소비자다.

## 결론 — 새 코드가 필요 없다

기존 동작이 설계와 정확히 일치한다.

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 마커로 감지 | `heal.py ./samples/py-index` | `🔍 언어 감지: python (pyproject.toml)` |
| 확장자 최빈값 | 마커 없이 `.py` 파일만 있는 임시 폴더 | `🔍 언어 감지: python (확장자 최빈값)` |
| 감지 실패 | 소스 파일이 하나도 없는 폴더 | exit 3, LLM 호출 0건 |
| `--lang` override | `heal.py ./samples/py-index --lang python` | `🔍 언어 감지: python (--lang 지정)` |
| 1단계 연결 | `heal.py ./samples/py-index/boundary.py` (파일 입력) | 1단계가 찾은 루트 기준으로 같은 감지 결과 |

## 참고 — 확장자 최빈값 경로는 폴더 입력에서만 발생한다

`detect_language` 의 2순위(확장자 최빈값)는 **폴더 입력에서만** 실행됩니다. 파일
입력에서는 1단계 규칙("상위로 올라가며 마커를 찾다가 못 찾으면 exit 3")이 먼저
적용되어, 마커가 없으면 언어 감지 단계까지 가지도 않고 그 자리에서 종료됩니다. 즉
확장자 최빈값 경로는 파일 입력에서는 도달 불가능하고, 폴더를 직접 지정했을 때만
쓰입니다.
