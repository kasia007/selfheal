#!/usr/bin/env bash
# 실행 스크립트 — src/agent.py 를 그대로 호출하는 얇은 래퍼입니다.
#
#   ./run.sh ./data/samples/py-index            # 제안만 (원본 불변)
#   ./run.sh ./data/samples/py-index --apply    # 검수 후 실제 적용
#   ./run.sh --stats                            # 누적 버그 패턴 리포트
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python -m src.agent "$@"
