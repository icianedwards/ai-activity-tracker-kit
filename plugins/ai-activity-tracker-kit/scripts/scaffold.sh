#!/usr/bin/env bash
# Copy the .context/ template into a folder and fill placeholders.
# Usage: scaffold.sh <repo-root>              repo level
#        scaffold.sh --project <folder>       project level (a folder that holds several repos)
# Refuses to overwrite an existing .context/
set -euo pipefail

level=repo
if [ "${1:-}" = "--project" ]; then level=project; shift; fi
root="${1:-$PWD}"
here="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/.context"

if [ -e "$dest" ]; then
  echo "EXISTS: $dest already exists — not overwriting." >&2
  exit 2
fi

name="$(basename "$root")"
date="$(date +%Y-%m-%d)"
branch="$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo n/a)"

stack="TODO"
if compgen -G "$root/*.sln" >/dev/null || compgen -G "$root/*.csproj" >/dev/null \
   || compgen -G "$root/*/*.csproj" >/dev/null; then stack=".NET (C#)"
elif [ -f "$root/angular.json" ] && [ -f "$root/ionic.config.json" ]; then stack="Ionic + Angular"
elif [ -f "$root/angular.json" ]; then stack="Angular"
elif [ -f "$root/shopify.app.toml" ]; then stack="Shopify app (Remix/Node)"
elif [ -f "$root/package.json" ]; then stack="Node"
fi

cp -R "$here/template" "$dest"
if [ "$level" = project ]; then
  cp "$here/template-project/INDEX.md" "$dest/INDEX.md"
  branch="n/a (project level)"
fi
find "$dest" -type f -name '*.md' -exec sed -i '' \
  -e "s|__NAME__|$name|g" -e "s|__DATE__|$date|g" \
  -e "s|__BRANCH__|$branch|g" -e "s|__STACK__|$stack|g" {} +

python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$here/.claude-plugin/plugin.json" > "$dest/.kit-version" 2>/dev/null || true
echo "CREATED: $dest (level=$level name=$name stack=$stack branch=$branch)"
find "$dest" -type f | sed "s|$root/||" | sort
