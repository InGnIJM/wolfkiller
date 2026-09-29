#!/bin/sh
# Credential scanner shared by the pre-commit hook and CI.
#
#   sh .githooks/secret-scan.sh --staged    files staged right now
#   sh .githooks/secret-scan.sh --history   every path and every added line in history
#
# Pure git + POSIX sh (grep/sed/cut): no third-party binary, so the local hook
# and the CI job run the same check and cannot drift apart.
#
# Exit 0 = clean, 1 = findings, 2 = usage error. A match is never printed — only
# its location — so the report itself cannot leak a credential into a terminal,
# a CI log or an issue.
#
# Known and accepted historical findings live in
# .githooks/secret-scan-baseline.txt; anything else fails.

set -u

# Content that must never be committed. No backslashes: the same string is used
# as an extended regular expression by grep.
PATTERNS='sk-[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|ghu_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----'

# Paths that must never enter the repository whatever they contain.
BLOCKED_PATHS='(^|/)\.env($|\.)|\.(pem|p12|pfx|jks|keystore)$|(^|/)data/models\.json$|\.sqlite3?$'

# Templates are the one shape of .env that belongs in the repository.
ALLOWED_PATHS='(^|/)\.env\.(example|sample|template)$'

BASELINE=$(dirname "$0")/secret-scan-baseline.txt

usage() {
    echo "usage: sh .githooks/secret-scan.sh --staged|--history" >&2
    exit 2
}

# Accepted findings, as bare values: "path <p>" or "commit <sha>".
baseline_values() {
    [ -f "$BASELINE" ] || return 0
    sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$BASELINE" |
        grep -E "^$1 " |
        cut -d' ' -f2-
}

# Report blocked paths, skipping the accepted ones.
report_paths() {
    kind=$1
    listed=$2
    accepted=$3
    [ -n "$listed" ] || return 0
    found=0
    for path in $listed; do
        if printf '%s\n' "$accepted" | grep -qxF "$path"; then
            continue
        fi
        if [ "$found" -eq 0 ]; then
            echo "secret-scan: $kind 中出现禁止提交的路径：" >&2
            found=1
        fi
        echo "  $path" >&2
    done
    [ "$found" -eq 1 ] && return 1
    return 0
}

# The staged check deliberately consults no baseline: whatever is about to be
# committed must be clean now, however that path behaved in the past.
scan_staged() {
    status=0
    paths=$(git diff --cached --name-only --diff-filter=ACMR |
        grep -E "$BLOCKED_PATHS" | grep -vE "$ALLOWED_PATHS")
    report_paths "暂存区" "$paths" "" || status=1

    # -l prints file names only, never the matching line.
    hits=$(git grep --cached -lI -E "$PATTERNS" || true)
    if [ -n "$hits" ]; then
        echo "secret-scan: 暂存区文件内容疑似包含凭据：" >&2
        printf '  %s\n' $hits >&2
        status=1
    fi
    return $status
}

scan_history() {
    status=0
    # Every path that ever existed in any commit (commits themselves list no path).
    paths=$(git rev-list --all --objects | grep ' ' | cut -d' ' -f2- | sort -u |
        grep -E "$BLOCKED_PATHS" | grep -vE "$ALLOWED_PATHS")
    report_paths "历史" "$paths" "$(baseline_values path)" || status=1

    # Commits whose diff changes the number of credential matches. The pickaxe
    # runs inside git, so this stays a single fast pass instead of one `git show`
    # per commit. Merge commits contribute no diff here, which is why this pass
    # complements the path check rather than replacing it.
    accepted=$(baseline_values commit)
    commits=$(git log --all --format=%h --pickaxe-regex -S"$PATTERNS" || true)
    found=0
    for short in $commits; do
        if printf '%s\n' "$accepted" | grep -qE "(^| )$short( |$)"; then
            continue
        fi
        if [ "$found" -eq 0 ]; then
            echo "secret-scan: 历史中的提交疑似包含凭据（仅列提交号）：" >&2
            found=1
        fi
        printf '  %s  %s\n' "$short" "$(git log -1 --format=%s "$short")" >&2
        status=1
    done
    return $status
}

case "${1:-}" in
    --staged) scan_staged ;;
    --history) scan_history ;;
    *) usage ;;
esac
