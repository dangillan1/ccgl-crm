#!/usr/bin/env python3
"""
Writes data/insights.json — the AI assessments shown at the top of the Insights tab.

Input: tools/assessments.json — a list of assessments written (by Claude) at each weekly refresh:
  {"account": "Resinate", "town": "Douglas",   # or null for a book-wide note
   "priority": "critical|high|medium|low", "kind": "risk|coverage|due|rising|protect|winback|hygiene|pipeline|ar",
   "headline": "...", "assessment": "...", "action": "..."}

The ranked lists (revenue at risk, due this week, past due…) are computed live in the app; this file is
only the narrative layer. This script resolves account names to ids, validates, and refuses to write if
an account can't be found — so a typo never produces a card that points nowhere.
"""
import json, os, sys, datetime, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
acc = json.load(open(os.path.join(DATA, "accounts.json")))
def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())
def find(name, town=None):
    hits = [a for a in acc if norm(a["name"]) == norm(name) and (not town or norm(a.get("town")) == norm(town))]
    if not hits: hits = [a for a in acc if norm(name) in norm(a["name"]) and (not town or norm(a.get("town")) == norm(town))]
    if len(hits) != 1: sys.exit(f"assessments.json: account not found or ambiguous: {name} / {town} ({len(hits)} matches)")
    return hits[0]["id"]

items = json.load(open(os.path.join(ROOT, "tools", "assessments.json")))
out = []
for x in items:
    for k in ("priority", "kind", "headline", "assessment", "action"):
        if not x.get(k): sys.exit(f"assessments.json: missing {k} in {x.get('headline')!r}")
    if x["priority"] not in ("critical", "high", "medium", "low"): sys.exit(f"bad priority {x['priority']!r}")
    out.append({"account": find(x["account"], x.get("town")) if x.get("account") else None, "priority": x["priority"], "kind": x["kind"],
                "headline": x["headline"].strip(), "assessment": x["assessment"].strip(), "action": x["action"].strip()})
order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
out.sort(key=lambda x: order[x["priority"]])
json.dump({"generated": datetime.date.today().isoformat(), "assessments": out}, open(os.path.join(DATA, "insights.json"), "w"), indent=1, ensure_ascii=False)
print(f"insights.json: {len(out)} assessments, generated {datetime.date.today().isoformat()}")
