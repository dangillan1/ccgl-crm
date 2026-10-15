#!/usr/bin/env python3
"""Keep the CRM order list reconciled to QuickBooks. QuickBooks is the master for what was sold.

  python3 tools/qbo_sync.py            plan only: prints what would change, writes tools/out/qbo_sync_plan.json
  python3 tools/qbo_sync.py --apply    applies the safe changes to data/orders.json and flags the rest

Run after tools/build_qbo.py (it reads data/qbo.json, where each invoice already carries the CRM account it resolves to).

What it does
  1. Orders that already carry a QuickBooks link (qbo_id) are re-checked against that invoice: amount or date changed in
     QuickBooks -> the order follows.
  2. Every other confirmed, pending or planned order is paired with an unlinked invoice on the same account (or the same
     chain family): exact amount counts most, then the invoice landing 0-14 days after the order, then a close amount.
     The tracker logs list price and the invoice carries the discount, so date proximity outweighs a near-miss on amount.
  3. Paired orders take the invoice's date, amount and location, and are stamped with the invoice number. The tracker's own
     values are kept beside them (tracker_total, tracker_date, tracker_account_id).
  4. Invoices with no order become orders (source QBO). Confirmed orders with nothing invoiced after 21 days are flagged
     review = "no-invoice"; the flag clears the moment an invoice turns up.
  5. Nothing is ever deleted here. Orders deleted in the CRM (settings.deleted_orders) are not recreated.
  6. Orders linked to an invoice that is not in this feed — the 2024-2025 history loaded by tools/qbo_history.py — are
     left exactly as they are: never re-paired, never flagged.
"""
import json, os, re, sys, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data"); OUT = os.path.join(ROOT, "tools", "out"); os.makedirs(OUT, exist_ok=True)
TODAY = dt.date.today().isoformat()
REVIEW_AFTER_DAYS = 21

def load(p, d=None):
    try: return json.load(open(p))
    except FileNotFoundError: return d
def d(s):
    try: return dt.date.fromisoformat(s)
    except Exception: return None
def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())
def is_conf(o): return bool(re.match(r"conf", o.get("status") or "", re.I))
def money(o):
    t = o.get("total"); return t if isinstance(t, (int, float)) and t else None

def run(apply=False, forced_links=None, quiet=False):
    orders = load(os.path.join(DATA, "orders.json"), [])
    accounts = load(os.path.join(DATA, "accounts.json"), []) + load(os.path.join(DATA, "leads.json"), [])
    settings = load(os.path.join(DATA, "settings.json"), {})
    qbo = load(os.path.join(DATA, "qbo.json"), {})
    acc = {a["id"]: a for a in accounts}
    deleted = set(settings.get("deleted_orders", []))
    def family(aid):
        a = acc.get(aid) or {}
        return norm(a.get("parent") or re.sub(r"\s*[-–/].*$", "", a.get("name", ""))) or aid
    def aname(aid):
        a = acc.get(aid); return (a["name"] + (" · " + a["town"] if a.get("town") else "")) if a else str(aid)

    # ---- invoices: one per QuickBooks invoice; a chain-level invoice split by build_qbo lists every candidate location
    inv = {}
    for r in qbo.get("invoices", []):
        x = inv.setdefault(r["id"], {"id": r["id"], "doc": r["doc"], "date": r["date"], "total": r["total"], "customer": r["customer"], "ship_town": r.get("ship_town", ""), "accounts": []})
        if r["account_id"] not in x["accounts"]: x["accounts"].append(r["account_id"])
    invoices = sorted(inv.values(), key=lambda i: i["date"])
    inv_by_acc = defaultdict(list); inv_by_fam = defaultdict(list)
    for i in invoices:
        for a in i["accounts"]: inv_by_acc[a].append(i); inv_by_fam[family(a)].append(i)

    used_i, link = set(), {}       # order id -> (invoice id, kind)
    # ---- 1. stored links (and links forced by the caller)
    for o in orders:
        iid = (forced_links or {}).get(o["id"]) or o.get("qbo_id")
        if iid and iid in inv and iid not in used_i: used_i.add(iid); link[o["id"]] = (iid, "linked")
    # ---- 2. pair the rest
    TOL_EXACT = lambda t: max(1.0, 0.005 * t); TOL_CLOSE = lambda t: max(25.0, 0.02 * t)
    pairs = []
    for o in orders:
        if o["id"] in link or not o.get("account_id") or not d(o.get("date")): continue
        if o.get("qbo_id"): continue                                   # linked to an invoice outside this feed (order history from before 2026): leave it alone
        od = d(o["date"]); seen = set(); t = money(o)
        cands = [(i, True) for i in inv_by_acc.get(o["account_id"], [])] + [(i, False) for i in inv_by_fam.get(family(o["account_id"]), []) if o["account_id"] not in i["accounts"]]
        for i, same in cands:
            if i["id"] in seen or i["id"] in used_i or i["total"] == 0: continue
            seen.add(i["id"]); gap = (d(i["date"]) - od).days
            if gap < -10 or gap > 60: continue
            amt = 0
            if t is not None:
                diff = abs(i["total"] - t); amt = 3 if diff <= TOL_EXACT(i["total"]) else 1 if diff <= TOL_CLOSE(i["total"]) else 0
            date = 2 if 0 <= gap <= 14 else 1 if -10 <= gap <= 30 else 0
            if amt == 0 and not (-5 <= gap <= 21): continue
            if not is_conf(o) and amt == 0: continue              # a pending / planned order only ships when the amount agrees
            pairs.append((amt + date + (0.5 if same else 0), abs(gap), o["id"], i["id"]))
    pairs.sort(key=lambda p: (-p[0], p[1]))
    used_o = set(link)
    for score, g, oid, iid in pairs:
        if oid in used_o or iid in used_i: continue
        used_o.add(oid); used_i.add(iid); link[oid] = (iid, "paired")

    # ---- 3. actions
    plan = []; by_id = {o["id"]: o for o in orders}
    for o in orders:
        if o["id"] in link:
            iid, kind = link[o["id"]]; i = inv[iid]; t = money(o)
            loc = o["account_id"] if o["account_id"] in i["accounts"] else i["accounts"][0]
            changes = {}
            if o.get("qbo_id") != iid or o.get("invoice") != i["doc"]: changes["invoice"] = i["doc"]
            if t is None: act = "FILL TOTAL"
            elif abs(i["total"] - t) <= TOL_EXACT(i["total"]): act = "MATCH"
            else: act = "ADJUST"
            if act != "MATCH" or o.get("date") != i["date"] or o.get("account_id") != loc or not is_conf(o): changes["amount_or_date"] = True
            if o.get("qbo_id") == iid and not changes: continue      # already reconciled and unchanged
            plan.append({"action": act, "order_id": o["id"], "account": aname(loc), "order_date": o.get("date"), "tracker_total": t, "invoice": i["doc"], "invoice_id": iid, "invoice_date": i["date"], "invoice_total": i["total"],
                         "moved_from": aname(o["account_id"]) if loc != o["account_id"] else "", "was_status": o.get("status")})
        elif is_conf(o) and not o.get("qbo_id") and o.get("account_id") and d(o.get("date")) and (dt.date.today() - d(o["date"])).days >= REVIEW_AFTER_DAYS and o.get("review") != "no-invoice":
            plan.append({"action": "REVIEW: no invoice", "order_id": o["id"], "account": aname(o["account_id"]), "order_date": o["date"], "tracker_total": money(o), "invoice": "", "invoice_id": "", "invoice_date": "", "invoice_total": None, "moved_from": "", "was_status": o.get("status")})
    for i in invoices:
        if i["id"] in used_i or i["total"] == 0 or ("Oqbo" + i["id"]) in deleted: continue
        loc = next((a for a in i["accounts"] if i["ship_town"] and norm(acc.get(a, {}).get("town")) and norm(i["ship_town"]) in norm(acc[a]["town"])), i["accounts"][0])
        plan.append({"action": "CREATE from invoice", "order_id": "", "account": aname(loc), "account_id": loc, "order_date": "", "tracker_total": None, "invoice": i["doc"], "invoice_id": i["id"], "invoice_date": i["date"], "invoice_total": i["total"],
                     "moved_from": "", "was_status": "", "customer": i["customer"], "chain_guess": len(i["accounts"]) > 1 and not i["ship_town"]})
    summary = defaultdict(int)
    for p in plan: summary[p["action"]] += 1
    json.dump({"generated": TODAY, "summary": dict(summary), "plan": plan}, open(os.path.join(OUT, "qbo_sync_plan.json"), "w"), indent=1)
    if not quiet:
        print("qbo_sync:", ", ".join(f"{v} {k}" for k, v in sorted(summary.items())) or "nothing to do")

    if not apply: return plan
    # ---- 4. apply
    n_new = 0
    for p in plan:
        if p["action"] == "CREATE from invoice":
            a = acc.get(p["account_id"]) or {}
            orders.append({"id": "Oqbo" + p["invoice_id"], "date": p["invoice_date"], "account_id": p["account_id"], "account_raw": p["customer"], "license": (a.get("license_number") or "").split(" / ")[0],
                           "status": "Confirmed", "contact_name": "", "contact_email": "", "delivery_date": "", "total": p["invoice_total"], "owner": a.get("owner") if a.get("owner") not in (None, "Unassigned") else "",
                           "mix": "", "special": "", "samples": False, "source": "QBO", "created": TODAY, "invoice": p["invoice"], "qbo_id": p["invoice_id"], "invoice_date": p["invoice_date"], "invoiced_total": p["invoice_total"],
                           "reconciled_at": TODAY, "edited_in_crm": True, "edited_by": "qbo", "edited_at": TODAY, "note": "Created from QuickBooks invoice; not in the Order Tracker" + (" — chain-level customer, location is a guess" if p.get("chain_guess") else "")})
            n_new += 1
        elif p["action"].startswith("REVIEW"):
            by_id[p["order_id"]]["review"] = "no-invoice"
        else:
            o = by_id[p["order_id"]]; i = inv[p["invoice_id"]]
            loc = o["account_id"] if o["account_id"] in i["accounts"] else i["accounts"][0]
            if "tracker_total" not in o: o["tracker_total"] = o.get("total")
            if "tracker_date" not in o: o["tracker_date"] = o.get("date")
            if loc != o["account_id"] and "tracker_account_id" not in o: o["tracker_account_id"] = o["account_id"]
            o.update({"account_id": loc, "date": i["date"], "total": i["total"], "status": "Confirmed", "invoice": i["doc"], "qbo_id": i["id"], "invoice_date": i["date"], "invoiced_total": i["total"],
                      "reconciled_at": TODAY, "edited_in_crm": True, "edited_by": "qbo", "edited_at": TODAY})
            o.pop("review", None)
    json.dump(orders, open(os.path.join(DATA, "orders.json"), "w"), indent=1)
    if not quiet: print(f"orders.json written: {len(orders)} orders ({n_new} created from invoices)")
    return plan

if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
