#!/usr/bin/env python3
"""
Prints the week's fact sheet from data/*.json — the same numbers the Insights tab computes live, in a form
that can be read in one pass before writing tools/assessments.json and the Monday email.

  python3 tools/weekly_facts.py            # human-readable
  python3 tools/weekly_facts.py --json     # machine-readable (used by weekly_email.py)
"""
import json, os, sys, re, datetime, glob
from collections import defaultdict
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TODAY = datetime.date.today()
def load(n, d=None):
    try: return json.load(open(os.path.join(DATA, n)))
    except FileNotFoundError: return d
accounts = load("accounts.json"); leads = load("leads.json", []); orders = load("orders.json", []); contacts = load("contacts.json", [])
qbo = load("qbo.json", {"accounts": {}, "recon": {}, "totals": {}, "invoices": []}); settings = load("settings.json", {}); users = load("users.json", [])
acts = []
for f in glob.glob(os.path.join(DATA, "activities", "*.json")):
    u = os.path.basename(f)[:-5]
    for x in json.load(open(f)): acts.append({**x, "user": u})
A = {a["id"]: a for a in accounts}; ALL = {**{l["id"]: l for l in leads}, **A}
uname = {u["id"]: u["name"] for u in users}
def d(s):
    try: return datetime.date.fromisoformat(s[:10])
    except Exception: return None
def money(x): return f"${x:,.0f}"
conf = [o for o in orders if not re.match(r"pend|plan", o.get("status") or "", re.I) and d(o.get("date"))]
week_ago = TODAY - datetime.timedelta(days=7); two_weeks = TODAY - datetime.timedelta(days=14)

# per-account order stats (mirrors index() in the app)
by_acc = defaultdict(list)
for o in conf: by_acc[o["account_id"]].append(o)
stats = {}
for aid, os_ in by_acc.items():
    os_.sort(key=lambda o: o["date"]); dates = [d(o["date"]) for o in os_]
    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    avg = round(sum(gaps) / len(gaps)) if gaps else None
    last = dates[-1]; since = (TODAY - last).days
    total = sum(o.get("total") or 0 for o in os_)
    q3 = sum(o.get("total") or 0 for o in os_ if (TODAY - d(o["date"])).days < 90); q2 = sum(o.get("total") or 0 for o in os_ if 90 <= (TODAY - d(o["date"])).days < 180)
    stats[aid] = {"n": len(os_), "total": total, "last": last.isoformat(), "since": since, "avg_gap": avg, "due_in": (avg - since) if avg else None,
                  "quiet": since > max(30, round(avg * 1.5) if avg else 45), "q3": q3, "q2": q2}
cur = [a for a in accounts if a["status"] == "Current"]
def nm(a): return f"{a['name']} / {a.get('town') or '—'}"
facts = {"date": TODAY.isoformat(), "week_start": week_ago.isoformat()}
facts["book"] = {"active": len(cur), "prospects": sum(1 for a in accounts if a["status"] != "Current"), "unclaimed_leads": len(leads),
                 "unassigned_active": sum(1 for a in cur if a.get("owner") in (None, "", "Unassigned"))}
wk = [o for o in conf if d(o["date"]) >= week_ago]; prev = [o for o in conf if two_weeks <= d(o["date"]) < week_ago]
facts["orders_last_7d"] = {"n": len(wk), "total": sum(o.get("total") or 0 for o in wk), "prev_n": len(prev), "prev_total": sum(o.get("total") or 0 for o in prev),
                           "rows": sorted([{"account": nm(ALL[o["account_id"]]) if o["account_id"] in ALL else o.get("account_raw"), "date": o["date"], "total": o.get("total"), "owner": o.get("owner", ""), "source": o.get("source", "")} for o in wk], key=lambda r: r["date"], reverse=True)}
facts["ytd"] = {"orders": len(conf), "tracker_total": sum(o.get("total") or 0 for o in conf), "qbo_invoiced": qbo.get("totals", {}).get("invoiced_2026"), "qbo_open": qbo.get("totals", {}).get("open"), "qbo_past_due": qbo.get("totals", {}).get("past_due"), "qbo_generated": qbo.get("generated")}
risk = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["quiet"]], key=lambda t: -t[1]["total"])
facts["revenue_at_risk"] = {"total": sum(s["total"] for _, s in risk), "rows": [{"account": nm(a), "owner": a.get("owner"), "ytd": s["total"], "days_since": s["since"], "avg_gap": s["avg_gap"], "grade": a.get("grade")} for a, s in risk[:12]]}
due = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["n"] >= 2 and stats[a["id"]]["due_in"] is not None and 0 <= stats[a["id"]]["due_in"] <= 7], key=lambda t: t[1]["due_in"])
facts["due_this_week"] = [{"account": nm(a), "owner": a.get("owner"), "due_in": s["due_in"], "avg_gap": s["avg_gap"], "ytd": s["total"]} for a, s in due]
qa = qbo.get("accounts", {})
pd_ = sorted([(A[i], q) for i, q in qa.items() if i in A and q.get("past_due", 0) > 0], key=lambda t: -t[1]["past_due"])
facts["past_due"] = [{"account": nm(a), "owner": a.get("owner"), "past_due": q["past_due"], "owes": q.get("open_alltime", q.get("open")), "oldest_days": q.get("oldest_due_days")} for a, q in pd_]
rising = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["q3"] >= 8000 and stats[a["id"]]["q2"] > 0 and stats[a["id"]]["q3"] >= stats[a["id"]]["q2"] * 1.4], key=lambda t: -(t[1]["q3"] - t[1]["q2"]))
fading = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["q2"] >= 8000 and stats[a["id"]]["q3"] < stats[a["id"]]["q2"] * 0.5], key=lambda t: -(t[1]["q2"] - t[1]["q3"]))
facts["rising"] = [{"account": nm(a), "owner": a.get("owner"), "last_90": s["q3"], "prior_90": s["q2"]} for a, s in rising[:8]]
facts["fading"] = [{"account": nm(a), "owner": a.get("owner"), "last_90": s["q3"], "prior_90": s["q2"], "days_since": s["since"]} for a, s in fading[:8]]
top = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats], key=lambda t: -t[1]["total"])[:10]
facts["top_accounts"] = [{"account": nm(a), "owner": a.get("owner"), "ytd": s["total"], "n": s["n"], "days_since": s["since"], "avg_gap": s["avg_gap"]} for a, s in top]
facts["unassigned_with_orders"] = [{"account": nm(a), "ytd": stats[a["id"]]["total"]} for a in cur if a.get("owner") in (None, "", "Unassigned") and a["id"] in stats][:10]
facts["once_then_silent"] = [{"account": nm(a), "ytd": stats[a["id"]]["total"], "days_since": stats[a["id"]]["since"]} for a in cur if a["id"] in stats and stats[a["id"]]["n"] == 1 and stats[a["id"]]["since"] >= 45][:8]
wk_acts = [x for x in acts if d(x.get("date")) and d(x["date"]) >= week_ago]
by_user = defaultdict(lambda: defaultdict(int))
for x in wk_acts: by_user[uname.get(x["user"], x["user"])][x.get("type", "?")] += 1
facts["activity_last_7d"] = {u: dict(v) for u, v in by_user.items()}
facts["new_prospects_last_7d"] = [nm(a) for a in accounts if a.get("claimed_at") and d(a["claimed_at"]) and d(a["claimed_at"]) >= week_ago]
facts["open_next_steps"] = [{"account": nm(a), "owner": a.get("owner"), "step": a.get("next_step"), "due": a.get("next_step_due"), "overdue": bool(a.get("next_step_due") and a["next_step_due"] < TODAY.isoformat())} for a in accounts if a.get("next_step")]
stages = defaultdict(int)
for a in accounts:
    if a["status"] != "Current" and a.get("stage") and a["stage"] != "New": stages[a["stage"]] += 1
facts["pipeline_by_stage"] = dict(stages)
r = qbo.get("recon", {})
facts["recon"] = {"matched": r.get("matched"), "orders_without_invoice": len(r.get("orders_without_invoice", [])), "invoices_without_order": len(r.get("invoices_without_order", [])),
                  "amount_differs": len(r.get("amount_differs", [])), "blank_totals_fillable": len(r.get("orders_total_from_qbo", [])), "unmapped": [u["customer"] for u in qbo.get("unmapped", [])]}
facts["placeholders"] = [{"account": nm(ALL[o["account_id"]]) if o["account_id"] in ALL else o.get("account_raw"), "date": o.get("date"), "status": o.get("status")} for o in orders if re.match(r"pend|plan", o.get("status") or "", re.I)]

if "--json" in sys.argv:
    print(json.dumps(facts, indent=1, default=str)); sys.exit()
print(f"CCGL WEEKLY FACTS — {TODAY}  (week since {week_ago})")
b = facts["book"]; print(f"Book: {b['active']} active · {b['prospects']} prospects · {b['unclaimed_leads']} unclaimed leads · {b['unassigned_active']} active accounts unassigned")
w = facts["orders_last_7d"]; print(f"Orders last 7d: {w['n']} / {money(w['total'])}   (prior week {w['prev_n']} / {money(w['prev_total'])})")
for r_ in w["rows"]: print(f"   {r_['date']}  {r_['account']:40s} {money(r_['total']) if r_['total'] else '—':>9}  {r_['owner']}")
y = facts["ytd"]; print(f"2026: {y['orders']} confirmed orders, tracker {money(y['tracker_total'])} · QBO invoiced {money(y['qbo_invoiced'] or 0)} · open {money(y['qbo_open'] or 0)} · past due {money(y['qbo_past_due'] or 0)} (QBO as of {y['qbo_generated']})")
print(f"\nREVENUE AT RISK ({money(facts['revenue_at_risk']['total'])}):")
for r_ in facts["revenue_at_risk"]["rows"]: print(f"   {r_['account']:40s} {money(r_['ytd']):>9}  {r_['days_since']}d since last{(' (usually %dd)' % r_['avg_gap']) if r_['avg_gap'] else ''}  {r_['owner']}  grade {r_['grade'] or '—'}")
print("\nDUE THIS WEEK:")
for r_ in facts["due_this_week"]: print(f"   {r_['account']:40s} due in {r_['due_in']}d (every ~{r_['avg_gap']}d)  {money(r_['ytd'])}  {r_['owner']}")
print("\nPAST DUE (QBO):")
for r_ in facts["past_due"]: print(f"   {r_['account']:40s} {money(r_['past_due']):>9} past due · owes {money(r_['owes'])} · oldest {r_['oldest_days']}d  {r_['owner']}")
print("\nRISING:"); [print(f"   {r_['account']:40s} {money(r_['last_90'])} vs {money(r_['prior_90'])}  {r_['owner']}") for r_ in facts["rising"]]
print("FADING:"); [print(f"   {r_['account']:40s} {money(r_['last_90'])} vs {money(r_['prior_90'])} · {r_['days_since']}d since last  {r_['owner']}") for r_ in facts["fading"]]
print("\nTOP 10 2026:"); [print(f"   {r_['account']:40s} {money(r_['ytd']):>9}  {r_['n']} orders · {r_['days_since']}d since last (~{r_['avg_gap']}d)  {r_['owner']}") for r_ in facts["top_accounts"]]
print("\nUNASSIGNED WITH ORDERS:", ", ".join(f"{r_['account']} ({money(r_['ytd'])})" for r_ in facts["unassigned_with_orders"]) or "none")
print("ONE ORDER THEN SILENT:", ", ".join(f"{r_['account']} ({money(r_['ytd'])}, {r_['days_since']}d)" for r_ in facts["once_then_silent"]) or "none")
print("\nACTIVITY LAST 7d:", {u: v for u, v in facts["activity_last_7d"].items()} or "none logged")
print("NEW PROSPECTS LAST 7d:", ", ".join(facts["new_prospects_last_7d"]) or "none")
print("OPEN NEXT STEPS:"); [print(f"   {'OVERDUE ' if s['overdue'] else ''}{s['account']:40s} {s['step']}  (due {s['due']})  {s['owner']}") for s in facts["open_next_steps"]]
print("PIPELINE BY STAGE:", facts["pipeline_by_stage"] or "nothing past New")
rc = facts["recon"]; print(f"\nQBO vs TRACKER: {rc['matched']} paired · {rc['orders_without_invoice']} orders w/o invoice · {rc['invoices_without_order']} invoices w/o order · {rc['amount_differs']} amounts differ · {rc['blank_totals_fillable']} blank totals fillable · unmapped: {rc['unmapped'] or 'none'}")
print("PLACEHOLDER ROWS:", ", ".join(f"{p['account']} {p['date']} ({p['status']})" for p in facts["placeholders"]) or "none")
