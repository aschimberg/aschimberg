#!/usr/bin/env bash
# Commit regenerated data (if any) and push, rebasing over concurrent pushes.
set -euo pipefail
msg="${1:-Data update}"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data site/data
if git diff --cached --quiet; then
  echo "No data changes"
  exit 0
fi
git commit -m "$msg $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3 4; do
  if git pull --rebase --autostash origin "${GITHUB_REF_NAME}" && git push origin "HEAD:${GITHUB_REF_NAME}"; then
    exit 0
  fi
  sleep $((i * 5))
done
echo "push failed" >&2
exit 1
