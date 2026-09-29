#!/usr/bin/env bash
# Commit the given paths (files or directories, relative to the repo root) to main and push.
# Usage: bash scripts/push_to_main.sh "<commit message>" <path> [<path> ...]
#
# Render's cron checkout isn't a normal clone (it has no `origin` remote and sits on whatever commit
# was last built), so rather than pushing from it, this makes a fresh shallow clone of main, copies
# the regenerated paths into it, and pushes from there - which also means it can never push a stale
# base over newer commits on main.
# Requires env vars GITHUB_TOKEN (a PAT with repo scope) and GITHUB_REPO ("owner/repo").
set -euo pipefail
cd "$(dirname "$0")/.."

msg="$1"; shift
: "${GITHUB_TOKEN:?GITHUB_TOKEN must be set}"
: "${GITHUB_REPO:?GITHUB_REPO must be set}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# Skip downloading LFS model files - we only overwrite them, never read them here.
GIT_LFS_SKIP_SMUDGE=1 git clone --quiet --depth 1 --branch main \
  "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git" "$work"

tar cf - "$@" | tar xf - -C "$work"

cd "$work"
git config user.email "bot@render.com"
git config user.name "render-cron-bot"
git add -- "$@"
if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit --quiet -m "$msg"
  git push --quiet origin HEAD:main
  echo "Pushed: $msg"
fi
