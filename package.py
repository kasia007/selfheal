"""제출용 zip 을 만듭니다.

산출물 구성은 다음 네 가지입니다.
    코드 · Dockerfile · README · RAGAS 평가 리포트

메모리(``chroma_db/``)와 실행 산출물(``.heal/``)은 제외합니다.
전자는 로컬 학습 결과라 제출물이 아니고, 후자는 실행하면 다시 생기기 때문입니다.

실행:
    python package.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

EXCLUDE_DIRS = {"chroma_db", ".heal", "__pycache__", ".pytest_cache", "dist", ".git", ".claude"}
EXCLUDE_SUFFIXES = {".pyc"}

# 정확히 이 이름의 **파일**은 디렉터리가 아니므로 EXCLUDE_DIRS 로는 안 걸립니다.
# ``.env`` 에는 실제 AWS·LangSmith 자격증명이 들어 있습니다 — 이걸 빼먹으면
# 공유용 zip 에 사용자의 진짜 비밀값이 그대로 실립니다. G10(비밀값은 나가는 곳에서
# 지운다)과 같은 원칙을 패키징 경로에도 적용합니다.
# ``sds-ax-c1-06.env`` 도 이름만 다를 뿐 같은 이유로 뺍니다 — 실제 AWS 키 두 쌍이
# 평문으로 들어 있는 게 확인되었습니다.
EXCLUDE_FILES = {".env", ".env.local", "sds-ax-c1-06.env"}

# 제출 규약(mini-pjt_{이름}/)이 요구하는 최상위 폴더명입니다.
SUBMIT_NAME = "김동규"
ARCHIVE_ROOT = f"mini-pjt_{SUBMIT_NAME}"

# 이 파일들이 없으면 제출 요건을 못 채웁니다. 빠졌으면 경고합니다.
REQUIRED = [
    "SERVICE.md",
    "README.md",
    "USAGE.md",
    "Dockerfile",
    "requirements.txt",
    "run.sh",
    ".env.example",
    "src/agent.py",
    "evaluation/test_queries.csv",
    "evaluation/round1_report.md",
    "evaluation/round2_report.md",
]


def collect() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        if path.name in EXCLUDE_FILES:
            continue
        files.append(path)
    return sorted(files)


def main() -> int:
    missing = [name for name in REQUIRED if not (ROOT / name).exists()]
    if missing:
        print("⚠️ 산출물 누락:")
        for name in missing:
            print(f"   - {name}")
        print("   리포트는 `python evaluation/run_inout.py --round {1,2}` 뒤에 `python evaluation/run_ragas.py --round {1,2}` 로 생성합니다.")

    DIST.mkdir(exist_ok=True)
    target = DIST / f"{ARCHIVE_ROOT}.zip"
    files = collect()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, Path(ARCHIVE_ROOT) / path.relative_to(ROOT))

    size_kb = target.stat().st_size / 1024
    print(f"\n✅ {target}  ({len(files)}개 파일 · {size_kb:.0f} KB)")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
