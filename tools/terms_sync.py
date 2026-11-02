#!/usr/bin/env python3
"""Payment terms: walk each account's make-up path forward from what QuickBooks shows.

  python3 tools/terms_sync.py            plan only — prints what would change
  python3 tools/terms_sync.py --apply    writes data/accounts.json (and data/orders.json if it stamped orders)

Runs after tools/build_qbo.py and tools/qbo_sync.py --apply (qbo_sync calls it itself when applying), so it sees
this week's invoices, balances and due dates in data/qbo.json.

Terms live on the account (data/accounts.json, key "terms"); no key means standard Net 30:
  {"plan": "net30" | "cod" | "makeup" | "custom", "step": 1-3 (make-up only), "label": "Net 15" (custom only),
   "since": "YYYY-MM-DD", "reason": "...", "set_by": "dan" | "qbo-sync", "flag": "...", "late": ["1750"], "history": [...]}

What moves on its own — only the make-up path (COD -> half/half -> Net 30 trial -> standard Net 30):
  * An order logged while the account is on a step is stamped by the app (order.terms = {"plan": "makeup", "step": n, "label": ...}).
    Orders QuickBooks created, or logged before the stamp existed, get the account's current terms here (dated on/after "since").
  * The step advances when every stamped order of the current step has a QuickBooks invoice, every one is paid (balance 0),
    and none was ever seen open past its due date. Step 3 paid clean => plan "net30" again (history keeps the path).
  * An invoice seen open past its due date goes into terms.late and sets terms.flag. Nothing advances after that: the
    profile, the Insights list and the Monday brief show the flag, and someone decides in the CRM (restart at COD, or
    advance by hand). Any change made in the app clears flag and late.
  * COD and custom terms never change on their own.
"""
import json, os, re, sys, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TODAY = dt.date.today().isoformat()
STEP_SHORT = {1: "COD", 2: "50 / 50", 3: "Net 30 trial"}
PLAN_LABEL = {"net30": "Net 30", "cod": "COD — no terms", "makeup": "Make-up path", "custom": "Custom"}

def load(p, d=None):
    try: return json.load(open(p))
    except FileNotFoundError: return d
def is_conf(o): return bool(re.match(r"conf", o.get("status") or "", re.I))
def step_of(t): return min(3, max(1, int(t.get("step") or 1)))
def label(plan, step=None, custom=""):
    if plan == "makeup": return f"Make-up {step}/3 · {STEP_SHORT[step]}"
    if plan == "custom": return custom or "Custom terms"
    return "COD" if plan == "cod" else "Net 30"
def short(a):
    t = a.get("terms") or {}; p = t.get("plan")
    return label(p, step_of(t) if p == "makeup" else None, t.get("label")) if p in ("cod", "makeup", "custom") else "Net 30"
def nm(a): return f"{a['name']} / {a.get('town') or '—'}"

def run(apply=False, quiet=False):
    accounts = load(os.path.join(DATA, "accounts.json"), [])
    orders = load(os.path.join(DATA, "orders.json"), [])
    qbo = load(os.path.join(DATA, "qbo.json"), {})
    inv = {}
    for i in qbo.get("invoices", []): inv.setdefault(str(i.get("id")), i)     # split invoices repeat the id; balance/due are the whole invoice's
    changes = []; orders_dirty = False
    for a in accounts:
        t = a.get("terms") or {}; plan = t.get("plan")
        if plan not in ("cod", "makeup", "custom"): continue
        step = step_of(t) if plan == "makeup" else None
        since = t.get("since") or ""
        stamp = {"plan": plan, "step": step, "label": label(plan, step, t.get("label"))}
        for o in orders:                                       # 1. stamp what the app could not
            if o.get("account_id") != a["id"] or not is_conf(o) or o.get("terms"): continue
            if (o.get("date") or "") < since: continue
            o["terms"] = dict(stamp); orders_dirty = True
            changes.append(("STAMP", f"{nm(a)}: order {o.get('date')} {('$%s' % o.get('total')) if o.get('total') else ''} -> {stamp['label']}"))
        if plan != "makeup": continue
        mine = [o for o in orders if o.get("account_id") == a["id"] and is_conf(o) and (o.get("terms") or {}).get("plan") == "makeup" and ((o.get("terms") or {}).get("step") or 1) == step]
        if not mine: continue
        late = set(str(x) for x in (t.get("late") or [])); open_past_due = []; waiting = []; docs = []
        for o in mine:
            i = inv.get(str(o.get("qbo_id") or ""))
            if not i: waiting.append(o); continue
            docs.append(str(i.get("doc")))
            if (i.get("balance") or 0) > 0.5:
                waiting.append(o)
                if i.get("due") and i["due"] < TODAY:
                    open_past_due.append(i); late.add(str(i.get("doc")))
        new_late = sorted(late)
        if open_past_due:
            days = max((dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(i["due"])).days for i in open_past_due)
            flag = f"invoice #{', #'.join(str(i['doc']) for i in open_past_due)} is {days}d past due on make-up step {step} — not advanced; restart at COD or advance by hand"
            if t.get("flag") != flag or new_late != sorted(str(x) for x in (t.get("late") or [])):
                t["flag"] = flag; t["late"] = new_late; a["terms"] = t
                changes.append(("FLAG", f"{nm(a)}: {flag}"))
            continue
        if waiting: continue                                   # invoice missing or still open and within terms: wait for next Monday
        if new_late:                                           # everything paid, but something in this step went past due first
            flag = f"invoice #{', #'.join(new_late)} of make-up step {step} was paid late — not advanced; advance or restart by hand"
            if t.get("flag") != flag:
                t["flag"] = flag; t["late"] = new_late; a["terms"] = t
                changes.append(("FLAG", f"{nm(a)}: {flag}"))
            continue
        reason = f"invoice #{', #'.join(docs)} paid on time"
        if step < 3: nplan, nstep = "makeup", step + 1
        else: nplan, nstep = "net30", None
        nlabel = label(nplan, nstep)
        entry = {"date": TODAY, "by": "qbo-sync", "plan": nplan, "step": nstep, "label": nlabel, "reason": reason}
        hist = t.get("history") if isinstance(t.get("history"), list) else []
        a["terms"] = {"plan": nplan, "step": nstep, "label": "", "since": TODAY, "reason": reason, "set_by": "qbo-sync", "history": ([entry] + hist)[:40]}
        changes.append(("ADVANCE", f"{nm(a)}: step {step} -> {'standard Net 30' if nplan == 'net30' else 'step %d (%s)' % (nstep, STEP_SHORT[nstep])} — {reason}"))
    kinds = {}
    for k, _ in changes: kinds[k] = kinds.get(k, 0) + 1
    if not quiet:
        print("terms_sync:", ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) or "nothing to do")
        for k, msg in changes:
            if k != "STAMP": print(f"   {k}: {msg}")
    if apply and changes:
        json.dump(accounts, open(os.path.join(DATA, "accounts.json"), "w"), indent=1, ensure_ascii=False)
        if orders_dirty: json.dump(orders, open(os.path.join(DATA, "orders.json"), "w"), indent=1, ensure_ascii=False)
        if not quiet: print(f"accounts.json written{' + orders.json' if orders_dirty else ''}")
    return changes

if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
