#!/usr/bin/env python3
"""
Writes data/insights.json — the AI assessments shown on the Insights tab.

The ranked opportunity lists (revenue at risk, due this week, rising, fading, one-and-done,
unclaimed licenses) are computed live in the app from orders/accounts. This file holds the
narrative layer: short assessments for the accounts that matter most right now, written from
the order pattern, contacts, grade, ownership and standing requests. Regenerate at each
re-import (Claude writes the text; this script only assembles and validates it).
"""
import json, os, datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
acc = {a["id"]: a for a in json.load(open(os.path.join(DATA, "accounts.json")))}
def find(name, town=None):
    for a in acc.values():
        if a["name"].lower() == name.lower() and (town is None or a["town"].lower() == town.lower()): return a["id"]
    raise SystemExit(f"account not found: {name} / {town}")

A = [
 dict(account=find("Resinate","Douglas"), priority="critical", kind="risk",
  headline="Resinate Douglas — $48K, unowned, 59 days quiet",
  assessment="Six confirmed bulk orders in 2026, $48K, reordering every ~37 days — and nothing since July 28, three weeks past their rhythm. Last-90-day spend fell from $37K to $8K. Worcester (Joey, $26K, ordered Sept 11) is healthy; Douglas has no owner at all. Note: the Sheet had this store filed under BeWell — Resinate owns BeWell and the same buyers cover both, which is why it looked like a BeWell problem. BeWell Merrimac itself has one order this year.",
  action="Matt: give Douglas to Joey since he already has the Resinate relationship. Joey: one call to the Resinate buyers covers Douglas and Northampton (licensed, zero orders) — ask what changed on the Douglas bulk lots and quote the next one."),
 dict(account=find("Embr/Fyre Ants","Springfield"), priority="critical", kind="coverage",
  headline="$64K chain with no owner",
  assessment="Springfield ($36K) and Northampton ($28K) together are a top-5 relationship, reordering roughly every 10 weeks, last order 37 days ago. Both locations are Unassigned. A chain this size with no named rep is the biggest coverage gap in the book.",
  action="Matt: assign both locations to one owner. Owner: one call covers the chain — confirm the next order date and whether Worcester (licensed, zero orders) is a real third location to open."),
 dict(account=find("Seaside","Orleans"), priority="high", kind="risk",
  headline="A+ account 22 days past its reorder",
  assessment="Eight orders, $38K, a tight 27-day cadence — and 49 days since the last one. Bulk & finished goods. This is Joey's account and it's the largest at-risk revenue he owns.",
  action="Joey: call this week, not email. Lead with a restock offer on whatever they bought most in August."),
 dict(account=find("Apex Noire","Boston"), priority="high", kind="due",
  headline="Due for reorder in 3 days",
  assessment="Four orders averaging $5.3K on a 33-day rhythm; last order 30 days ago. Standing instruction: always include Lysah in Finance. Buyer is Nate Hall.",
  action="Joey: text Nate today with a menu — beat the reorder rather than chase it. Copy Lysah on the invoice."),
 dict(account=find("Reverie 73","Lowell"), priority="high", kind="due",
  headline="Chain due this week — one call covers three stores",
  assessment="Lowell and Gloucester are both due within 7 days (38-day rhythm, last order 31 days ago); Beverly ordered 10 days ago. Lowell's last-90-day spend is a third of the prior quarter — worth asking why. $82K across the three locations in 2026.",
  action="Joey: one call to the buyer for the chain — confirm Lowell and Gloucester reorders, and ask what shifted in Lowell."),
 dict(account=find("Capeway Cannabis","Carver"), priority="medium", kind="rising",
  headline="Growing fast, graded C",
  assessment="$28K in 2026, eight orders, and the last 90 days nearly doubled the prior quarter ($18K vs $9.5K). Still graded C and Unassigned — the grade is stale and the account has no owner.",
  action="Matt: regrade to A or B and assign an owner. Owner: ask what's driving the growth and whether Bourne (second license, tracked separately) is ready to order."),
 dict(account=find("Sublime Cannabis","Mashpee"), priority="medium", kind="rising",
  headline="Momentum account — expand the menu",
  assessment="#2 customer at $50K. Last 90 days ($27K) more than doubled the prior quarter. Ordered yesterday. Healthy 27-day cadence.",
  action="Joey: on the next visit, ask what they'd add if it were on the menu — this is where a new SKU lands first."),
 dict(account=find("Eastern Cannabis Co.","Malden"), priority="medium", kind="protect",
  headline="#1 customer, healthy — protect it",
  assessment="$62K, ten orders, 27-day rhythm, last order 17 days ago. Nothing is wrong. That's exactly when accounts get taken for granted.",
  action="Joey: schedule a visit before the next reorder (~10 days) — check menu placement and in-store visibility rather than waiting for the order."),
 dict(account=find("Quincy Cannabis Co","Quincy"), priority="medium", kind="winback",
  headline="Graded A, silent since April",
  assessment="Two orders, $12K, then nothing for 162 days. Unassigned. Either they churned or they're buying elsewhere; at Grade A the Sheet still thinks this is a priority account.",
  action="Owner: one honest call — 'we haven't heard from you since April, what happened?' Then regrade or reactivate."),
 dict(account=find("Cannabis Connection","Westfield"), priority="medium", kind="winback",
  headline="Both locations went dark on the same day",
  assessment="Westfield and West Springfield each ordered twice ($18K combined), last on May 27, then nothing for 121 days. Same-day stop across two stores is a decision, not drift — price, a competitor, or a buyer change.",
  action="Joey: find out which. If it's price, bring the tier discount; if it's a new buyer, start over with samples."),
 dict(account=find("Kush Groove","Brockton"), priority="low", kind="winback",
  headline="Big first order, never came back",
  assessment="One $9.9K order in March on the January 15%-off COD promo, then 192 days of silence. Graded A. Classic promo buyer — the discount got the order, the product didn't get the reorder.",
  action="Joey: reopen with a visit and samples of what's moving now; don't lead with a discount."),
 dict(account=find("Fresh Connection - Cambridge","Cambridge"), priority="high", kind="risk",
  headline="34 days past reorder — the Sept 21 line was a placeholder",
  assessment="Four confirmed orders, $15K, a 36-day rhythm — and nothing since July 17. The tracker has a Sept 21 row for this store with no total and no status; that's a planned order, not a real one. Somerville is the same story: one real order in April, then a placeholder.",
  action="Joey: confirm whether the Sept 21 order actually happened. If it did, log it with the total; if not, this is a win-back call today."),
 dict(account=find("Firehouse","Hyde Park"), priority="high", kind="risk",
  headline="#5 customer, 12 days past its rhythm",
  assessment="Six confirmed orders, $36K, reordering every ~32 days — last real order Aug 12. The Sept 24 row in the tracker is a placeholder with no total. Graded B; this is A-tier revenue.",
  action="Joey: same as Fresh Connection — did the Sept 24 order ship? Log it or call. Matt: regrade to A."),
 dict(account=None, priority="medium", kind="hygiene",
  headline="11 tracker rows are placeholders, not orders",
  assessment="The bottom of the Order Tracker has 11 rows with a date and a store but no total and a blank status — Enlite (2), Fresh Connection (2), Ascend (2), Firehouse, Road Trip, Trinity, Thrive, Apex. The CRM now shows these as Planned and leaves them out of revenue and reorder math.",
  action="Whoever owns the tracker: when one of these ships, fill in the total and mark it Confirmed; if it fell through, delete the row. The CRM picks it up on the next refresh."),
 dict(account=None, priority="high", kind="hygiene",
  headline="57 'Active' accounts have no 2026 orders",
  assessment="The Sheet marks 138 accounts Current, but 57 of them haven't ordered this year. They inflate the active count and hide the real base (~80 accounts driving $1.18M).",
  action="Matt: work the 'Active, no 2026 orders' list — move each to Pipeline or Non-prospect. A clean Active list is what makes the reorder alerts trustworthy."),
 dict(account=None, priority="high", kind="hygiene",
  headline="197 accounts unassigned after Allen",
  assessment="Every account Allen owned is now Unassigned, alongside the ones that were never assigned. Joey owns 161. Until the book is split, quiet-customer alerts have nobody to land on.",
  action="Matt: bulk-assign from Active Accounts (select → assign). Start with the 15 unassigned accounts that ordered in 2026."),
 dict(account=None, priority="medium", kind="pipeline",
  headline="19 new CCC licensees, none claimed",
  assessment="13 are in the Prospects pool and 6 already sit in the book. New licenses are the one lead source with a known open date — they're building menus right now.",
  action="Joey: claim five this week, intro call each, sample drop for any that bite."),
]

out = {"generated": datetime.date.today().isoformat(), "assessments": A}
for x in A:
    assert x["priority"] in ("critical","high","medium","low") and x["account"] is None or x["account"] in acc
json.dump(out, open(os.path.join(DATA, "insights.json"), "w"), indent=1, ensure_ascii=False)
print(f"insights.json: {len(A)} assessments, generated {out['generated']}")
