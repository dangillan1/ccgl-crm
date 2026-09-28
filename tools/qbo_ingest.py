#!/usr/bin/env python3
"""
Normalise raw QuickBooks query output into the two files build_qbo.py reads.

  python3 tools/qbo_ingest.py invoices  <file-with-query-output>
  python3 tools/qbo_ingest.py customers <file-with-query-output>

The input file is whatever the ccgl-qbo connection returned (the JSON object, possibly wrapped in other
text) for these two queries:

  SELECT Id, TxnDate, DocNumber, TotalAmt, Balance, DueDate, CustomerRef, ShipAddr
  FROM Invoice WHERE TxnDate >= '2026-01-01' ORDERBY TxnDate DESC MAXRESULTS 1000

  SELECT Id, DisplayName, Balance FROM Customer WHERE Active = true MAXRESULTS 1000

Writes tools/qbo_raw/invoices_2026.json and tools/qbo_raw/customers.json. Refuses to overwrite a file
with fewer rows than 80% of the previous one, so a partial pull can't wipe the feed.
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

kind, path = sys.argv[1], sys.argv[2]
d = load_blob(path)
if kind == "invoices":
    inv = d.get("Invoice") or d.get("QueryResponse", {}).get("Invoice") or []
    out = []
    for i in inv:
        town, ship = ship_town(i.get("ShipAddr") or {})
        out.append({"id": i["Id"], "doc": i.get("DocNumber", ""), "date": i["TxnDate"], "due": i.get("DueDate", ""), "total": i["TotalAmt"], "balance": i["Balance"],
                    "customer_id": i["CustomerRef"]["value"], "customer": i["CustomerRef"]["name"], "ship_town": town, "ship": ship})
    p = os.path.join(RAW, "invoices_2026.json"); guard(p, len(out))
    json.dump(out, open(p, "w"), indent=1); print(f"invoices_2026.json: {len(out)} invoices, ${sum(o['total'] for o in out):,.0f}")
elif kind == "customers":
    cust = d.get("Customer") or d.get("QueryResponse", {}).get("Customer") or []
    out = sorted([{"id": c["Id"], "name": c["DisplayName"], "balance": c.get("Balance", 0.0)} for c in cust], key=lambda c: c["name"])
    p = os.path.join(RAW, "customers.json"); guard(p, len(out))
    json.dump(out, open(p, "w"), indent=1); print(f"customers.json: {len(out)} customers, open balance ${sum(c['balance'] for c in out):,.0f}")
else:
    sys.exit("first argument must be invoices or customers")
