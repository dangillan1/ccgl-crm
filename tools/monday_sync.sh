#!/bin/bash
# Git legs of the Monday-morning refresh, run by launchd on the Mac that has the repo (the Mac mini).
#   monday_sync.sh pull   — Mon 6:45 AM (com.ccgl.crm.monday-pull): bring the team's week of CRM saves down
#   monday_sync.sh push   — Mon 7:45 AM (com.ccgl.crm.monday-push): commit + publish what the 7:00 AM task wrote
#   monday_sync.sh queue  — on demand (com.ccgl.crm.sync-request): the Cowork task cannot run git from its
#                           sandbox, so it drops an empty file named `pull` or `push` into tools/out/requests/;
#                           launchd watches that folder and runs this leg, which serves the request and deletes
#                           the file. This is what lets a manual re-run of the task work on any day of the week.
# Logs to tools/out/sync.log. Safe to run by hand any time.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/tools/out"; LOG="$OUT/sync.log"; REQ="$OUT/requests"
mkdir -p "$OUT" "$REQ"
cd "$REPO" || exit 1
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
git config core.editor true
log(){ echo "$*" >> "$LOG"; }

do_pull(){
  log "=== $(date '+%Y-%m-%d %H:%M') pull"
  git pull --no-rebase >> "$LOG" 2>&1 || log "PULL FAILED"
}
do_push(){
  log "=== $(date '+%Y-%m-%d %H:%M') push"
  git add -A >> "$LOG" 2>&1
  if git diff --cached --quiet; then
    log "nothing to commit"
  else
    git commit -m "Monday refresh $(date '+%Y-%m-%d'): QuickBooks feed + assessments" >> "$LOG" 2>&1
  fi
  if ! git pull --no-rebase >> "$LOG" 2>&1; then
    # a data file the team touched during the run collided: keep GitHub's copy of anything conflicted, then retry
    for f in $(git diff --name-only --diff-filter=U); do git checkout --theirs -- "$f"; git add "$f"; log "conflict on $f -> kept GitHub's copy"; done
    git commit -m "Merge Monday refresh" >> "$LOG" 2>&1 || true
  fi
  git push >> "$LOG" 2>&1 || log "PUSH FAILED"
}

case "${1:-}" in
  pull) do_pull; tail -3 "$LOG" ;;
  push) do_push; tail -3 "$LOG" ;;
  queue)
    served=0
    for name in pull push; do            # pulls before pushes when both are waiting
      [ -e "$REQ/$name" ] || continue
      rm -f "$REQ/$name"                 # remove first, so launchd stops re-launching us
      log "--- serving request: $name"
      "do_$name"; served=1
    done
    # anything else left in the folder (.DS_Store, a typo) would keep launchd re-launching: clear it
    find "$REQ" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null
    [ "$served" = 1 ] && tail -3 "$LOG"
    exit 0 ;;
  *) echo "usage: monday_sync.sh pull|push|queue"; exit 2 ;;
esac
