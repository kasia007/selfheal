#!/usr/bin/env bash
# git 훅 설치 스크립트 — selfheal 을 대상 저장소의 커밋·푸시 흐름에 걸어 둡니다.
#
#   ./install-hooks.sh                      # 현재 저장소에 pre-push 설치
#   ./install-hooks.sh /path/to/repo        # 다른 저장소에 설치
#   ./install-hooks.sh --hook post-commit   # 커밋 직후 알림용으로 설치 (.heal/post-commit.log)
#   ./install-hooks.sh --hook both
#   ./install-hooks.sh --uninstall
#
# 훅을 두 종류로 나눈 이유가 있습니다.
#
#   pre-push    푸시를 **막을 수 있습니다**. git 이 이 훅의 종료 코드를 봅니다.
#               그래서 "푸시 전 검증 룰셋" 은 이쪽이어야 의미가 생깁니다. 동기 실행입니다.
#   post-commit 커밋을 막지 못합니다. git 이 종료 코드를 무시하기 때문입니다.
#               알림 전용이므로 백그라운드로 떼어 터미널을 잡지 않게 만듭니다.
#
# 두 훅 모두 --no-trace 로 LangSmith 추적을 끕니다. 훅은 자동 실행 경로라, .env 에 잊힌
# 설정 하나 때문에 매 커밋 소스 전문이 외부로 나가서는 안 됩니다.
#
# 두 훅 모두 --apply 를 쓰지 않습니다. 커밋이 만들어진 뒤에 파일을 고치면 그 수정이
# 커밋 밖으로 떨어져 트리가 더러워지고, 훅 안에서 amend 로 주워담는 것은 재진입 위험이
# 있습니다. 훅은 감지와 제안까지만 하고, 적용은 사람이 --apply 로 합니다.
set -euo pipefail

SELFHEAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

hook_kind="pre-push"
uninstall=0
force=0
target=""

while [ $# -gt 0 ]; do
    case "$1" in
        --hook)      hook_kind="${2:?--hook 뒤에 pre-push · post-commit · both 중 하나}"; shift 2 ;;
        --uninstall) uninstall=1; shift ;;
        --force)     force=1; shift ;;
        -h|--help)   sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*)          echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
        *)           target="$1"; shift ;;
    esac
done

case "$hook_kind" in
    pre-push|post-commit|both) ;;
    *) echo "--hook 은 pre-push · post-commit · both 중 하나입니다: $hook_kind" >&2; exit 2 ;;
esac

# ── 대상 저장소 확인 ──────────────────────────────────────────
target="${target:-$PWD}"
if ! repo="$(git -C "$target" rev-parse --show-toplevel 2>/dev/null)"; then
    echo "git 저장소가 아닙니다: $target" >&2
    exit 3
fi
hooks_dir="$(git -C "$repo" rev-parse --git-path hooks)"
case "$hooks_dir" in /*) ;; *) hooks_dir="$repo/$hooks_dir" ;; esac
mkdir -p "$hooks_dir"

# ── 파이썬 위치를 설치 시점에 굳혀 둡니다 ─────────────────────
# 훅은 최소화된 환경에서 실행됩니다. 특히 Windows 의 Git Bash 에서는 로그인 셸의 PATH 가
# 그대로 오지 않아 `python` 을 못 찾는 일이 흔합니다. 그래서 지금 찾은 파이썬의 디렉터리를
# 훅 안에 박아 둡니다.
if ! py="$(command -v python || command -v python3)"; then
    echo "python 을 찾을 수 없습니다. 가상환경을 활성화한 뒤 다시 실행하십시오." >&2
    exit 3
fi
py_dir="$(cd "$(dirname "$py")" && pwd)"

write_hook() {
    local kind="$1" path="$hooks_dir/$1"

    if [ "$uninstall" = 1 ]; then
        if [ -f "$path" ] && grep -q "selfheal-hook" "$path" 2>/dev/null; then
            rm -f "$path"
            echo "🗑  제거: $path"
        else
            echo "…  건너뜀 (selfheal 훅이 아님): $path"
        fi
        return
    fi

    # 남의 훅을 말없이 덮지 않습니다.
    if [ -f "$path" ] && ! grep -q "selfheal-hook" "$path" 2>/dev/null && [ "$force" = 0 ]; then
        echo "❌ 이미 다른 훅이 있습니다: $path" >&2
        echo "   내용을 확인하고 합치거나, --force 로 덮어쓰십시오." >&2
        return 1
    fi

    case "$kind" in
        pre-push)    body="$PRE_PUSH" ;;
        post-commit) body="$POST_COMMIT" ;;
    esac
    printf '%s\n' "$body" \
        | sed -e "s|__SELFHEAL__|$SELFHEAL|g" -e "s|__PYDIR__|$py_dir|g" \
        > "$path"
    chmod +x "$path"
    echo "✅ 설치: $path"
}

# ── pre-push — 검증하고, 통과하지 못하면 푸시를 막습니다 ──────
PRE_PUSH='#!/usr/bin/env bash
# selfheal-hook (pre-push) — install-hooks.sh 가 생성했습니다. 직접 고치지 마십시오.
PATH="__PYDIR__:$PATH"
repo="$(git rev-parse --show-toplevel)"
# --no-trace: 훅은 커밋·푸시마다 자동으로 돕니다. .env 에 LANGSMITH_TRACING 이 남아 있으면
# 그때마다 소스 전문이 외부(LangSmith)로 나갑니다. 자동 실행 경로에서는 항상 끕니다.
"__SELFHEAL__/run.sh" "$repo" --dry-run --no-trace
case $? in
    0|2) exit 0 ;;
    4)   echo "🩹 검증된 수정안이 $repo/.heal/ 에 있습니다. 검수 후 푸시하십시오."; exit 1 ;;
    3)   echo "⚠️  검증 전제를 못 채웠습니다(툴체인·테스트 없음). 푸시를 막지 않습니다."; exit 0 ;;
    # 5 는 모델을 쓸 수 없었던 경우(토큰 한도·인증·네트워크)입니다. 코드 결함이 아니라
    # 인프라 제약이므로 3 과 같이 푸시를 막지 않습니다. 막으면 한도가 소진된 날 아무도
    # 푸시할 수 없게 됩니다.
    5)   echo "⚠️  모델을 쓸 수 없어 검증하지 못했습니다($repo/.heal/ 에 진단과 참고 사례). 푸시를 막지 않습니다."; exit 0 ;;
    *)   echo "❌ 테스트 실패를 스스로 고치지 못했습니다. $repo/.heal/ 를 확인하십시오."; exit 1 ;;
esac'

# ── post-commit — 알림 전용. 커밋을 막을 수 없으므로 백그라운드로 돕니다 ──
POST_COMMIT='#!/usr/bin/env bash
# selfheal-hook (post-commit) — install-hooks.sh 가 생성했습니다. 직접 고치지 마십시오.
PATH="__PYDIR__:$PATH"
repo="$(git rev-parse --show-toplevel)"
mkdir -p "$repo/.heal"
# --quiet 은 쓰지 않습니다. 그 옵션은 stdout 을 전부 막아 로그 파일이 비게 됩니다
# (src/report.py 의 Console.quiet). 화면이 아니라 파일로 보내는 것이 여기서의 조용함입니다.
( "__SELFHEAL__/run.sh" "$repo" --dry-run --no-trace \
    >"$repo/.heal/post-commit.log" 2>&1 ) &
exit 0'

case "$hook_kind" in
    both) write_hook pre-push; write_hook post-commit ;;
    *)    write_hook "$hook_kind" ;;
esac

# ── 산출물이 대상 저장소의 커밋을 더럽히지 않게 합니다 ────────
# .heal/ 은 검수할 diff 가 대상 폴더에 있어야 하므로 거기에 생깁니다(src/agent.py).
# 대상 저장소에 무시 규칙이 없으면 다음 커밋의 후보로 잡힙니다.
if [ "$uninstall" = 0 ] && ! git -C "$repo" check-ignore -q .heal 2>/dev/null; then
    echo "ℹ️  $repo/.gitignore 에 \`.heal/\` 을 추가하십시오. 훅 산출물이 커밋에 섞입니다."
fi
