#!/usr/bin/env python3
"""
Builds the Monday team email from the current data (via weekly_facts.py) and data/insights.json.

  python3 tools/weekly_email.py            -> tools/out/weekly_email.html, weekly_email.txt, weekly_email.json
                                              (json = {"subject", "to", "html", "text"} ready to hand to Gmail)

Recipients live in tools/weekly_email_config.json so they can be changed without touching code.
"""
import json, os, sys, subprocess, datetime, html
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tools", "out"); os.makedirs(OUT, exist_ok=True)
CFG = json.load(open(os.path.join(ROOT, "tools", "weekly_email_config.json")))
F = json.loads(subprocess.check_output([sys.executable, os.path.join(ROOT, "tools", "weekly_facts.py"), "--json"]))
INS = json.load(open(os.path.join(ROOT, "data", "insights.json")))
ACC = {a["id"]: a for a in json.load(open(os.path.join(ROOT, "data", "accounts.json")))}
TODAY = datetime.date.fromisoformat(F["date"])
monday = TODAY + datetime.timedelta(days=(7 - TODAY.weekday()) % 7)      # next Monday (today if Monday)
e = html.escape
def money(x): return f"${(x or 0):,.0f}"
def money_k(x): x = x or 0; return f"${x/1000:,.0f}K" if abs(x) >= 10000 else f"${x:,.0f}"
NAVY, GREEN, RED, MUTED, LINE = "#14213D", "#2D6A4F", "#B42318", "#6B7280", "#E5E7EB"
TEAL, BLUE, LEAF = "#2F8F8F", "#3080B0", "#80B040"          # the CCGL logo colours
URL = CFG.get("crm_url", "https://dangillan1.github.io/ccgl-crm/")
LOGO = CFG.get("logo_url", URL.rstrip("/") + "/logo-email.png")

def row(cells, bold_first=True, muted_last=False):
    tds = []
    for i, c in enumerate(cells):
        style = "padding:6px 8px;border-bottom:1px solid %s;font-size:13px;vertical-align:top;" % LINE
        if i == 0 and bold_first: style += "font-weight:600;"
        if i == len(cells) - 1: style += "text-align:right;white-space:nowrap;" + ("color:%s;" % MUTED if muted_last else "")
        tds.append(f"<td style='{style}'>{c}</td>")
    return "<tr>" + "".join(tds) + "</tr>"
def table(rows_html):
    return f"<table cellpadding='0' cellspacing='0' width='100%' style='border-collapse:collapse;margin:6px 0 0'>{rows_html}</table>"
def section(title, body, note=""):
    return (f"<h3 style='margin:22px 0 4px;font-size:15px;color:{TEAL};text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid {LEAF};padding-bottom:3px'>{e(title)}</h3>"
            + (f"<div style='font-size:12px;color:{MUTED};margin-bottom:4px'>{e(note)}</div>" if note else "") + body)
def owner(o): return f"<span style='color:{MUTED}'>{e(o or 'Unassigned')}</span>"

# ---- numbers
w, y, rar = F["orders_last_7d"], F["ytd"], F["revenue_at_risk"]
due, pdq = F["due_this_week"], F["past_due"]
due_total = sum(r["ytd"] for r in due); pd_total = sum(r["past_due"] for r in pdq)
delta = ""
if w["prev_total"]: delta = f" ({'+' if w['total'] >= w['prev_total'] else ''}{(w['total'] - w['prev_total']) / w['prev_total'] * 100:.0f}% vs prior week)"
kpis = [("Orders last week", f"{w['n']} · {money(w['total'])}", delta.strip() or f"prior week {w['prev_n']}"),
        ("Due for reorder this week", f"{len(due)}", f"{money_k(due_total)} of 2026 revenue"),
        ("Revenue at risk", money_k(rar["total"]), f"{len(rar['rows'])} accounts past their rhythm"),
        ("Past due in QuickBooks", money_k(y["qbo_past_due"]), f"{len(pdq)} accounts · {money_k(y['qbo_open'])} open")]
kpi_html = "<table cellpadding='0' cellspacing='0' width='100%' style='border-collapse:separate;border-spacing:6px 0;margin:10px -6px 0'><tr>" + "".join(
    f"<td width='25%' style='background:#F1F7F7;border-top:3px solid {TEAL};border-radius:0 0 8px 8px;padding:10px 12px;vertical-align:top'><div style='font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:.04em'>{e(l)}</div><div style='font-size:20px;font-weight:700;color:{BLUE};margin:2px 0'>{e(v)}</div><div style='font-size:11px;color:{MUTED}'>{e(s)}</div></td>"
    for l, v, s in kpis) + "</tr></table>"

# ---- sections
due_html = table("".join(row([e(r["account"]), owner(r["owner"]), ("<b style='color:%s'>due now</b>" % RED) if r["due_in"] <= 0 else f"due in {r['due_in']}d", money(r["ytd"])]) for r in due)) if due else "<div style='color:%s;font-size:13px'>Nobody on rhythm this week.</div>" % MUTED
risk_html = table("".join(row([e(r["account"]), owner(r["owner"]), f"<span style='color:{RED}'>{r['days_since']}d</span> since last" + (f" · usually {r['avg_gap']}d" if r["avg_gap"] else ""), money(r["ytd"])]) for r in rar["rows"][:8]))
pd_html = table("".join(row([e(r["account"]), owner(r["owner"]), f"oldest {r['oldest_days']}d · owes {money(r['owes'])}", f"<b style='color:{RED}'>{money(r['past_due'])}</b>"]) for r in pdq[:8])) if pdq else "<div style='color:%s;font-size:13px'>Nothing past due.</div>" % MUTED
top_ins = [x for x in INS.get("assessments", []) if x["priority"] in ("critical", "high")][:CFG.get("max_assessments", 6)]
ins_html = "".join(
    f"<div style='border-left:4px solid {RED if x['priority']=='critical' else '#D97706'};padding:6px 10px;margin:8px 0;background:#FAFAFA'>"
    f"<div style='font-size:13px'><b>{e(x['headline'])}</b>" + (f" <span style='color:{MUTED}'>· {e(ACC[x['account']]['name'])}{(' · ' + e(ACC[x['account']]['town'])) if ACC[x['account']].get('town') else ''}</span>" if x.get("account") and x["account"] in ACC else "") + "</div>"
    f"<div style='font-size:13px;margin-top:3px'>{e(x['assessment'])}</div><div style='font-size:13px;margin-top:4px;color:{GREEN}'><b style='color:{LEAF}'>▶</b> <b>Do:</b> {e(x['action'])}</div></div>" for x in top_ins)
def plural(n, t): return f"{n} {t.lower()}" + ("" if n == 1 else "s")
acts = F["activity_last_7d"]
act_html = ", ".join("<b>%s</b> %s" % (e(u), ", ".join(plural(n, t) for t, n in sorted(v.items(), key=lambda kv: -kv[1]))) for u, v in acts.items()) or "nothing logged"
last_html = (f"<div style='font-size:13px'>{w['n']} orders for {money(w['total'])}" + (f", up from {money(w['prev_total'])}" if w["total"] >= w["prev_total"] and w["prev_total"] else (f", down from {money(w['prev_total'])}" if w["prev_total"] else "")) + ".</div>"
             + table("".join(row([e(r["account"]), owner(r["owner"]), e(datetime.date.fromisoformat(r["date"]).strftime("%a %b %-d")), money(r["total"]) if r["total"] else "<span style='color:%s'>no total</span>" % MUTED]) for r in w["rows"][:12]))
             + f"<div style='font-size:13px;margin-top:8px'>Activity logged: {act_html}.</div>"
             + (f"<div style='font-size:13px;margin-top:4px'>New prospects: {e(', '.join(F['new_prospects_last_7d']))}.</div>" if F["new_prospects_last_7d"] else ""))
ns = [s for s in F["open_next_steps"] if s["overdue"]]
house = []
if F["book"]["unassigned_active"]: house.append(f"{F['book']['unassigned_active']} active accounts have no owner" + (f" — including {', '.join(r['account'].split(' / ')[0] for r in F['unassigned_with_orders'][:4])} with 2026 orders" if F["unassigned_with_orders"] else ""))
if ns: house.append(f"{len(ns)} overdue next step{'s' if len(ns) != 1 else ''}: " + "; ".join(f"{s['account'].split(' / ')[0]} — {s['step']} ({s['owner']})" for s in ns[:4]))
if F["placeholders"]: house.append(f"{len(F['placeholders'])} placeholder order rows still need a total or a delete: " + ", ".join(f"{p['account'].split(' / ')[0]} {p['date'][5:] if p['date'] else ''}".strip() for p in F["placeholders"][:6]))
rc = F["recon"]
if rc["orders_without_invoice"] or rc["blank_totals_fillable"]: house.append(f"QuickBooks check: {rc['orders_without_invoice']} orders with no invoice yet, {rc['blank_totals_fillable']} blank tracker totals QuickBooks can fill, {rc['amount_differs']} where the tracker total differs from the invoice")
house_html = "<ul style='margin:6px 0 0 18px;padding:0;font-size:13px'>" + "".join(f"<li style='margin:3px 0'>{e(h)}</li>" for h in house) + "</ul>" if house else ""

body = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;max-width:680px;margin:0 auto;color:#111827">
  <table cellpadding='0' cellspacing='0' width='100%' style="background:{TEAL};background-image:linear-gradient(90deg,{TEAL},{BLUE});border-radius:8px 8px 0 0;border-collapse:collapse"><tr>
    <td width='72' style='padding:12px 0 12px 16px;vertical-align:middle'><img src="{LOGO}" width='56' height='56' alt='Cape Cod Grow Lab' style='display:block;border-radius:50%;background:#fff;padding:3px'></td>
    <td style='padding:12px 16px;vertical-align:middle;color:#fff'>
      <div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;opacity:.9">Cape Cod Grow Lab · Wholesale</div>
      <div style="font-size:20px;font-weight:700;margin-top:2px">Monday brief — week of {monday.strftime('%B %-d')}</div>
      <div style="font-size:12px;opacity:.9;margin-top:2px">From the CCGL CRM and QuickBooks as of {TODAY.strftime('%A, %B %-d')}</div>
    </td></tr></table>
  <div style="height:4px;background:{LEAF}"></div>
  <div style="padding:4px 18px 18px;border:1px solid {LINE};border-top:none;border-radius:0 0 8px 8px">
    {kpi_html}
    {section("This week's reorder calls", due_html, "On their usual rhythm — call before they run out.")}
    {section("Revenue at risk", risk_html, "Past 1.5× their reorder rhythm. Biggest first.")}
    {section("Collections", pd_html, "Past due in QuickBooks. Check before the next delivery.")}
    {section("Where to focus", ins_html or "<div style='color:%s;font-size:13px'>No assessments this week.</div>" % MUTED)}
    {section("Last week", last_html)}
    {section("Housekeeping", house_html) if house else ""}
    <div style="margin-top:22px;padding-top:12px;border-top:1px solid {LINE};font-size:12px;color:{MUTED}">
      <a href="{URL}" style="display:inline-block;background:{TEAL};color:#fff;text-decoration:none;font-weight:600;padding:8px 14px;border-radius:6px;font-size:13px">Open the CCGL CRM →</a>
      <div style="margin-top:10px">Full lists and every account's history are on the Insights tab. This brief is generated every Monday morning from what the team entered during the week.</div>
      <div style="margin-top:8px;color:{TEAL};font-weight:600">Cape Cod Grow Lab · Wholesale · Brewster, MA</div>
    </div>
  </div>
</div>"""
subject = f"CCGL Monday brief · {monday.strftime('%b %-d')}: {money_k(w['total'])} last week, {len(due)} due for reorder, {money_k(y['qbo_past_due'])} past due"
text = "\n".join([
    f"CCGL Wholesale — Monday brief, week of {monday.strftime('%B %-d, %Y')}",
    "", f"Orders last week: {w['n']} / {money(w['total'])}{delta}", f"Due for reorder this week: {len(due)} accounts ({money(due_total)} of 2026 revenue)",
    f"Revenue at risk: {money(rar['total'])} across {len(rar['rows'])} accounts", f"Past due in QuickBooks: {money(y['qbo_past_due'])} across {len(pdq)} accounts",
    "", "THIS WEEK'S REORDER CALLS"] + [f"  {r['account']} — {r['owner'] or 'Unassigned'} — {'due now' if r['due_in'] <= 0 else 'due in %dd' % r['due_in']} — {money(r['ytd'])} YTD" for r in due] +
    ["", "REVENUE AT RISK"] + [f"  {r['account']} — {r['owner'] or 'Unassigned'} — {r['days_since']}d since last order — {money(r['ytd'])} YTD" for r in rar["rows"][:8]] +
    ["", "COLLECTIONS"] + [f"  {r['account']} — {money(r['past_due'])} past due, oldest {r['oldest_days']}d, owes {money(r['owes'])}" for r in pdq[:8]] +
    ["", "WHERE TO FOCUS"] + [f"  [{x['priority'].upper()}] {x['headline']}\n    {x['assessment']}\n    Do: {x['action']}" for x in top_ins] +
    ["", "HOUSEKEEPING"] + [f"  - {h}" for h in house] + ["", f"Open the CRM: {URL}"])
json.dump({"subject": subject, "to": CFG["to"], "cc": CFG.get("cc", []), "html": body, "text": text}, open(os.path.join(OUT, "weekly_email.json"), "w"), indent=1, ensure_ascii=False)
open(os.path.join(OUT, "weekly_email.html"), "w").write("<!doctype html><meta charset='utf-8'><title>" + e(subject) + "</title><body style='margin:0;padding:16px;background:#F3F4F6'>" + body + "</body>")
open(os.path.join(OUT, "weekly_email.txt"), "w").write(text)
print("SUBJECT:", subject); print("TO:", ", ".join(CFG["to"])); print("written tools/out/weekly_email.{json,html,txt}")
