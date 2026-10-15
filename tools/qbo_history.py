#!/usr/bin/env python3
"""Load the order history from before the 2026 Order Tracker: every QuickBooks invoice dated before 2026-01-01
becomes a confirmed order (source QBO), so the CRM's order list, Last order column and account timelines run back to
the first wholesale invoice (Aug 2024).

  python3 tools/qbo_history.py            plan only: prints what would be added, writes tools/out/qbo_history_plan.json
  python3 tools/qbo_history.py --apply    adds the orders to data/orders.json

Input: tools/qbo_raw/invoices_pre2026.json — a one-off pull normalised by
  python3 tools/qbo_ingest.py invoices <query result> --out invoices_pre2026.json
  (SELECT Id, TxnDate, DocNumber, TotalAmt, Balance, DueDate, CustomerRef, ShipAddr FROM Invoice WHERE TxnDate < '2026-01-01')

Rules
  - Customers resolve to accounts exactly as the weekly feed does (tools/qbo_resolve.py + tools/qbo_map.json).
  - A chain-level invoice with no ship-to address goes to the location that customer usually ships to (its other
    invoices, 2024-2026); if there is no such evidence, the location with the most orders already; and it is flagged
    review = "location" so the team can move it (Edit order) — the Orders page shows a "location?" pill.
  - Safe to re-run: an invoice already in orders.json (by qbo_id) or deleted in the CRM (settings.deleted_orders) is
    skipped; $0 invoices are not orders; a customer with no CRM account is reported, never invented; a customer mapped
    to null in qbo_map.json (a store that closed) is skipped quietly; an unclaimed lead is never promoted from here. Account statuses are not touched — a Prospect with 2025 orders is a former customer,
    and the app says so on its page.
"""
import json, os, sys, datetime as dt
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qbo_resolve import Resolver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data"); RAW = os.path.join(ROOT, "tools", "qbo_raw"); OUT = os.path.join(ROOT, "tools", "out"); os.makedirs(OUT, exist_ok=True)
TODAY = dt.date.today().isoformat()
TRACKER_START = "2026-01-01"

def load(p, d=None):
    try: return json.load(open(p))
    except FileNotFoundError: return d

def run(apply=False):
    invoices = load(os.path.join(RAW, "invoices_pre2026.json")) or sys.exit("no tools/qbo_raw/invoices_pre2026.json")
    accounts = load(os.path.join(DATA, "accounts.json")); leads = load(os.path.join(DATA, "leads.json"), [])
    orders = load(os.path.join(DATA, "orders.json"), []); settings = load(os.path.join(DATA, "settings.json"), {})
    cmap = {k: v for k, v in (load(os.path.join(ROOT, "tools", "qbo_map.json")) or {}).items() if not k.startswith("_")}
    feed = load(os.path.join(DATA, "qbo.json"), {}).get("invoices", [])
    acc = {a["id"]: a for a in accounts}; lead_ids = {l["id"] for l in leads}
    R = Resolver(accounts, leads, cmap)
    have = {o.get("qbo_id") for o in orders if o.get("qbo_id")} | {o["id"] for o in orders}
    deleted = set(settings.get("deleted_orders", []))
    orders_on = Counter(o["account_id"] for o in orders if o.get("account_id"))
    def aname(aid):
        a = acc.get(aid); return (a["name"] + (" · " + a["town"] if a.get("town") else "")) if a else str(aid)

    # first pass: where does each customer usually ship? (its own invoices that did resolve to one location, 2024-2026)
    usual = defaultdict(Counter)
    for r in feed:
        if not r.get("split"): usual[r["customer"]][r["account_id"]] += 1
    resolved = {}
    for inv in sorted(invoices, key=lambda i: i["date"]):
        target, split = R.resolve(inv["customer"], inv.get("ship_town"))
        resolved[inv["id"]] = (target, split)
        if target is not None and not split and target["id"] in acc: usual[inv["customer"]][target["id"]] += 1

    plan, skipped = [], defaultdict(list)
    unmapped = defaultdict(lambda: {"n": 0, "total": 0.0, "ship_towns": set(), "lead": ""})
    for inv in sorted(invoices, key=lambda i: i["date"]):
        target, split = resolved[inv["id"]]
        if inv["total"] == 0: skipped["zero"].append(inv["doc"]); continue
        if inv["id"] in have or ("Oqbo" + inv["id"]) in have: skipped["already"].append(inv["doc"]); continue
        if ("Oqbo" + inv["id"]) in deleted: skipped["deleted"].append(inv["doc"]); continue
        if R.ignored(inv["customer"]): skipped["ignored (closed business, null in qbo_map.json)"].append(inv["doc"]); continue
        if target is None or (not split and target["id"] in lead_ids):
            u = unmapped[inv["customer"]]; u["n"] += 1; u["total"] += inv["total"]; u["ship_towns"].add(inv.get("ship_town") or "")
            if target is not None: u["lead"] = target["name"]
            continue
        guess = ""
        if split:
            cands = [a for a in target if a["id"] in acc]
            if not cands:
                u = unmapped[inv["customer"]]; u["n"] += 1; u["total"] += inv["total"]; u["ship_towns"].add(inv.get("ship_town") or ""); continue
            by_usual = sorted(cands, key=lambda a: (-usual[inv["customer"]][a["id"]], -orders_on[a["id"]]))
            loc = by_usual[0]
            guess = "usual ship-to" if usual[inv["customer"]][loc["id"]] else "most orders" if orders_on[loc["id"]] else "first in list"
        else: loc = target
        plan.append({"invoice_id": inv["id"], "invoice": inv["doc"], "date": inv["date"], "total": inv["total"], "customer": inv["customer"], "ship_town": inv.get("ship_town", ""),
                     "account_id": loc["id"], "account": aname(loc["id"]), "chain_guess": guess, "candidates": [aname(a["id"]) for a in target] if split else []})
    by_year = Counter(p["date"][:4] for p in plan)
    report = {"generated": TODAY, "to_add": len(plan), "by_year": dict(sorted(by_year.items())), "total": round(sum(p["total"] for p in plan), 2),
              "chain_guesses": [p for p in plan if p["chain_guess"]], "skipped": {k: len(v) for k, v in skipped.items()},
              "unmapped": [{"customer": k, "n": v["n"], "total": round(v["total"], 2), "ship_towns": sorted(t for t in v["ship_towns"] if t), "unclaimed_lead": v["lead"]} for k, v in sorted(unmapped.items())],
              "plan": plan}
    json.dump(report, open(os.path.join(OUT, "qbo_history_plan.json"), "w"), indent=1)
    print(f"qbo_history: {len(plan)} invoices to add as orders ({', '.join(f'{v} in {k}' for k, v in sorted(by_year.items()))}), ${report['total']:,.0f}"
          + (f" · skipped {', '.join(f'{n} {k}' for k, n in report['skipped'].items())}" if skipped else ""))
    gs = Counter((p["customer"], p["account"]) for p in report["chain_guesses"])
    for (c, a), n in sorted(gs.items()): print(f"  LOCATION GUESS: {c} -> {a}  ({n} invoice{'s' if n > 1 else ''}, no ship-to address; flagged for review)")
    for u in report["unmapped"]: print(f"  UNMAPPED: {u['customer']} — {u['n']} invoice{'s' if u['n'] > 1 else ''}, ${u['total']:,.0f}" + (f" (matches unclaimed lead '{u['unclaimed_lead']}')" if u["unclaimed_lead"] else "") + (f" · ships to {', '.join(u['ship_towns'])}" if u["ship_towns"] else ""))
    if not apply: return report

    for p in plan:
        a = acc[p["account_id"]]
        orders.append({"id": "Oqbo" + p["invoice_id"], "date": p["date"], "account_id": p["account_id"], "account_raw": p["customer"], "license": (a.get("license_number") or "").split(" / ")[0],
                       "status": "Confirmed", "contact_name": "", "contact_email": "", "delivery_date": "", "total": p["total"], "owner": "",
                       "mix": "", "special": "", "samples": False, "source": "QBO", "created": TODAY, "invoice": p["invoice"], "qbo_id": p["invoice_id"], "invoice_date": p["date"], "invoiced_total": p["total"],
                       "reconciled_at": TODAY, "edited_in_crm": True, "edited_by": "qbo", "edited_at": TODAY,
                       "note": "From QuickBooks invoice — before the 2026 Order Tracker" + (f"; chain-level customer with no ship-to address, location is a guess ({p['chain_guess']})" if p["chain_guess"] else ""),
                       **({"review": "location", "location_guess": True} if p["chain_guess"] else {})})
    orders.sort(key=lambda o: (o.get("date") or "", o.get("row", 0) or 0))
    json.dump(orders, open(os.path.join(DATA, "orders.json"), "w"), indent=1)
    print(f"orders.json written: {len(orders)} orders ({len(plan)} added from invoices before {TRACKER_START})")
    return report

if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
