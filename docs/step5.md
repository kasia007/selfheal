# 5단계 — 샌드박스 준비 (S5)

`heal.py:194-201` 에 이미 구현되어 있다 — `mkdtemp` + `copytree` 로 원본을 임시 폴더에
복사하고, 이후 모든 작업(테스트 실행 · 수정)은 이 사본에서만 일어난다.

```python
sandbox_root = Path(tempfile.mkdtemp(prefix="selfheal-"))
sandbox = sandbox_root / workdir.name
shutil.copytree(
    workdir,
    sandbox,
    ignore=shutil.ignore_patterns(".heal", "__pycache__", ".pytest_cache", ".git"),
)
console.step("📦", "샌드박스 사본에서 작업합니다 (원본 불변)")
```

## 발견 — 복사 범위와 스캔 범위의 불일치

`iter_source_files`(`adapters.py:140`)와 `EXCLUDED_DIRS`(`node_modules` · `vendor` ·
`site-packages` · `venv` · `dist` · `build`)는 4단계 스캔·이후 모든 판단에서 이 경로들을
제외한다. 하지만 `copytree` 의 `ignore_patterns` 에는 `EXCLUDED_DIRS` 가 빠져 있어서,
`node_modules` 가 있는 JS 프로젝트라면 매번 수백MB를 복사하게 된다. 스캔에서는 안 보는
경로를 복사에서는 그대로 끌고 가는 불일치다.

## 결정 — 복사 시에도 EXCLUDED_DIRS 를 제외한다

`ignore_patterns` 목록에 `EXCLUDED_DIRS` 를 추가한다. 어차피 그 안의 파일은 스캔·수정
대상이 아니므로 사본에 없어도 동작에 영향이 없고, 복사 속도와 디스크 사용량만 줄어든다.

## 할 일 (계획이 전부 완료된 뒤 구현)

| # | 할 일 | 파일 |
|---|---|---|
| P5-1 | `copytree` 의 `ignore_patterns` 에 `EXCLUDED_DIRS` 추가 | `heal.py` |

## 검증

| 무엇 | 어떻게 | 통과 기준 |
|---|---|---|
| 정상 동작 | `heal.py ./samples/py-index` | 지금과 동일하게 샌드박스 생성, 원본 불변 |
| 제외 경로 미복사 | `node_modules` 등을 포함한 임시 폴더로 실행 | 사본 안에 해당 폴더가 없음 |
| 원본 불변 (G6·G8) | 실행 전후 `git status --short samples/` | 소스 파일 변경 0건 |
