#!/usr/bin/env bash
# Copy this skill into the Claude Code skills folder. The only difference between the two
# copies is the path each SKILL.md uses for its own scripts; everything else is verbatim.
# Edit the Pi copy; run this; `--check` (also run by scripts/test_sync.py) fails when they drift.
#
# Usage:  bash sync-to-claude.sh [--check]     (PI_DIR / CLAUDE_DIR override the defaults)
set -euo pipefail

PI_DIR="${PI_DIR:-$HOME/.pi/agent/skills/soulreap}"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude/skills/soulreap}"
RSYNC_EXCLUDES=(--exclude '__pycache__' --exclude '.pytest_cache' --exclude '.ruff_cache')
DIFF_EXCLUDES=(-x '__pycache__' -x '.pytest_cache' -x '.ruff_cache')

[ -d "$PI_DIR" ] || { echo "source skill not found: $PI_DIR" >&2; exit 2; }
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$PI_DIR/" "$STAGE/"
sed -i 's|~/\.pi/agent/skills/soulreap/|~/.claude/skills/soulreap/|g' "$STAGE/SKILL.md"

if [ "${1:-}" = "--check" ]; then
    if diff -rq "${DIFF_EXCLUDES[@]}" "$STAGE" "$CLAUDE_DIR" >/dev/null 2>&1; then
        echo "claude copy is current"
        exit 0
    fi
    echo "claude copy is OUT OF DATE — run without --check" >&2
    diff -rq "${DIFF_EXCLUDES[@]}" "$STAGE" "$CLAUDE_DIR" >&2 || true
    exit 1
fi
mkdir -p "$CLAUDE_DIR"
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$STAGE/" "$CLAUDE_DIR/"
echo "synced $PI_DIR -> $CLAUDE_DIR"
