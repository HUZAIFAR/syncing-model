#!/bin/bash
# Block a commit that would publish private data.
#
# Scans only what is STAGED. Patterns here are generic on purpose -- putting a
# real name or address in this file would itself leak it. For terms specific to
# you (your name, account, org), create ".secrets-patterns.local" in the repo
# root, one extended-regex per line; it is git-ignored and read automatically.
#
# Install:  ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit
# Bypass (only if you are certain):  git commit --no-verify

set -uo pipefail
FAIL=0
staged() { git diff --cached --name-only --diff-filter=ACM; }

report() {
    printf '\n  BLOCKED [%s]\n' "$1"
    printf '%s\n' "$2" | head -5 | sed 's/^/      /' | cut -c1-118
    FAIL=1
}

scan() {
    local label="$1" pat="$2" hits
    # the scanner itself holds these patterns as literals; don't match on it
    hits=$(git diff --cached -U0 -- . ':(exclude)scripts/check-secrets.sh' \
           | grep -E '^\+' | grep -vE '^\+\+\+' | grep -nEI "$pat" 2>/dev/null)
    [ -n "$hits" ] && report "$label" "$hits"
}

scan "email address"   '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
scan "home directory"  '/(Users|home)/[A-Za-z0-9._-]+'
scan "cloud mount"     'CloudStorage|GoogleDrive-|Dropbox/|OneDrive'
scan "GitHub token"    'gh[pousr]_[A-Za-z0-9]{16,}'
scan "OpenAI key"      'sk-[A-Za-z0-9_-]{16,}'
scan "Google API key"  'AIza[A-Za-z0-9_-]{30,}'
scan "AWS key"         'AKIA[0-9A-Z]{16}|aws_secret_access_key'
scan "Slack token"     'xox[baprs]-[A-Za-z0-9-]{10,}'
scan "private key"     'BEGIN [A-Z ]*PRIVATE KEY'
scan "assigned secret" '(password|passwd|secret|api[_-]?key|token)[[:space:]]*[=:][[:space:]]*["'"'"'][^"'"'"']{6,}'

# user-supplied private terms, never committed
if [ -f .secrets-patterns.local ]; then
    while IFS= read -r p; do
        [ -z "$p" ] && continue
        case "$p" in \#*) continue ;; esac
        scan "private term" "$p"
    done < .secrets-patterns.local
fi

# content that should never be in this repo at all
BIN=$(staged | grep -iE '\.(mp3|wav|m4a|m4b|aac|flac|ogg|opus|aif|aiff|wma|mp4|pdf|npz)$')
[ -n "$BIN" ] && report "media/data file staged" "$BIN"

BIG=$(staged | while read -r f; do
        [ -f "$f" ] || continue
        s=$(wc -c < "$f")
        [ "$s" -gt 1048576 ] && echo "$f ($((s/1024)) KB)"
      done)
[ -n "$BIG" ] && report "file over 1 MB" "$BIG"

if [ "$FAIL" = "1" ]; then
    printf '\n  Commit aborted. Remove the above, or use --no-verify if it is a false positive.\n\n'
    exit 1
fi
exit 0
