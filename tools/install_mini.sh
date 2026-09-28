#!/bin/bash
# One-shot setup of the Monday-morning refresh on the Mac that will run it (the Mac mini).
#   curl -fsSL https://raw.githubusercontent.com/dangillan1/ccgl-crm/main/tools/install_mini.sh | bash
# Safe to re-run. Does: clone the repo into ~/Documents/Claude/Projects/CCGL Wholesale/ccgl-crm (if missing),
# set git to never open an editor and to remember the GitHub token in the keychain, install the 6:45 AM pull
# and 7:45 AM push launchd jobs for THIS user's path, and print what's left to do by hand.
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
echo "   repo at: $REPO"

echo "== 2/4 launchd jobs (Mon 6:45 AM pull, Mon 7:45 AM push)"
mkdir -p "$HOME/Library/LaunchAgents"
for leg in pull push; do
  if [ "$leg" = pull ]; then H=6; else H=7; fi
  P="$HOME/Library/LaunchAgents/com.ccgl.crm.monday-$leg.plist"
  launchctl bootout "gui/$(id -u)/com.ccgl.crm.monday-$leg" 2>/dev/null || true
  cat > "$P" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ccgl.crm.monday-$leg</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/tools/monday_sync.sh</string><string>$leg</string></array>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$H</integer><key>Minute</key><integer>45</integer></dict>
  <key>StandardOutPath</key><string>/tmp/ccgl-crm-monday-$leg.log</string>
  <key>StandardErrorPath</key><string>/tmp/ccgl-crm-monday-$leg.log</string>
</dict></plist>
PLIST
  launchctl bootstrap "gui/$(id -u)" "$P"
  echo "   loaded com.ccgl.crm.monday-$leg"
done

echo "== 3/4 test the pull leg"
bash "$REPO/tools/monday_sync.sh" pull

echo "== 4/4 what's left (by hand, in this order)"
cat << TXT

  A. Save the GitHub token so the 7:45 push can run unattended. In this Terminal:
        cd "$REPO" && git push
     If it asks for a username, type dangillan1; for the password, paste the CCGL CRM GitHub token
     (the same github_pat_... the team uses). It says "Everything up-to-date" and the keychain keeps it.

  B. Open Cowork on this Mac, connect the folder  $BASE
     and paste this into a new chat:
        Create a scheduled task with id ccgl-monday-refresh, title "CCGL CRM — Monday 7 AM refresh + team brief",
        running every Monday at 7:00 AM (cron 0 7 * * 1), using exactly the prompt in the file
        ccgl-crm/tools/monday_task_prompt.md in the connected folder.

  C. In Cowork's Scheduled section, click Run now on that task once and approve the QuickBooks and Gmail
     tools when asked (the approvals stick for future runs). That first run sends the real brief.

  D. Tell Claude on the MacBook it's done, so the copy of the task there gets switched off (otherwise two
     briefs go out on Monday).

  Leave Cowork open on this Mac Sunday night; the task only fires while the app is running.
TXT
