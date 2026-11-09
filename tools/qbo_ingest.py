#!/usr/bin/env python3
"""
Normalise raw QuickBooks query output into the two files build_qbo.py reads.

  python3 tools/qbo_ingest.py invoices  <file-with-query-output>
  python3 tools/qbo_ingest.py customers <file-with-query-output>

The input file is whatever the ccgl-qbo connection returned (the JSON object, possibly wrapped in other
text) for these two queries:

  SELECT * FROM Invoice WHERE TxnDate >= '2026-01-01' ORDERBY TxnDate DESC MAXRESULTS 1000
  (SELECT * so each invoice comes with its Line detail; the old field list — Id, TxnDate, DocNumber, TotalAmt,
   Balance, DueDate, CustomerRef, ShipAddr — still works, it just carries no line items)

  SELECT Id, DisplayName, Balance FROM Customer WHERE Active = true MAXRESULTS 1000

Writes tools/qbo_raw/invoices_2026.json and tools/qbo_raw/customers.json. Refuses to overwrite a file
with fewer rows than 80% of the previous one, so a partial pull can't wipe the feed.

  python3 tools/qbo_ingest.py invoices <file> --out invoices_pre2026.json
normalises a different invoice pull (the one-off history pull, TxnDate < '2026-01-01') into that file instead,
leaving the weekly feed alone.

Line items: whenever the invoices in a pull carry Line detail, the item lines are merged into data/items.json
(what each invoice was for: item, category, qty, unit price, amount — keyed by QuickBooks invoice id; invoices
not in the pull keep their lines). The app shows them under every order and on the Last order column.
  python3 tools/qbo_ingest.py items <file>     merges the lines only, touching nothing else (the one-off backfill)
"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "tools", "qbo_raw"); os.makedirs(RAW, exist_ok=True)

def load_blob(path):
    raw = open(path).read()
    start = raw.find("{"); end = raw.rfind("}")
    if start < 0 or end < 0: sys.exit(f"{path}: no JSON object found")
    return json.loads(raw[start:end + 1])

def ship_town(sa):
    lines = [sa.get(k, "") for k in ("Line1", "Line2", "Line3", "Line4", "Line5") if sa.get(k)]
    for l in lines:
        m = re.match(r"\s*([A-Za-z.' ]+?)\s*,?\s*(?:MA|Ma|Massachusetts)?\s*,?\s*\d{5}", l)
        if m: return m.group(1).strip(), " | ".join(lines[1:])
    return "", " | ".join(lines[1:])

def guard(path, n):
    try: old = len(json.load(open(path)))
    except Exception: old = 0
    if old and n < 0.8 * old: sys.exit(f"refusing to write {os.path.basename(path)}: {n} rows vs {old} before — partial pull?")

ITEMS = os.path.join(ROOT, "data", "items.json")
def item_lines(inv):
    """[[item, category, qty, unit price, amount], ...] for one raw invoice; [] when the pull had no Line detail"""
    out = []
    for l in inv.get("Line") or []:
        t = l.get("DetailType")
        if t == "SalesItemLineDetail":
            det = l.get("SalesItemLineDetail") or {}; full = ((det.get("ItemRef") or {}).get("name") or l.get("Description") or "Item").strip()
            parts = [re.sub(r"\s*\(deleted\)\s*$", "", x.strip()) for x in full.split(":")]      # QuickBooks tags retired items "(deleted)"
            name = parts[-1]; cat = " · ".join(parts[:-1]) if len(parts) > 1 else ""
            desc = (l.get("Description") or "").split("\n")[0].strip()
            if desc and (len(parts) == 1 or name == parts[-2]): name = desc                 # a generic item (Bulk Flower:Bulk Flower, Delivery Fee): the description says what it was
            out.append([name, cat, det.get("Qty", 1), det.get("UnitPrice", l.get("Amount", 0)), l.get("Amount", 0)])
        elif t == "DiscountLineDetail" and l.get("Amount"):
            out.append([(l.get("Description") or "Discount").strip(), "Discount", 1, -abs(l["Amount"]), -abs(l["Amount"])])
    return out

def merge_items(inv_rows):
    """merge the pull's line items into data/items.json; returns (invoices with lines in this pull, total invoices with lines)"""
    try: cur = json.load(open(ITEMS))
    except FileNotFoundError: cur = {"generated": "", "lines": {}}
    lines = cur.setdefault("lines", {}); n = 0
    for i in inv_rows:
        ls = item_lines(i)
        if ls: lines[i["Id"]] = ls; n += 1
    if n:
        import datetime
        cur["generated"] = datetime.date.today().isoformat()
        json.dump(cur, open(ITEMS, "w"), separators=(",", ":"), ensure_ascii=False)
    return n, len(lines)

kind, path = sys.argv[1], sys.argv[2]
out_name = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
d = load_blob(path)
if kind == "items":
    inv = d.get("Invoice") or d.get("QueryResponse", {}).get("Invoice") or []
    n, total = merge_items(inv); print(f"items.json: lines for {n} invoices merged ({total} invoices with lines in all)")
    sys.exit()
if kind == "invoices":
    inv = d.get("Invoice") or d.get("QueryResponse", {}).get("Invoice") or []
    out = []
    for i in inv:
        town, ship = ship_town(i.get("ShipAddr") or {})
        out.append({"id": i["Id"], "doc": i.get("DocNumber", ""), "date": i["TxnDate"], "due": i.get("DueDate", ""), "total": i["TotalAmt"], "balance": i["Balance"],
                    "customer_id": i["CustomerRef"]["value"], "customer": i["CustomerRef"]["name"], "ship_town": town, "ship": ship})
    p = os.path.join(RAW, out_name or "invoices_2026.json"); guard(p, len(out))
    json.dump(out, open(p, "w"), indent=1); print(f"{os.path.basename(p)}: {len(out)} invoices, ${sum(o['total'] for o in out):,.0f}")
    n, total = merge_items(inv)
    print(f"items.json: lines for {n} invoices merged ({total} invoices with lines in all)" if n else "items.json: this pull carried no Line detail (query without SELECT *) — items left as they were")
elif kind == "customers":
    cust = d.get("Customer") or d.get("QueryResponse", {}).get("Customer") or []
    out = sorted([{"id": c["Id"], "name": c["DisplayName"], "balance": c.get("Balance", 0.0)} for c in cust], key=lambda c: c["name"])
    p = os.path.join(RAW, "customers.json"); guard(p, len(out))
    json.dump(out, open(p, "w"), indent=1); print(f"customers.json: {len(out)} customers, open balance ${sum(c['balance'] for c in out):,.0f}")
else:
    sys.exit("first argument must be invoices or customers")
