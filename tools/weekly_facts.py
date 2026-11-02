#!/usr/bin/env python3
"""
Prints the week's fact sheet from data/*.json — the same numbers the Insights tab computes live, in a form
that can be read in one pass before writing tools/assessments.json and the Monday email.

  python3 tools/weekly_facts.py            # human-readable
  python3 tools/weekly_facts.py --json     # machine-readable (used by weekly_email.py)
"""
import json, os, sys, re, datetime, glob, math, statistics
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
# the order list runs back to Aug 2024 (tools/qbo_history.py): year-to-date figures use this year's orders, rhythm and
# "last order" use the whole history, and an account with nothing in 6+ months is lapsed rather than merely quiet
YEAR = str(TODAY.year); LAPSED_DAYS = 180
conf_y = [o for o in conf if (o.get("date") or "")[:4] == YEAR]
week_ago = TODAY - datetime.timedelta(days=7); two_weeks = TODAY - datetime.timedelta(days=14)

# per-account order stats (mirrors index() in the app)
by_acc = defaultdict(list)
for o in conf: by_acc[o["account_id"]].append(o)
stats = {}
for aid, os_ in by_acc.items():
    os_.sort(key=lambda o: o["date"]); dates = [d(o["date"]) for o in os_]
    gaps = [g for g in ((dates[i] - dates[i - 1]).days for i in range(1, len(dates))) if g > 0]
    rg = sorted(g for g in gaps[-7:] if g <= LAPSED_DAYS)                       # rhythm = median gap of the last 8 orders; a pause longer than LAPSED_DAYS is a lapse, not a rhythm (same as the app)
    avg = math.floor(statistics.median(rg) + 0.5) if rg else None
    last = dates[-1]; since = (TODAY - last).days
    ytd = [o for o in os_ if (o.get("date") or "")[:4] == YEAR]
    total = sum(o.get("total") or 0 for o in ytd); total_all = sum(o.get("total") or 0 for o in os_)
    q3 = sum(o.get("total") or 0 for o in os_ if (TODAY - d(o["date"])).days < 90); q2 = sum(o.get("total") or 0 for o in os_ if 90 <= (TODAY - d(o["date"])).days < 180)
    t12 = [o for o in os_ if (TODAY - d(o["date"])).days < 365]
    stats[aid] = {"n": len(ytd), "total": total, "n_all": len(os_), "total_all": total_all, "first": dates[0].isoformat(), "last": last.isoformat(), "since": since, "avg_gap": avg, "due_in": (avg - since) if avg else None,
                  "quiet": since > max(30, round(avg * 1.5) if avg else 45), "lapsed": since > LAPSED_DAYS, "q3": q3, "q2": q2, "rev12": sum(o.get("total") or 0 for o in t12), "n12": len(t12)}
# star rating — the same formula as starIndex() in index.html (weights / bands from settings.stars)
STAR_W = {"revenue": 50, "frequency": 25, "rhythm": 15, "payment": 10, "trend": 10}; STAR_BANDS = [80, 60, 40, 20]
_sc = settings.get("stars") or {}
for k in STAR_W:
    try: v = float((_sc.get("w") or {})[k]); STAR_W[k] = v if v >= 0 else STAR_W[k]
    except (KeyError, TypeError, ValueError): pass
try:
    b = [float(x) for x in _sc.get("bands")]
    if len(b) == 4 and b[0] > b[1] > b[2] > b[3]: STAR_BANDS = b
except (TypeError, ValueError): pass
_qa = qbo.get("accounts", {})
STAR_TOP = max([s["rev12"] for a_ in accounts if a_["id"] in stats for s in [stats[a_["id"]]]] + [0])
def star_of(a):
    s = stats.get(a["id"]);
    if not s or s["rev12"] <= 0: return 0, None
    pct = s["rev12"] / STAR_TOP * 100 if STAR_TOP else 0; n = s["n12"]; r = s["avg_gap"] or 45; dd = s["since"]
    q = _qa.get(a["id"], {}); pd_ = q.get("past_due") or 0; pdd = q.get("oldest_due_days") or 0
    rev = STAR_W["revenue"] * (1 if pct >= 50 else .8 if pct >= 25 else .6 if pct >= 12 else .4 if pct >= 6 else .2 if pct >= 3 else .1)
    freq = STAR_W["frequency"] * (1 if n >= 12 else .8 if n >= 8 else .6 if n >= 5 else .4 if n >= 3 else .2 if n >= 2 else .08)
    rh = STAR_W["rhythm"] * (1 if dd <= r else 2 / 3 if dd <= r * 1.5 else 1 / 3 if dd <= r * 2 else 0)
    pay = STAR_W["payment"] * ((.5 if pdd < 30 else 0) if pd_ > 0 else 1)
    tr = STAR_W["trend"] if (s["q3"] >= 8000 and s["q2"] > 0 and s["q3"] >= s["q2"] * 1.4) else -STAR_W["trend"] if (s["q2"] >= 8000 and s["q3"] < s["q2"] * 0.5) else 0
    sc = max(0, min(100, round(rev + freq + rh + pay + tr)))
    return (5 if sc >= STAR_BANDS[0] else 4 if sc >= STAR_BANDS[1] else 3 if sc >= STAR_BANDS[2] else 2 if sc >= STAR_BANDS[3] else 1), sc
def stars_txt(a): n, sc = star_of(a); return ("★" * n + "☆" * (5 - n)) if n else "—"
cur = [a for a in accounts if a["status"] == "Current"]
def nm(a): return f"{a['name']} / {a.get('town') or '—'}"
facts = {"date": TODAY.isoformat(), "week_start": week_ago.isoformat()}
facts["book"] = {"active": len(cur), "prospects": sum(1 for a in accounts if a["status"] != "Current"), "unclaimed_leads": len(leads),
                 "unassigned_active": sum(1 for a in cur if a.get("owner") in (None, "", "Unassigned"))}
wk = [o for o in conf if d(o["date"]) >= week_ago]; prev = [o for o in conf if two_weeks <= d(o["date"]) < week_ago]
facts["orders_last_7d"] = {"n": len(wk), "total": sum(o.get("total") or 0 for o in wk), "prev_n": len(prev), "prev_total": sum(o.get("total") or 0 for o in prev),
                           "rows": sorted([{"account": nm(ALL[o["account_id"]]) if o["account_id"] in ALL else o.get("account_raw"), "date": o["date"], "total": o.get("total"), "owner": o.get("owner", ""), "source": o.get("source", "")} for o in wk], key=lambda r: r["date"], reverse=True)}
facts["ytd"] = {"year": YEAR, "orders": len(conf_y), "tracker_total": sum(o.get("total") or 0 for o in conf_y), "orders_all_years": len(conf), "first_order": min(o["date"] for o in conf) if conf else None, "qbo_invoiced": qbo.get("totals", {}).get("invoiced_2026"), "qbo_open": qbo.get("totals", {}).get("open"), "qbo_past_due": qbo.get("totals", {}).get("past_due"), "qbo_generated": qbo.get("generated")}
risk = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["quiet"] and not stats[a["id"]]["lapsed"]], key=lambda t: -t[1]["total"])
facts["revenue_at_risk"] = {"total": sum(s["total"] for _, s in risk), "rows": [{"account": nm(a), "owner": a.get("owner"), "ytd": s["total"], "days_since": s["since"], "avg_gap": s["avg_gap"], "stars": star_of(a)[0], "score": star_of(a)[1]} for a, s in risk[:12]]}
due = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["n_all"] >= 2 and stats[a["id"]]["due_in"] is not None and 0 <= stats[a["id"]]["due_in"] <= 7], key=lambda t: t[1]["due_in"])
facts["due_this_week"] = [{"account": nm(a), "owner": a.get("owner"), "due_in": s["due_in"], "avg_gap": s["avg_gap"], "ytd": s["total"]} for a, s in due]
qa = qbo.get("accounts", {})
pd_ = sorted([(A[i], q) for i, q in qa.items() if i in A and q.get("past_due", 0) > 0], key=lambda t: -t[1]["past_due"])
facts["past_due"] = [{"account": nm(a), "owner": a.get("owner"), "past_due": q["past_due"], "owes": q.get("open_alltime", q.get("open")), "oldest_days": q.get("oldest_due_days")} for a, q in pd_]
rising = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["q3"] >= 8000 and stats[a["id"]]["q2"] > 0 and stats[a["id"]]["q3"] >= stats[a["id"]]["q2"] * 1.4], key=lambda t: -(t[1]["q3"] - t[1]["q2"]))
fading = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["q2"] >= 8000 and stats[a["id"]]["q3"] < stats[a["id"]]["q2"] * 0.5], key=lambda t: -(t[1]["q2"] - t[1]["q3"]))
facts["rising"] = [{"account": nm(a), "owner": a.get("owner"), "last_90": s["q3"], "prior_90": s["q2"]} for a, s in rising[:8]]
facts["fading"] = [{"account": nm(a), "owner": a.get("owner"), "last_90": s["q3"], "prior_90": s["q2"], "days_since": s["since"]} for a, s in fading[:8]]
top = sorted([(a, stats[a["id"]]) for a in cur if a["id"] in stats and stats[a["id"]]["n"]], key=lambda t: -t[1]["total"])[:10]
facts["top_accounts"] = [{"account": nm(a), "owner": a.get("owner"), "ytd": s["total"], "n": s["n"], "days_since": s["since"], "avg_gap": s["avg_gap"], "stars": star_of(a)[0], "score": star_of(a)[1]} for a, s in top]
facts["stars"] = {"top_account": next((nm(a) for a in cur if a["id"] in stats and stats[a["id"]]["rev12"] == STAR_TOP), None), "top_rev12": STAR_TOP, "bands": STAR_BANDS, "weights": STAR_W,
                  "distribution": {str(n): sum(1 for a in cur if star_of(a)[0] == n) for n in (5, 4, 3, 2, 1, 0)},
                  "five_star": [{"account": nm(a), "owner": a.get("owner"), "score": star_of(a)[1], "rev12": stats[a["id"]]["rev12"], "n12": stats[a["id"]]["n12"]} for a in sorted(cur, key=lambda a: -(star_of(a)[1] or 0)) if star_of(a)[0] == 5]}
facts["unassigned_with_orders"] = [{"account": nm(a), "ytd": stats[a["id"]]["total"]} for a in cur if a.get("owner") in (None, "", "Unassigned") and a["id"] in stats and stats[a["id"]]["n"]][:10]
facts["once_then_silent"] = [{"account": nm(a), "ytd": stats[a["id"]]["total_all"], "days_since": stats[a["id"]]["since"]} for a in cur if a["id"] in stats and stats[a["id"]]["n_all"] == 1 and stats[a["id"]]["since"] >= 45 and not stats[a["id"]]["lapsed"]][:8]
lapsed = sorted([(a, stats[a["id"]]) for a in accounts if a["id"] in stats and stats[a["id"]]["lapsed"]], key=lambda t: -t[1]["total_all"])
facts["lapsed"] = {"n": len(lapsed), "rows": [{"account": nm(a), "owner": a.get("owner"), "status": "Active" if a["status"] == "Current" else a["status"], "lifetime": s["total_all"], "n_all": s["n_all"], "last": s["last"], "days_since": s["since"], "first": s["first"]} for a, s in lapsed[:15]]}
facts["never_ordered"] = [nm(a) for a in cur if a["id"] not in stats]
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
# payment terms: every account is Net 30 unless accounts.json says otherwise (set on the profile; tools/terms_sync.py walks the make-up path)
STEP_SHORT = {1: "COD", 2: "50 / 50", 3: "Net 30 trial"}
def terms_row(a):
    t = a.get("terms") or {}; plan = t.get("plan"); step = min(3, max(1, int(t.get("step") or 1))) if plan == "makeup" else None
    lab = (f"Make-up {step}/3 · {STEP_SHORT[step]}" if plan == "makeup" else (t.get("label") or "Custom terms") if plan == "custom" else "Terms TBD" if plan == "tbd" else "COD")
    q = qa.get(a["id"], {})
    return {"account": nm(a), "owner": a.get("owner"), "plan": plan, "step": step, "label": lab, "since": t.get("since"), "reason": t.get("reason"), "flag": t.get("flag"),
            "set_by": t.get("set_by"), "owes": q.get("open_alltime", q.get("open")) or 0, "past_due": q.get("past_due") or 0}
facts["special_terms"] = sorted([terms_row(a) for a in accounts if (a.get("terms") or {}).get("plan") in ("cod", "makeup", "custom", "tbd")], key=lambda r: (0 if r["flag"] else 1, r["plan"], -r["owes"]))
# contacts confirmed (tools/contacts_sync.py has the rule; same as ccState in the app)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import contacts_sync
cc = contacts_sync.states(accounts, contacts, settings); cc_days = contacts_sync.cfg(settings)["days"]
cc_rows = sorted([(a, cc[a["id"]]) for a in cur if cc[a["id"]]["state"] != "confirmed"], key=lambda t: (contacts_sync.ORDER[t[1]["state"]], -stats.get(t[0]["id"], {}).get("total", 0)))
facts["contacts_confirmed"] = {"active": len(cur), "days": cc_days, **{k: sum(1 for a in cur if cc[a["id"]]["state"] == k) for k in contacts_sync.ORDER},
                               "rows": [{"account": nm(a), "owner": a.get("owner"), "state": s["state"], "label": s["label"], "why": contacts_sync.why(s, lambda i: uname.get(i, i)), "ytd": stats.get(a["id"], {}).get("total", 0)} for a, s in cc_rows[:10]],
                               "auto_tasks": sum(1 for a in cur if re.match(r"^Confirm contacts", a.get("next_step") or ""))}
facts["placeholders"] = [{"account": nm(ALL[o["account_id"]]) if o["account_id"] in ALL else o.get("account_raw"), "date": o.get("date"), "status": o.get("status")} for o in orders if re.match(r"pend|plan", o.get("status") or "", re.I)]

if "--json" in sys.argv:
    print(json.dumps(facts, indent=1, default=str)); sys.exit()
print(f"CCGL WEEKLY FACTS — {TODAY}  (week since {week_ago})")
b = facts["book"]; print(f"Book: {b['active']} active · {b['prospects']} prospects · {b['unclaimed_leads']} unclaimed leads · {b['unassigned_active']} active accounts unassigned")
w = facts["orders_last_7d"]; print(f"Orders last 7d: {w['n']} / {money(w['total'])}   (prior week {w['prev_n']} / {money(w['prev_total'])})")
for r_ in w["rows"]: print(f"   {r_['date']}  {r_['account']:40s} {money(r_['total']) if r_['total'] else '—':>9}  {r_['owner']}")
y = facts["ytd"]; print(f"{y['year']}: {y['orders']} confirmed orders, tracker {money(y['tracker_total'])} · QBO invoiced {money(y['qbo_invoiced'] or 0)} · open {money(y['qbo_open'] or 0)} · past due {money(y['qbo_past_due'] or 0)} (QBO as of {y['qbo_generated']}) · order history: {y['orders_all_years']} orders since {y['first_order']}")
print(f"\nREVENUE AT RISK ({money(facts['revenue_at_risk']['total'])}):")
for r_ in facts["revenue_at_risk"]["rows"]: print(f"   {r_['account']:40s} {money(r_['ytd']):>9}  {r_['days_since']}d since last{(' (usually %dd)' % r_['avg_gap']) if r_['avg_gap'] else ''}  {r_['owner']}  {('★' * r_['stars'] + '☆' * (5 - r_['stars'])) if r_['stars'] else 'no stars'}")
print("\nDUE THIS WEEK:")
for r_ in facts["due_this_week"]: print(f"   {r_['account']:40s} due in {r_['due_in']}d (every ~{r_['avg_gap']}d)  {money(r_['ytd'])}  {r_['owner']}")
print("\nPAST DUE (QBO):")
for r_ in facts["past_due"]: print(f"   {r_['account']:40s} {money(r_['past_due']):>9} past due · owes {money(r_['owes'])} · oldest {r_['oldest_days']}d  {r_['owner']}")
print("\nRISING:"); [print(f"   {r_['account']:40s} {money(r_['last_90'])} vs {money(r_['prior_90'])}  {r_['owner']}") for r_ in facts["rising"]]
print("FADING:"); [print(f"   {r_['account']:40s} {money(r_['last_90'])} vs {money(r_['prior_90'])} · {r_['days_since']}d since last  {r_['owner']}") for r_ in facts["fading"]]
print(f"\nTOP 10 {YEAR}:"); [print(f"   {r_['account']:40s} {money(r_['ytd']):>9}  {r_['n']} orders · {r_['days_since']}d since last (~{r_['avg_gap']}d)  {r_['owner']}  {('★' * r_['stars'] + '☆' * (5 - r_['stars'])) if r_['stars'] else '—'} {r_['score'] or ''}") for r_ in facts["top_accounts"]]
sd = facts["stars"]; print(f"STAR RATINGS (12-mo revenue as % of #1 {sd['top_account']} {money(sd['top_rev12'])}, orders, rhythm, pays on time, trend; bands {'/'.join(str(int(b)) for b in sd['bands'])}): " + " · ".join(f"{n}★ {c}" for n, c in sd["distribution"].items() if n != '0') + f" · no rating {sd['distribution']['0']}")
print("   5★:", ", ".join(f"{r_['account']} ({r_['score']})" for r_ in sd["five_star"]) or "none")
print("\nUNASSIGNED WITH ORDERS:", ", ".join(f"{r_['account']} ({money(r_['ytd'])})" for r_ in facts["unassigned_with_orders"]) or "none")
print("ONE ORDER THEN SILENT:", ", ".join(f"{r_['account']} ({money(r_['ytd'])}, {r_['days_since']}d)" for r_ in facts["once_then_silent"]) or "none")
lp = facts["lapsed"]; print(f"\nLAPSED — no order in {LAPSED_DAYS}+ days ({lp['n']} accounts; win-back candidates, biggest lifetime first):")
for r_ in lp["rows"]: print(f"   {r_['account']:40s} {money(r_['lifetime']):>9} lifetime · {r_['n_all']} orders · last {r_['last']} ({r_['days_since']}d)  {r_['owner']}  [{r_['status']}]")
print(f"NEVER ORDERED ({len(facts['never_ordered'])} \"Active\" accounts with no invoice ever):", ", ".join(facts["never_ordered"]) or "none")
print("\nACTIVITY LAST 7d:", {u: v for u, v in facts["activity_last_7d"].items()} or "none logged")
print("NEW PROSPECTS LAST 7d:", ", ".join(facts["new_prospects_last_7d"]) or "none")
print("OPEN NEXT STEPS:"); [print(f"   {'OVERDUE ' if s['overdue'] else ''}{s['account']:40s} {s['step']}  (due {s['due']})  {s['owner']}") for s in facts["open_next_steps"]]
print("PIPELINE BY STAGE:", facts["pipeline_by_stage"] or "nothing past New")
rc = facts["recon"]; print(f"\nQBO vs TRACKER: {rc['matched']} paired · {rc['orders_without_invoice']} orders w/o invoice · {rc['invoices_without_order']} invoices w/o order · {rc['amount_differs']} amounts differ · {rc['blank_totals_fillable']} blank totals fillable · unmapped: {rc['unmapped'] or 'none'}")
ccf = facts["contacts_confirmed"]; print(f"\nCONTACTS CONFIRMED: {ccf['confirmed']} of {ccf['active']} active (every {ccf['days']}d) · to confirm: {ccf['never']} never, {ccf['changed']} changed, {ccf['expired']} expired, {ccf['review']} flagged · {ccf['missing']} missing a Buyer / Finance / Intake contact · {ccf['auto_tasks']} open 'Confirm contacts' next steps")
for r_ in ccf["rows"]: print(f"   {r_['account']:40s} {r_['label']:34s} {r_['why']}  {r_['owner']}  {money(r_['ytd'])}")
print(f"\nSPECIAL TERMS ({len(facts['special_terms'])} accounts not on Net 30):")
for r_ in facts["special_terms"]: print(f"   {r_['account']:40s} {r_['label']:22s} since {r_['since'] or '?'}  {r_['owner']}  owes {money(r_['owes'])}{(' · %s past due' % money(r_['past_due'])) if r_['past_due'] else ''}{(' · ' + r_['reason']) if r_['reason'] else ''}{(' · NEEDS A DECISION: ' + r_['flag']) if r_['flag'] else ''}")
print("PLACEHOLDER ROWS:", ", ".join(f"{p['account']} {p['date']} ({p['status']})" for p in facts["placeholders"]) or "none")
