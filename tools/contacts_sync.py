#!/usr/bin/env python3
"""Contacts confirmed: the Monday pass over every active account's people.

  python3 tools/contacts_sync.py            plan only — prints the state of the book and what would change
  python3 tools/contacts_sync.py --apply    writes data/accounts.json

Runs after qbo_sync.py --apply (which calls it), so it sees the week's contact edits from the 6:45 pull.

The rule, same as the app (ccState in index.html):
  * An account is "confirmed" when someone pressed Confirm contacts (accounts.json: contacts_confirmed = {at, by, n, people})
    and since then the people have not changed (people = sorted "name|email|phone|role" lines, compared live),
    fewer than settings.contacts_confirm.days (180) have passed, and nobody flagged it (contacts_review = {at, by, reason}).
    "Reachable" means an email or a phone; a name is not required, because Finance / AP is often a shared mailbox.
  * Otherwise it is: review (flagged), changed, expired, never — or missing when Buyer, Finance / AP or Intake has no
    named person with an email or phone (that one needs a contact added before it can be confirmed).

What this script does (when settings.contacts_confirm.auto_task is not false):
  * puts a next step "Confirm contacts (Buyer / Finance / Intake)" on an active account that is not confirmed and has no
    open next step, owned by the account's owner (or the paperwork fallback owner) — due in 14 days when it was never
    confirmed, 7 days when it changed, expired, was flagged or is missing a role (then the text says what to add first);
  * clears that next step when the account turns out to be confirmed (someone pressed Confirm; the app clears it too).
At most settings.contacts_confirm.tasks_per_week (10) new next steps per owner per run, flagged / changed / expired first, then
never-confirmed, then missing-a-role, biggest 2026 accounts first — so the backlog drains over a few weeks instead of
landing on one person at once. It never touches a next step somebody typed by hand, and never changes contacts_confirmed.
"""
import json, os, re, sys, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TODAY = dt.date.today()
CC_TASK = "Confirm contacts (Buyer / Finance / Intake)"
ORDER = {"missing": 0, "review": 1, "changed": 2, "expired": 3, "never": 4, "confirmed": 5}
LABEL = {"confirmed": "Contacts confirmed", "never": "Confirm contacts", "changed": "Contacts changed", "expired": "Reconfirm contacts", "review": "Contacts need review", "missing": "Contacts missing"}
BUYER, FINANCE, INTAKE = re.compile(r"buyer", re.I), re.compile(r"finance|\bap\b|\bar\b|billing|accounting|payable", re.I), re.compile(r"intake", re.I)

def load(p, d=None):
    try: return json.load(open(p))
    except FileNotFoundError: return d
def cfg(settings):
    c = settings.get("contacts_confirm") or {}
    try: days = int(c.get("days"))
    except (TypeError, ValueError): days = 0
    try: per = int(c.get("tasks_per_week"))
    except (TypeError, ValueError): per = -1
    return {"days": days if days > 0 else 180, "auto_task": c.get("auto_task") is not False, "tasks_per_week": per if per >= 0 else 10}
def d(s):
    try: return dt.date.fromisoformat(str(s)[:10])
    except Exception: return None
def filled(l): return any(str(c.get("email") or "").strip() or str(c.get("phone") or "").strip() for c in l)      # reachable; a name is not required (AP is often a shared mailbox)
def people_sig(cs): return sorted(("|".join(str(c.get(k) or "").strip() for k in ("name", "email", "phone", "role")) for c in cs), key=str.lower)   # kept as typed; compared case-insensitively

def cc_state(a, cs, days):
    """cs = this account's contacts. Mirrors ccState() in index.html."""
    buyers = [c for c in cs if BUYER.search(c.get("role") or "")] or [c for c in cs if c.get("primary")][:1]
    finances = [c for c in cs if FINANCE.search(c.get("role") or "")]; intakes = [c for c in cs if INTAKE.search(c.get("role") or "")]
    missing = [n for n, l in (("Buyer", buyers), ("Finance / AP", finances), ("Intake", intakes)) if not filled(l)]
    r = a.get("contacts_confirmed") if isinstance(a.get("contacts_confirmed"), dict) else None
    rv = a.get("contacts_review") if isinstance(a.get("contacts_review"), dict) else None
    people = people_sig(cs); at = d(r.get("at")) if r else None
    o = {"state": "never", "missing": missing, "ready": not missing, "at": r.get("at") if r else "", "by": r.get("by") if r else "", "age": (TODAY - at).days if at else None,
         "due": (at + dt.timedelta(days=days)).isoformat() if at else "", "changed": [], "review": rv, "n": len(people)}
    if rv and (not r or str(rv.get("at") or "") >= str(r.get("at") or "")): o["state"] = "review"
    elif r:
        was = [str(p) for p in r.get("people")] if isinstance(r.get("people"), list) else []
        nm = lambda p: p.split("|")[0] or p.split("|")[1] or "?"
        was_l = {p.lower() for p in was}; now_l = {p.lower() for p in people}
        o["changed"] = ["+ " + nm(p) for p in people if p.lower() not in was_l] + ["− " + nm(p) for p in was if p.lower() not in now_l]
        o["state"] = "changed" if o["changed"] else "expired" if (o["age"] is not None and o["age"] > days) else "confirmed"
    if o["state"] != "confirmed" and missing: o["state"] = "missing"
    o["label"] = LABEL[o["state"]] + (": " + ", ".join(missing) if o["state"] == "missing" else "")
    return o

def why(o, uname):
    s = o["state"]
    if s == "confirmed": return f"by {uname(o['by'])} {o['at']} · again by {o['due']}"
    if s == "expired": return f"last confirmed {o['at']} by {uname(o['by'])} · {o['age']}d ago"
    if s == "changed": return "since %s: %s" % (o["at"], ", ".join(o["changed"][:3]) + (" +%d more" % (len(o["changed"]) - 3) if len(o["changed"]) > 3 else ""))
    if s == "review": return f"flagged by {uname(o['review'].get('by'))} {o['review'].get('at')}" + (" · " + o["review"]["reason"] if o["review"].get("reason") else "")
    if s == "missing": return "no %s contact with an email or phone" % " / ".join(o["missing"])
    return f"{o['n']} people on file, never confirmed" if o["n"] else "nobody on file"

def states(accounts, contacts, settings):
    by_acc = defaultdict(list)
    for c in contacts: by_acc[c.get("account_id")].append(c)
    days = cfg(settings)["days"]
    return {a["id"]: cc_state(a, by_acc.get(a["id"], []), days) for a in accounts}

def run(apply=False, quiet=False):
    accounts = load(os.path.join(DATA, "accounts.json"), []); contacts = load(os.path.join(DATA, "contacts.json"), []); settings = load(os.path.join(DATA, "settings.json"), {}); users = load(os.path.join(DATA, "users.json"), [])
    ytd = defaultdict(float)                                                       # this year's confirmed order $ per account: the backlog drains biggest-first
    for o_ in load(os.path.join(DATA, "orders.json"), []):
        if o_.get("account_id") and not re.match(r"pend|plan", o_.get("status") or "", re.I) and str(o_.get("date") or "").startswith(str(TODAY.year)): ytd[o_["account_id"]] += o_.get("total") or 0
    st = states(accounts, contacts, settings); c = cfg(settings)
    uid = {u["name"].lower(): u["id"] for u in users if u.get("role") != "viewer"}
    fallback = (settings.get("paperwork") or {}).get("fallback_owner") or next((u["id"] for u in users if u.get("role") == "manager"), None) or next((u["id"] for u in users if u.get("role") == "admin"), "")
    cur = [a for a in accounts if a.get("status") == "Current"]
    counts = {k: sum(1 for a in cur if st[a["id"]]["state"] == k) for k in ORDER}
    changes = []; made = defaultdict(int)
    TASK_ORDER = {"review": 0, "changed": 1, "expired": 2, "never": 3, "missing": 4, "confirmed": 5}   # stale sign-offs first, then never-confirmed, then the ones that need a contact added
    for a in sorted(cur, key=lambda a: (TASK_ORDER[st[a["id"]]["state"]], -ytd.get(a["id"], 0), a["name"])):
        o = st[a["id"]]; auto = bool(re.match(r"^Confirm contacts", a.get("next_step") or ""))
        if o["state"] == "confirmed":
            if auto:
                a["next_step"] = ""; a["next_step_due"] = ""; a["next_step_owner"] = ""; changes.append(("CLEAR", f"{a['name']} / {a.get('town') or '—'}: confirmed — next step cleared"))
            continue
        if not c["auto_task"] or a.get("next_step"): continue
        text = CC_TASK if o["ready"] else f"Confirm contacts — first add: {', '.join(o['missing'])}"
        due = (TODAY + dt.timedelta(days=14 if o["state"] == "never" else 7)).isoformat()
        owner = uid.get(str(a.get("owner") or "").lower()) or fallback
        if made[owner] >= c["tasks_per_week"]: continue
        made[owner] += 1; a["next_step"] = text; a["next_step_due"] = due; a["next_step_owner"] = owner
        changes.append(("TASK", f"{a['name']} / {a.get('town') or '—'}: {o['label']} -> {owner} by {due}"))
    kinds = {}
    for k, _ in changes: kinds[k] = kinds.get(k, 0) + 1
    if not quiet:
        print("contacts_sync: %d active · %s · %s" % (len(cur), " · ".join(f"{counts[k]} {k}" for k in ("confirmed", "never", "changed", "expired", "review", "missing")),
              ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) or "nothing to do"))
        for k, m in changes[:12]: print(f"   {k}: {m}")
        if len(changes) > 12: print(f"   … {len(changes) - 12} more")
    if apply and changes:
        json.dump(accounts, open(os.path.join(DATA, "accounts.json"), "w"), indent=1, ensure_ascii=False)
        if not quiet: print("accounts.json written")
    return changes

if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
