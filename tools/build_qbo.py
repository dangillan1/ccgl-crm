#!/usr/bin/env python3
"""
Builds data/qbo.json — the read-only QuickBooks feed the CRM shows on every account.

Inputs (written from QuickBooks by Claude via the ccgl-qbo connection; no QBO credentials touch the app):
  tools/qbo_raw/invoices_2026.json   one row per invoice: id, doc, date, due, total, balance, customer_id, customer, ship_town, ship
  tools/qbo_raw/customers.json       one row per customer: id, name, balance   (all-time open balance)
  tools/qbo_map.json                 QBO customer name -> CRM account id (or list of ids for chain-level customers)

How an invoice finds its account:
  1. qbo_map.json override for the customer name (single id, or a list -> the location whose town matches the ship-to;
     no town match -> split evenly across the list, flagged "split").
  2. Otherwise name match against every book account; several locations -> ship-to town decides; still ambiguous -> split.
  3. No match at all -> listed under "unmapped" so it shows up on Insights instead of vanishing.

Side effects on data/accounts.json (deliberate, per Dan): an account with a 2026 invoice is a customer, so a Prospect
that QuickBooks has invoiced becomes Active (in_qbo=true keeps it Active through Sheet re-imports); QBO customers with no
CRM account at all listed in qbo_map.json under a new "AQBO…" id are created as Active accounts.

Output data/qbo.json:
  generated, invoices[] (with account_id / split), accounts{id: {invoiced_2026, n, open, past_due, oldest_due_days,
  last_invoice, first_invoice, split_share}}, unmapped[], recon{orders_without_invoice[], invoices_without_order[],
  orders_total_from_qbo[]}, totals{}
"""
import json, os, re, sys, datetime
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data"); RAW = os.path.join(ROOT, "tools", "qbo_raw")
TODAY = datetime.date.today()

def load(p, d=None):
    try: return json.load(open(p))
    except FileNotFoundError: return d

invoices = load(os.path.join(RAW, "invoices_2026.json")) or sys.exit("no tools/qbo_raw/invoices_2026.json")
customers = load(os.path.join(RAW, "customers.json"), [])
cmap = {k: v for k, v in (load(os.path.join(ROOT, "tools", "qbo_map.json")) or {}).items() if not k.startswith("_")}
accounts = load(os.path.join(DATA, "accounts.json"))
orders = load(os.path.join(DATA, "orders.json"), [])
acc = {a["id"]: a for a in accounts}

STOP = {"llc", "inc", "co", "corp", "company", "the", "of", "ma", "dba", "d/b/a", "cannabis", "dispensary", "wellness", "organics",
        "organic", "medicine", "collective", "naturals", "retail", "and", "&"}
def tok(s): return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower().replace("&", " and ")) if t not in STOP}
def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())
def same_town(a, b): a, b = norm(a), norm(b); return bool(a and b) and (a == b or a in b or b in a)

# ---- 1. new accounts named in the map but not yet in the CRM (id starts with AQBO)
new_defs = {"AQBO0001": {"name": "LTT Trading Co", "town": "Worcester", "address": "64 Beacon St B108"}}
for aid, d in new_defs.items():
    if aid in acc: continue
    a = {"id": aid, "sheet_key": "", "source": "CRM (QBO)", "parent": d["name"], "stage": "Customer", "stage_entered": TODAY.isoformat(),
         "delivery_notes": "", "standing_requests": "", "license_number": "", "tags": ["From QBO", "Not in Master Sheet"], "flags": ["not-in-master"],
         "created": TODAY.isoformat(), "name": d["name"], "town": d["town"], "address": d.get("address", ""), "license_type": "", "status": "Current",
         "grade": "", "owner": "Unassigned", "last_contact": "", "next_contact": "", "claimed": True, "claimed_by": "dan", "claimed_at": TODAY.isoformat(), "in_qbo": True}
    accounts.append(a); acc[aid] = a
    print(f"created account {aid} {d['name']} / {d['town']}")

# ---- 2. resolve each invoice to an account (unclaimed leads count too: an invoiced lead is a customer)
leads = load(os.path.join(DATA, "leads.json"), [])
TOWN_NAMES = sorted({a["town"].strip() for a in accounts + leads if a.get("town")} | {"Boston", "Worcester", "Springfield", "Indian Orchard", "New Bedford"}, key=len, reverse=True)
_town_re = re.compile(r"\b(" + "|".join(re.escape(t) for t in TOWN_NAMES if t) + r")\b", re.I)
def strip_towns(s): return _town_re.sub(" ", s or "")
def name_tok(s): return tok(strip_towns(s)) or tok(s)                     # name without the town words (unless that leaves nothing)
by_family = defaultdict(list)
for a in accounts + leads:
    for t in name_tok(a["name"]) | (tok(a.get("parent", "")) - {""}): by_family[t].append(a)

def candidates(customer):
    qn = name_tok(customer)
    if not qn: return []
    scored = []
    for a in {x["id"]: x for t in qn for x in by_family.get(t, [])}.values():
        an = name_tok(a["name"]) or name_tok(a.get("parent", "")); inter = qn & an
        if not inter: continue
        scored.append((len(inter) / len(qn | an), a))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < 0.34: return []
    LEGAL = {"llc", "inc", "co", "corp", "company", "the", "of", "ma", "dba"}
    raw = lambda x: {t for t in re.findall(r"[a-z0-9]+", (x or "").lower()) if t not in LEGAL}
    exact = [a for s, a in scored if raw(a["name"]) == raw(customer)]        # 'Peak Cannabis Co' -> 'Peak Cannabis', not 'Peak Collective'
    if len(exact) == 1: return exact
    best = scored[0][0]
    return [a for s, a in scored if s >= best - 1e-9]                      # the family: same name, several towns

def acc_town(a): return a.get("town") or next((t for t in TOWN_NAMES if t and re.search(r"\b" + re.escape(t) + r"\b", a["name"], re.I)), "")
def pick(cands, customer, ship_town):
    """one account, or a list to split across"""
    if len(cands) == 1: return cands[0], False
    if ship_town:
        hit = [a for a in cands if same_town(acc_town(a), ship_town)]
        if len(hit) == 1: return hit[0], False
    towns_in_name = [a for a in cands if acc_town(a) and norm(acc_town(a)) in norm(customer)]
    if len(towns_in_name) == 1: return towns_in_name[0], False
    return cands, True

resolved, unmapped = [], defaultdict(lambda: {"n": 0, "total": 0.0, "open": 0.0, "ship_towns": set()})
for inv in invoices:
    cust = inv["customer"]; target = None; split = False
    if cust in cmap:
        v = cmap[cust]; ids = v if isinstance(v, list) else [v]
        lead_by_id = {l["id"]: l for l in leads}
        missing = [i for i in ids if i not in acc and i not in lead_by_id]
        if missing: sys.exit(f"qbo_map.json: {cust} -> unknown account id(s) {missing}")
        target, split = pick([acc.get(i) or lead_by_id[i] for i in ids], cust, inv.get("ship_town"))
    else:
        c = candidates(cust)
        if c: target, split = pick(c, cust, inv.get("ship_town"))
    if target is None:
        u = unmapped[cust]; u["n"] += 1; u["total"] += inv["total"]; u["open"] += inv["balance"]; u["ship_towns"].add(inv.get("ship_town") or "")
        continue
    targets = target if split else [target]
    share = 1.0 / len(targets)
    for a in targets:
        if a["id"] not in acc:                                              # it was an unclaimed lead: it's a customer now
            leads.remove(a); accounts.append(a); acc[a["id"]] = a
            if re.search(r"\.[a-z]{2,4}$", a["name"]): a["name"] = cust      # domain-named lead takes the QBO customer name
            if not a.get("town") and inv.get("ship_town"): a["town"] = inv["ship_town"]
            a["claimed"] = True; a["claimed_by"] = "dan"; a["claimed_at"] = TODAY.isoformat(); a["tags"] = sorted(set(a.get("tags", []) + ["From QBO"]))
            a["flags"] = [f for f in a.get("flags", []) if f not in ("name-needed", "unassigned")]
            print(f"lead promoted to Active from QBO: {a['name']} / {a.get('town','')}")
        resolved.append({**{k: inv[k] for k in ("id", "doc", "date", "due", "total", "balance", "customer", "ship_town")},
                         "account_id": a["id"], "share": share, "split": split,
                         "amount": round(inv["total"] * share, 2), "open": round(inv["balance"] * share, 2)})
        if not a.get("town") and acc_town(a): a["town"] = acc_town(a)          # 'Lazy River - Tewksbury' with a blank town
        if a.get("status") != "Current":
            a["status"] = "Current"; a["stage"] = "Customer"
            a["flags"] = [f for f in a.get("flags", []) if f not in ("ordered-but-not-current", "tracker-says-customer")]
            print(f"QBO invoiced -> Active: {a['name']} / {a['town']}")
        a["in_qbo"] = True

# ---- 3. per-account summary
summ = defaultdict(lambda: {"invoiced_2026": 0.0, "n": 0, "open": 0.0, "past_due": 0.0, "oldest_due_days": 0, "last_invoice": "", "first_invoice": "", "split_share": 0.0, "customers": set()})
for r in resolved:
    s = summ[r["account_id"]]
    s["invoiced_2026"] += r["amount"]; s["n"] += r["share"]; s["open"] += r["open"]; s["customers"].add(r["customer"])
    if r["split"]: s["split_share"] += r["amount"]
    s["last_invoice"] = max(s["last_invoice"], r["date"]); s["first_invoice"] = min(s["first_invoice"] or r["date"], r["date"])
    if r["open"] > 0.005 and r.get("due"):
        days = (TODAY - datetime.date.fromisoformat(r["due"])).days
        if days > 0:
            s["past_due"] += r["open"]; s["oldest_due_days"] = max(s["oldest_due_days"], days)
# all-time open balance from the customer record when the customer maps to exactly one account (catches pre-2026 invoices and credits)
cust_bal = {c["name"]: c.get("balance", 0) for c in customers}
cust_accounts = defaultdict(set)
for r in resolved: cust_accounts[r["customer"]].add(r["account_id"])
for cname, ids in cust_accounts.items():
    if len(ids) == 1 and cname in cust_bal:
        s = summ[next(iter(ids))]
        s["open_alltime"] = round(s.get("open_alltime", 0) + cust_bal[cname], 2)
for s in summ.values():
    for k in ("invoiced_2026", "open", "past_due", "split_share"): s[k] = round(s[k], 2)
    s["n"] = round(s["n"], 2); s["customers"] = sorted(s["customers"])

# ---- 4. reconciliation vs the Order Tracker: invoices follow orders by a few days, so pair each confirmed order with
# the next unused invoice on that account (up to 30 days later); then compare amounts.
def d(s):
    try: return datetime.date.fromisoformat(s)
    except Exception: return None
inv_by_acc = defaultdict(list)
for r in resolved:
    if not r["split"]: inv_by_acc[r["account_id"]].append(r)
for lst in inv_by_acc.values(): lst.sort(key=lambda r: r["date"])
used = set(); orders_without_invoice = []; orders_total_from_qbo = []; amount_differs = []; matched_pairs = 0
def pair_check(o, best):
    if o.get("total") in (None, 0):
        orders_total_from_qbo.append({"order_id": o["id"], "account_id": o["account_id"], "date": o["date"], "invoice": best["doc"], "invoice_date": best["date"], "total": best["amount"]})
    elif abs(best["amount"] - o["total"]) > max(0.02 * o["total"], 10):
        amount_differs.append({"order_id": o["id"], "account_id": o["account_id"], "date": o["date"], "tracker_total": o["total"], "invoice": best["doc"], "invoice_date": best["date"], "invoiced": best["amount"]})
conf = [o for o in orders if re.match(r"conf", o.get("status") or "", re.I) and o.get("account_id") and d(o.get("date"))]
for o in sorted(conf, key=lambda o: o["date"]):
    od = d(o["date"]); best = None
    for r in inv_by_acc.get(o["account_id"], []):
        if r["id"] in used: continue
        gap = (d(r["date"]) - od).days
        if gap < -3: continue
        if gap > 45: break
        best = r; break
    if not best:
        orders_without_invoice.append({"order_id": o["id"], "account_id": o["account_id"], "date": o["date"], "total": o.get("total")}); continue
    used.add(best["id"]); matched_pairs += 1
    pair_check(o, best)

# second pass: chains where the tracker books the order on one store and QuickBooks invoices another (Ember Gardens Boston -> Cape Cod LLC)
def family_key(aid):
    a = acc.get(aid) or {}
    return norm(a.get("parent") or re.sub(r"\s*[-–/].*$", "", a.get("name", ""))) or aid
left_orders = {o["order_id"]: o for o in orders_without_invoice}
left_inv = defaultdict(list)
for lst in inv_by_acc.values():
    for r in lst:
        if r["id"] not in used: left_inv[family_key(r["account_id"])].append(r)
for lst in left_inv.values(): lst.sort(key=lambda r: r["date"])
chain_pairs = []
for o in sorted(orders_without_invoice, key=lambda o: o["date"]):
    fam = family_key(o["account_id"]); od = d(o["date"]); best = None
    for r in left_inv.get(fam, []):
        if r["id"] in used or r["account_id"] == o["account_id"]: continue
        gap = (d(r["date"]) - od).days
        if gap < -3: continue
        if gap > 45: break
        best = r; break
    if best:
        used.add(best["id"]); matched_pairs += 1; del left_orders[o["order_id"]]
        oo = next(x for x in orders if x["id"] == o["order_id"]); pair_check(oo, best)
        chain_pairs.append({"order_id": o["order_id"], "order_account": o["account_id"], "invoice": best["doc"], "invoice_account": best["account_id"], "date": o["date"], "total": best["amount"]})
orders_without_invoice = list(left_orders.values())
invoices_without_order = [{"invoice_id": r["id"], "doc": r["doc"], "account_id": r["account_id"], "date": r["date"], "total": r["amount"], "customer": r["customer"]}
                          for lst in inv_by_acc.values() for r in lst if r["id"] not in used]
report_recon = {"matched": matched_pairs, "orders_without_invoice": orders_without_invoice, "invoices_without_order": invoices_without_order,
                "orders_total_from_qbo": orders_total_from_qbo, "amount_differs": amount_differs, "chain_pairs": chain_pairs}

out = {
    "generated": TODAY.isoformat(),
    "invoices": sorted(resolved, key=lambda r: r["date"], reverse=True),
    "accounts": dict(summ),
    "unmapped": [{"customer": k, **{kk: (sorted(vv) if isinstance(vv, set) else round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}} for k, v in sorted(unmapped.items())],
    "recon": report_recon,
    "totals": {"invoiced_2026": round(sum(i["total"] for i in invoices), 2), "open": round(sum(i["balance"] for i in invoices), 2),
               "past_due": round(sum(s["past_due"] for s in summ.values()), 2), "invoices": len(invoices), "customers": len({i["customer"] for i in invoices}),
               "mapped_customers": len(cust_accounts), "unmapped_customers": len(unmapped)},
}
json.dump(out, open(os.path.join(DATA, "qbo.json"), "w"), indent=1, ensure_ascii=False)
json.dump(accounts, open(os.path.join(DATA, "accounts.json"), "w"), indent=1, ensure_ascii=False)
json.dump(leads, open(os.path.join(DATA, "leads.json"), "w"), indent=1, ensure_ascii=False)
t = out["totals"]
print(f"qbo.json: {t['invoices']} invoices ${t['invoiced_2026']:,.0f} · open ${t['open']:,.0f} · past due ${t['past_due']:,.0f} · {t['mapped_customers']} customers mapped, {t['unmapped_customers']} unmapped")
print(f"split across locations: {sum(1 for r in resolved if r['split'])} invoice-shares · recon: {matched_pairs} orders paired with an invoice, {len(orders_without_invoice)} orders w/o invoice, {len(invoices_without_order)} invoices w/o order, {len(orders_total_from_qbo)} blank totals fillable from QBO, {len(amount_differs)} amounts differ")
for u in out["unmapped"]: print("  UNMAPPED:", u)
