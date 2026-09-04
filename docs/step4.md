# 4단계 — 스캔 집계 (S4)

2·3단계와 달리 이 화면은 지금 코드에 없다. 현재는 `has_tests` 로 테스트 존재 여부만
boolean 으로 확인하고(`heal.py:185`) 곧장 샌드박스로 넘어간다.

## 재사용할 것

| 함수 | 위치 | 무엇을 하나 |
|---|---|---|
| `iter_source_files(workdir)` | `adapters.py:140` | 제외 경로(`EXCLUDED_DIRS`)를 뺀 파일 전체 순회 |
| `adapter.is_source_file(path)` | `adapters.py:65` | 확장자 화이트리스트 통과 + 테스트 파일 아님 |
| `adapter.is_test_file(path)` | `adapters.py:61` | 테스트 파일 패턴 매칭 |
| `has_tests(adapter, workdir)` | `adapters.py:180` | 지금 이미 같은 순회를 한 번 돈다 |

새 함수 `count_fixable_sources(adapter, workdir)` 가 이 세 개를 **한 번의 순회로 묶어**
고칠 수 있는 소스 개수를 반환하고, `has_tests` 판정도 같은 순회에서 겸한다.

## 결정 — 화면 문구 (최종)

```
총 N개 파일에 대해서 테스트 시작하겠습니다.
```

- `N` = **고칠 수 있는 소스 파일 개수** (`is_source_file` 기준, 폴더 전체 스캔).
  - 폴더 입력 → 폴더 전체에서 센 값
  - 파일 입력 → **지정 파일 1개** (1단계 결정: 파일 입력이면 소스 개수는 사용자가
    좁힌 범위를 반영)
- "테스트 M개(수정 안 함)" 표시는 뺀다. 이 한 줄이 개수 안내와 "이제부터 테스트를
  실행한다" 는 다음 동작(테스트 실행 단계)의 예고를 겸한다.
- "전체 파일 갯수" 라는 별도 분모는 두지 않는다. 필요해지면(예: 착수 확인 게이트에서
  "고칠 수 있는 소스 N개 / 전체 M개" 형태로) 그때 `iter_source_files` 전체 개수를
  별도로 센다 — `iter_source_files` 기준이며 `EXCLUDED_DIRS` 는 제외하고 그 안의
  모든 파일(소스·테스트·설정 파일 등)을 센다.

## 할 일 (계획이 전부 완료된 뒤 구현)

| # | 할 일 | 파일 |
|---|---|---|
| P4-1 | `count_fixable_sources(adapter, workdir)` — 고칠 수 있는 소스 개수를 한 번의 순회로 반환 (`has_tests` 판정도 같은 순회에서 겸함). 파일 입력이면 1로 override | `selfheal/adapters.py` |
| P4-2 | `has_tests` 호출을 위 함수 결과로 대체 (순회 중복 제거) | `heal.py` |
| P4-3 | S4 화면 출력 — `총 N개 파일에 대해서 테스트 시작하겠습니다.` | `heal.py` |

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 폴더 입력 집계 | `heal.py ./samples/py-index` | `총 N개 파일에 대해서 테스트 시작하겠습니다.` — N이 실제 고칠 수 있는 소스 개수와 일치 |
| 파일 입력 집계 | `heal.py ./samples/py-index/boundary.py` | `총 1개 파일에 대해서 테스트 시작하겠습니다.` |
| 테스트 없음 | 소스만 있고 테스트 파일이 없는 폴더 | exit 3, 기존 메시지 유지 |
| 순회 중복 제거 확인 | 코드 리뷰 | `iter_source_files` 가 한 실행에 한 번만 전체 순회됨 |
