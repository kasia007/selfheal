"""배포용 zip 을 만듭니다.

``package.py`` 는 제출 규약(코드 · Dockerfile · README · RAGAS 평가 리포트)용이라
``tests/``·``docs/``·``evaluation/``·``data/`` 까지 전부 담습니다. 이 스크립트는
반대로, 이 도구를 다른 프로젝트에 붙여 쓸 사용자에게 줄 **실행에 필요한 것만** 담습니다.
개발·과제 채점용 파일(테스트, 회고 문서, 평가 세트, 이 프로젝트 자체를 시험하는 샘플)은
사용자의 실제 사용 흐름과 무관하므로 뺍니다.

그래서 제외 목록이 아니라 **포함 목록(화이트리스트)**을 씁니다 — 새로 생기는 개발용
파일이 실수로 배포 zip에 섞여 들어가는 쪽보다, 빠뜨린 실행 파일이 있으면 바로 눈에
띄는 쪽이 안전합니다.

실행:
    python package_deploy.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc"}

# 실행에 필요한 것만. src/ 는 디렉터리 전체를 담고, 나머지는 실행·설치 안내에
# 직접 쓰이는 개별 파일입니다.
DEPLOY_INCLUDE = [
    "src",
    "run.sh",
    "install-hooks.sh",
    "requirements.txt",
    "Dockerfile",
    ".env.example",
    "README.md",
    "USAGE.md",
]


def collect() -> list[Path]:
    files: list[Path] = []
    for name in DEPLOY_INCLUDE:
        path = ROOT / name
        if path.is_dir():
            for sub in sorted(path.rglob("*")):
                if not sub.is_file():
                    continue
                if any(part in EXCLUDE_DIRS for part in sub.relative_to(ROOT).parts):
                    continue
                if sub.suffix in EXCLUDE_SUFFIXES:
                    continue
                files.append(sub)
        elif path.is_file():
            files.append(path)
    return files


def _write(zf: zipfile.ZipFile, path: Path) -> None:
    """실행 권한 비트를 보존해서 씁니다.

    ``ZipFile.write()`` 만 쓰면 압축을 풀었을 때 ``run.sh``·``install-hooks.sh`` 의
    실행 권한이 사라질 수 있습니다 — 받은 사람이 ``chmod +x`` 를 다시 해야 하는
    불편을 없애려고 ``os.stat`` 의 모드를 ``ZipInfo.external_attr`` 에 그대로 넣습니다.
    """
    arcname = Path("selfheal") / path.relative_to(ROOT)
    info = zipfile.ZipInfo.from_file(path, arcname)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
    with path.open("rb") as fp:
        zf.writestr(info, fp.read())


def main() -> int:
    missing = [name for name in DEPLOY_INCLUDE if not (ROOT / name).exists()]
    if missing:
        print("⚠️ 누락:")
        for name in missing:
            print(f"   - {name}")

    DIST.mkdir(exist_ok=True)
    target = DIST / "selfheal-deploy.zip"
    files = collect()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            _write(zf, path)

    size_kb = target.stat().st_size / 1024
    print(f"\n✅ {target}  ({len(files)}개 파일 · {size_kb:.0f} KB)")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
