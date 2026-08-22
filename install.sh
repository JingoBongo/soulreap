#!/usr/bin/env bash
# Install soulreap into a harness skills directory.
#
#   ./install.sh claude          -> ~/.claude/skills/soulreap
#   ./install.sh pi              -> ~/.pi/agent/skills/soulreap
#   ./install.sh opencode        -> ${XDG_CONFIG_HOME:-~/.config}/opencode/skills/soulreap
#   ./install.sh /abs/or/rel/dir -> that directory
#
# Copies SKILL.md, scripts/ and references/. The engine is path-independent.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:-}"

case "$target" in
  claude)   dest="$HOME/.claude/skills/soulreap" ;;
  pi)       dest="$HOME/.pi/agent/skills/soulreap" ;;
  opencode) dest="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills/soulreap" ;;
  "" )
    echo "usage: ./install.sh <claude|pi|opencode|/target/dir>" >&2
    exit 2 ;;
  *)        dest="$target" ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "warning: 'uv' not found on PATH — soulreap needs it at runtime (https://docs.astral.sh/uv/)" >&2
fi

mkdir -p "$dest"
cp "$here/SKILL.md" "$dest/"
cp -r "$here/scripts" "$dest/"
cp -r "$here/references" "$dest/"
# never ship caches
find "$dest" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$dest" \( -name "*.pyc" -o -name ".pytest_cache" -o -name ".ruff_cache" \) -exec rm -rf {} + 2>/dev/null || true

echo "installed soulreap -> $dest"
echo "in SKILL.md, \$SKILL = $dest"
