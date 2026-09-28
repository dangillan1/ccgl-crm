#!/bin/bash
# Monday-morning refresh git legs, run by launchd on the Mac that has the repo:
#   monday_sync.sh pull   (6:45 AM) — bring the team's week of CRM saves down before Claude regenerates data
#   monday_sync.sh push   (7:45 AM) — publish what the 7:00 AM Cowork task wrote (qbo.json, insights.json, accounts/leads)
# Logs to tools/out/sync.log. Safe to run by hand any time.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$REPO/tools/out/sync.log"; mkdir -p "$REPO/tools/out"
cd "$REPO" || exit 1
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
git config core.editor true
echo "=== $(date '+%Y-%m-%d %H:%M') $1" >> "$LOG"
case "${1:-}" in
  pull)
    git pull --no-rebase >> "$LOG" 2>&1 || echo "PULL FAILED" >> "$LOG" ;;
  push)
    git add -A >> "$LOG" 2>&1
    if git diff --cached --quiet; then echo "nothing to commit" >> "$LOG"; exit 0; fi
    git commit -m "Monday refresh $(date '+%Y-%m-%d'): QuickBooks feed + assessments" >> "$LOG" 2>&1
    if ! git pull --no-rebase >> "$LOG" 2>&1; then
      # a data file the team touched during the run collided: keep GitHub's copy of anything conflicted, then retry
      for f in $(git diff --name-only --diff-filter=U); do git checkout --theirs -- "$f"; git add "$f"; echo "conflict on $f -> kept GitHub's copy" >> "$LOG"; done
      git commit -m "Merge Monday refresh" >> "$LOG" 2>&1 || true
    fi
    git push >> "$LOG" 2>&1 || echo "PUSH FAILED" >> "$LOG" ;;
  *) echo "usage: monday_sync.sh pull|push"; exit 2 ;;
esac
tail -3 "$LOG"
