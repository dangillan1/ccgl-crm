#!/usr/bin/env python3
"""
One-time, on the Mac that sends the Monday brief: store the Gmail app password used to send as
"CCGL CRM Weekly Update <dgmacminiai1@gmail.com>". Writes ~/.ccgl_crm_smtp.json (mode 600), outside the repo.

  python3 tools/smtp_setup.py

Get the app password first: sign in to the sending Gmail account -> myaccount.google.com -> Security ->
2-Step Verification (must be on) -> App passwords -> create one named "CCGL CRM brief". Google shows 16
characters in groups of four; paste the whole thing, spaces are fine.
"""
import os, json, getpass, smtplib, ssl, sys
path = os.path.expanduser("~/.ccgl_crm_smtp.json")
user = input("Gmail address that sends the brief [dgmacminiai1@gmail.com]: ").strip() or "dgmacminiai1@gmail.com"
pw = getpass.getpass("Paste the 16-character app password (nothing shows while you paste), then Enter: ").replace(" ", "").strip()
if len(pw) != 16: sys.exit(f"that was {len(pw)} characters after removing spaces; a Gmail app password is 16 — try again")
print("checking the login with Gmail…")
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s: s.login(user, pw)
except Exception as ex:
    sys.exit(f"Gmail rejected it: {ex}\nMake sure 2-Step Verification is on for {user} and the app password is fresh.")
json.dump({"user": user, "app_password": pw}, open(path, "w")); os.chmod(path, 0o600)
print(f"saved to {path} — login OK. Test with:\n  python3 tools/weekly_email.py --send --to {user}")
