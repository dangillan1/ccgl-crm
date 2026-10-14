#!/bin/bash
# One-shot setup of the Monday-morning refresh on the Mac that will run it (the Mac mini).
#   curl -fsSL https://raw.githubusercontent.com/dangillan1/ccgl-crm/main/tools/install_mini.sh | bash
# Safe to re-run. Does: clone the repo into ~/Documents/Claude/Projects/CCGL Wholesale/ccgl-crm (if missing),
# set git to never open an editor and to remember the GitHub token in the keychain, install the three launchd
# jobs for THIS user's path (Mon 6:45 AM pull, Mon 7:45 AM push, and the on-demand pull/push queue the Cowork
# task uses — see tools/monday_sync.sh), and print what's left to do by hand.
set -e
BASE="$HOME/Documents/Claude/Projects/CCGL Wholesale"
REPO="$BASE/ccgl-crm"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

echo "== 1/4 repo"
mkdir -p "$BASE"
if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/dangillan1/ccgl-crm.git "$REPO"
else
  git -C "$REPO" pull --no-rebase || true
fi
git -C "$REPO" config core.editor true
git -C "$REPO" config credential.helper osxkeychain
git -C "$REPO" config user.name "${USER}"
git -C "$REPO" config user.email "${USER}@$(scutil --get LocalHostName 2>/dev/null || hostname)"
chmod +x "$REPO/tools/monday_sync.sh"
mkdir -p "$REPO/tools/out/requests"
echo "   repo at: $REPO"

echo "== 2/4 launchd jobs (Mon 6:45 AM pull, Mon 7:45 AM push, on-demand queue)"
mkdir -p "$HOME/Library/LaunchAgents"
install_job() {   # label  leg  log-name  trigger-xml
  P="$HOME/Library/LaunchAgents/$1.plist"
  launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
  cat > "$P" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/tools/monday_sync.sh</string><string>$2</string></array>
  $4
  <key>StandardOutPath</key><string>/tmp/$3.log</string>
  <key>StandardErrorPath</key><string>/tmp/$3.log</string>
</dict></plist>
PLIST
  launchctl bootstrap "gui/$(id -u)" "$P"
  echo "   loaded $1"
}
install_job com.ccgl.crm.monday-pull  pull  ccgl-crm-monday-pull  "<key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>45</integer></dict>"
install_job com.ccgl.crm.monday-push  push  ccgl-crm-monday-push  "<key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>45</integer></dict>"
install_job com.ccgl.crm.sync-request queue ccgl-crm-sync-request "<key>QueueDirectories</key><array><string>$REPO/tools/out/requests</string></array>"

echo "== 3/4 test the pull leg"
bash "$REPO/tools/monday_sync.sh" pull

echo "== 4/4 what's left (by hand, in this order)"
cat << TXT

  A. Full Disk Access for /bin/bash (System Settings > Privacy & Security > Full Disk Access, "+", press
     Cmd-Shift-G and type /bin/bash). Without it launchd's bash cannot read this Documents folder (exit 126).

  B. Save the GitHub token so pushes can run unattended. In this Terminal:
        cd "$REPO" && git push
     If it asks for a username, type dangillan1; for the password, paste the CCGL CRM GitHub token
     (the same github_pat_... the team uses). It says "Everything up-to-date" and the keychain keeps it.

  C. Open Cowork on this Mac, connect the folder  $BASE
     and create (or update) the scheduled task "CCGL CRM — Monday 7 AM refresh + team brief",
     every Monday at 7:00 AM, using exactly the prompt in ccgl-crm/tools/monday_task_prompt.md.

  D. Click Run now on that task once and approve the QuickBooks and Gmail tools when asked (the approvals
     stick for future runs). The task can be re-run on any day: it requests its own pull and push.

  Leave Cowork open on this Mac with this user logged in; the task only fires while the app is running.
TXT
