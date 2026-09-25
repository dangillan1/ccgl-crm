#!/usr/bin/env python3
"""
CCGL CRM — Sheet import / re-import.

Usage:
  python tools/import_sheet.py "path/to/CCGL Wholesale Contact List.xlsx" [--orders "path/to/Order Tracker.xlsx"] [--merge]

--orders : also ingest the Order Tracker workbook:
   'Customer Tracker'     -> buyer / intake / finance contacts (role-tagged), license numbers,
                             standing requests and delivery notes (both seed-only: CRM owns them once edited)
   'Order Tracker 2026'   -> data/orders.json (one row per order) + contacts from the Contact column

Without --merge : fresh import. Writes data/accounts.json, data/contacts.json,
                  data/settings.json (stages/owners/grades), and empty
                  data/activities/<user>.json files if they don't exist.
With    --merge : parallel-run re-import. Refreshes Sheet-owned fields on
                  accounts/contacts (name, town, address, license, status,
                  last/next contact, notes, contact people). owner and grade
                  are seeded from the Sheet but CRM-owned afterwards (only
                  filled when the CRM value is blank/Unassigned). CRM-only
                  fields (stage, delivery_notes, license_number, tags, flags)
                  and CRM-created contacts are preserved. New Sheet rows are
                  added; rows missing from the Sheet are kept and flagged.

Tab handling (by name, so tab order changes don't break it):
  'CCGL Wholesale Contact List'   -> Master (accounts + contact slots 1-3)
  'Random Data 31826' / '1926'    -> people rows: email,first,last,role,...,business_name,street,city
  'Random Data 4'                 -> company + address (no people)
  'Random Data 5' / '51926'       -> blob of "Name <email>, ..." (rogue emails)
  'Random Data 6'                 -> company, first name, email
  'CCGL Current Accounts'         -> QBO billing export: customer, phone, email, bill addr
  'CCGL Prospect List'            -> account, city, POC, grade, notes (grade only used if Master blank)
  'Random Data 1'                 -> company, AR email, secondary email
  'Random Data 2'                 -> company, position, email
  'Random Data 3'                 -> account+city combined, MSO flag
  'CCC New Licenses Jan - June'   -> "Company (#LIC), License Type"
  'Sheet11'                       -> SKIPPED (per Dan)
"""
import sys, json, re, os, datetime, hashlib
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TODAY = datetime.date.today().isoformat()

EMAIL_RE = re.compile(r"[\w\.\+\-']+@[\w\-]+\.[\w\.\-]+")
# legal / generic words that carry no identity. Applied after punctuation is stripped to spaces.
SUFFIX_WORDS = {
    "llc", "lllc", "llp", "inc", "corp", "corporation", "co", "company", "ltd", "limited",
    "dispensary", "dispensaries", "cannabis", "canna", "the", "of", "massachusetts", "ma", "mass",
    "wellness", "holdings", "group", "medical", "recreational", "rec", "adult", "use", "retail",
    "marijuana", "medicinals", "delivery", "only", "dba", "d", "b", "a", "market", "farms", "farm", "bulk", "medical only",
}
GENERIC_TOKENS = SUFFIX_WORDS | {
    "green", "north", "south", "east", "west", "new", "street", "st", "road", "rd", "ave",
    "organic", "organics", "garden", "gardens", "farms", "farm", "collective", "health", "care",
    "center", "centre", "market", "harvest", "valley", "river", "alternative", "therapies",
    "therapeutics", "compassion", "services", "remedies", "remedy", "natures", "nature", "cape", "cod",
    "boston", "premium", "craft", "house", "leaf", "bud", "buds", "flower", "high", "good", "grow", "lab",
    "industries", "industry", "therapeutics", "therapeutic", "roots", "root", "england", "treatment", "access",
    "naturals", "natural", "partners", "partner", "growth", "clinics", "clinic", "medicine", "integrative",
    "select", "northeast", "botanicals", "botanical", "cultivators", "cultivator", "cultivation", "gmail", "john",
    "ultra", "united", "street", "investments", "worcester", "springfield", "new england",
}
FREE_EMAIL_DOMAINS = {"gmail", "yahoo", "hotmail", "outlook", "icloud", "aol", "me", "live", "msn", "comcast", "verizon", "protonmail"}
KNOWN_TOWNS = set()   # filled from Master TOWN column at runtime

USERS = [
    {"id": "dan",     "name": "Dan",     "role": "admin"},
    {"id": "matt",    "name": "Matt",    "role": "manager"},
    {"id": "joey",    "name": "Joey",    "role": "rep"},
    {"id": "charley", "name": "Charley", "role": "ops"},
]

DEFAULT_STAGES = ["New", "Intro Call", "Sample Drop", "Menu Setup", "Launch Promo", "First Reorder", "Customer"]

# ---------------------------------------------------------------- helpers
def clean(v):
    if v is None: return ""
    try:
        if pd.isna(v): return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, (pd.Timestamp, datetime.datetime, datetime.date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "n/a", "-", "nat") else s

def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def stem(t):
    return t[:-1] if len(t) > 4 and t.endswith("s") and not t.endswith("ss") else t

def tokens(s):
    """Identity tokens: lowercase words, parentheticals dropped, legal suffixes removed, plurals stemmed."""
    s = re.sub(r"\(.*?\)", " ", s or "")
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"\bd/?b/?a\b", " ", s, flags=re.I)
    s = re.sub(r"\bops\b", "operations", s, flags=re.I)
    s = re.sub(r"\.(com|net|org|co|us|io|biz|life|shop)\b", " ", s, flags=re.I)
    toks = [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in SUFFIX_WORDS]
    toks = [stem(t) for t in toks]
    return [t for t in toks if t not in SUFFIX_WORDS]

def strip_trailing_town(toks):
    """'eskar arlington' -> 'eskar' when 'arlington' is a known Master town."""
    while len(toks) > 1 and toks[-1] in KNOWN_TOWNS:
        toks = toks[:-1]
    return toks

DOMAIN_PREFIXES = ("my", "the", "visit", "shop", "go", "get", "i", "try", "hello")
DOMAIN_SUFFIXES = ("cannabis", "dispensary", "wellness", "farms", "farm", "market", "products", "shop", "store",
                   "stores", "company", "co", "llc", "inc", "ma", "us", "boutique", "canna", "brands", "brand",
                   "trading", "tradingco", "holdings", "group", "worldwide", "botanicals", "cultivators", "health")
def domain_variants(tok):
    """'mytemescalwellness' -> {..., 'temescal'}; strips marketing prefixes and generic suffixes from a raw domain root."""
    out = {tok, stem(tok)}
    cores = {tok}
    for pre in DOMAIN_PREFIXES:
        if tok.startswith(pre) and len(tok) - len(pre) >= 4: cores.add(tok[len(pre):])
    more = set()
    for c in cores:
        changed = True; cur = c
        while changed:
            changed = False
            for suf in sorted(DOMAIN_SUFFIXES, key=len, reverse=True):
                if cur.endswith(suf) and len(cur) - len(suf) >= 4:
                    cur = cur[:-len(suf)]; changed = True; more.add(cur); break
    out |= cores | more | {stem(x) for x in cores | more}
    return {v for v in out if len(v) >= 4}

def raw_single_token(s):
    """If the string is a single alphanumeric run (a domain root), return it un-stemmed, else ''."""
    s = re.sub(r"\.(com|net|org|co|us|io|biz|life|shop)\b", " ", s or "", flags=re.I)
    toks = re.findall(r"[a-z0-9]+", s.lower())
    return toks[0] if len(toks) == 1 and len(toks[0]) >= 8 else ""

def name_variants(s):
    """All normalized forms an account name should be indexed under.
    'Mellow Fellow d/b/a Mello Cannabis' -> {'mellowfellow','mello'}; 'RISE/Affinity' -> {'rise','affinity','riseaffinity'};
    'Lazy River - Dracut' -> {'lazyriverdracut','lazyriver'}; 'mypureoasis.com' -> {'mypureoasi','pureoasi',...}"""
    out = set()
    s = s or ""
    parts = [s] + re.split(r"\s*(?:/|\bd/?b/?a\b|\s-\s)\s*", s, flags=re.I)
    for p in parts:
        toks = tokens(p)
        if toks and not all(t in GENERIC_TOKENS or len(t) < 4 for t in toks) or (toks and p == s):
            out.add("".join(toks))
            if len(toks) > 1: out.add("".join(sorted(toks)))   # word-order-insensitive
            tt = strip_trailing_town(toks)
            if tt != toks:
                out.add("".join(tt))
                if len(tt) > 1: out.add("".join(sorted(tt)))
    rt = raw_single_token(s)
    if rt: out |= domain_variants(rt)
    out.discard("")
    return out

def norm_loose(s):
    toks = strip_trailing_town(tokens(s))
    return "".join(toks)

def distinctive_tokens(s):
    return {t for t in tokens(s) if len(t) >= 4 and t not in GENERIC_TOKENS and t not in KNOWN_TOWNS}

def email_domain(em):
    if not em or "@" not in em: return ""
    d = em.split("@", 1)[1].lower()
    root = d.split(".")[0]
    return "" if root in FREE_EMAIL_DOMAINS else root

def email_of(v):
    m = EMAIL_RE.search(clean(v))
    return m.group(0).lower() if m else ""

def date_of(v):
    s = clean(v)
    if not s: return ""
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return s

def sheet(xl, name, **kw):
    for n in xl.sheet_names:
        if n.strip().lower() == name.strip().lower():
            df = pd.read_excel(xl, sheet_name=n, **kw)
            if "header" in kw and kw["header"] is not None:
                df.columns = [str(c).strip() for c in df.columns]
            return df
    return None

def cid(account_id, email, name):
    """Deterministic contact id: stable across re-imports."""
    key = f"{account_id or 'none'}|{(email or '').lower().strip()}|{(name or '').lower().strip()}"
    return "C" + hashlib.sha1(key.encode()).hexdigest()[:8]

def load_json(p, default):
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return default

def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f: json.dump(obj, f, indent=1, ensure_ascii=False)

# ---------------------------------------------------------------- main
def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]
    merge = "--merge" in sys.argv
    orders_path = sys.argv[sys.argv.index("--orders") + 1] if "--orders" in sys.argv else ""
    xl = pd.ExcelFile(path)

    existing_accounts = load_json(os.path.join(DATA, "accounts.json"), []) if merge else []
    existing_leads    = load_json(os.path.join(DATA, "leads.json"), []) if merge else []
    existing_contacts = load_json(os.path.join(DATA, "contacts.json"), []) if merge else []
    existing_orders   = load_json(os.path.join(DATA, "orders.json"), []) if merge else []
    existing_settings = load_json(os.path.join(DATA, "settings.json"), {}) if merge else {}
    by_key = {a["sheet_key"]: a for a in existing_accounts if a.get("sheet_key")}
    next_a = max([int(a["id"][1:]) for a in existing_accounts + existing_leads if a["id"][1:].isdigit()] + [0]) + 1

    accounts, contacts = [], []
    seen_sheet_keys = set()
    report = {"master_rows": 0, "new_accounts": 0, "updated_accounts": 0, "contacts_from_master": 0,
              "leads_matched": 0, "leads_new_accounts": 0, "contacts_added": 0, "rogue_emails": 0,
              "addresses_filled": 0, "licenses_attached": 0, "licenses_new_prospects": 0,
              "phones_filled": 0, "grades_filled": 0}

    # ---------------- 1. MASTER -> accounts + contact slots
    m = sheet(xl, "CCGL Wholesale Contact List", header=0)
    m = m.dropna(subset=["ACCOUNT NAME"])
    m["ACCOUNT NAME"] = m["ACCOUNT NAME"].astype(str).str.strip()
    m = m[m["ACCOUNT NAME"].str.len() > 0]

    for ridx, r in m.iterrows():
        name = clean(r["ACCOUNT NAME"])
        town = clean(r.get("TOWN", ""))
        key = norm(name) + "|" + norm(town)
        # multi-location rows with same name+town: disambiguate by row order
        base_key, k = key, 0
        while key in seen_sheet_keys:
            k += 1; key = f"{base_key}#{k}"
        seen_sheet_keys.add(key)
        report["master_rows"] += 1

        owner = clean(r.get("OWNERSHIP", ""))
        # Allen has left: his accounts become Unassigned, shared "Allen/Joey" becomes Joey
        if owner.lower() == "allen": owner = ""
        elif "allen" in owner.lower(): owner = re.sub(r"\s*/?\s*allen\s*/?\s*", "", owner, flags=re.I).strip("/ ").strip() or ""
        status = clean(r.get("STATUS", "")) or "Unspecified"
        sheet_fields = {
            "name": name,
            "town": town,
            "address": clean(r.get("ADDRESS", "")),
            "license_type": clean(r.get("LICENSE TYPE", "")),
            "status": status,
            "grade": clean(r.get("GRADE", "")).upper(),
            "owner": owner or "Unassigned",
            "last_contact": date_of(r.get("LAST CONTACT", "")),
            "next_contact": date_of(r.get("NEXT CONTACT", "")),
            "notes": clean(r.get("NOTES", "")),
            "sheet_row": int(ridx) + 2,
        }
        flags = []
        if not owner: flags.append("unassigned")
        elif "/" in owner or owner.lower() == "team": flags.append("shared-owner")
        if owner and owner.lower() not in [u["id"] for u in USERS] and owner.lower() not in ("team",) and "/" not in owner:
            flags.append("owner-not-user")

        if key in by_key:
            a = by_key[key]
            # owner & grade: seeded from Sheet, owned by CRM afterwards — only fill if CRM has nothing
            for f in ("owner", "grade"):
                cur = a.get(f, "")
                if cur and cur != "Unassigned": sheet_fields.pop(f, None)
            if a.get("in_customer_tracker") and a.get("status") == "Current": sheet_fields.pop("status", None)
            if a.get("town") and not sheet_fields.get("town"): sheet_fields.pop("town", None)
            a.update(sheet_fields)
            a["flags"] = sorted(set([f for f in a.get("flags", []) if f not in ("unassigned","shared-owner","owner-not-user","missing-from-sheet")] + flags))
            report["updated_accounts"] += 1
        else:
            a = {
                "id": f"A{next_a:04d}", "sheet_key": key, "source": "Master",
                "parent": "", "stage": "" if status == "Current" else "New",
                "stage_entered": "" if status == "Current" else TODAY,
                "delivery_notes": "", "standing_requests": "", "license_number": "", "tags": [],
                "flags": flags, "created": TODAY,
                **sheet_fields,
            }
            next_a += 1
            report["new_accounts"] += 1
        accounts.append(a)

        # contact slots
        for slot in (1, 2, 3):
            cname = clean(r.get(f"CONTACT PERSON {slot}", ""))
            cemail = email_of(r.get(f"CONTACT {slot} EMAIL", ""))
            cphone = clean(r.get(f"CONTACT {slot} PHONE", ""))
            if not (cname or cemail or cphone): continue
            contacts.append({
                "id": cid(a["id"], cemail, cname), "account_id": a["id"], "name": cname, "email": cemail,
                "phone": cphone, "role": "", "source": "Master", "primary": slot == 1,
                "sheet_slot": slot,
            })
            report["contacts_from_master"] += 1

    # rows in existing data not seen this time (merge mode) -> keep, flag
    if merge:
        for a in existing_accounts:
            if a.get("sheet_key") and a["sheet_key"] not in seen_sheet_keys and a.get("source") == "Master":
                a["flags"] = sorted(set(a.get("flags", []) + ["missing-from-sheet"]))
                accounts.append(a)
            elif a.get("source") != "Master":
                accounts.append(a)  # claimed leads / tracker locations / CRM-created accounts persist
        for l in existing_leads:
            accounts.append(l)      # unclaimed leads re-enter the working set; split back out at write time

    # ---------------- lookup structures
    KNOWN_TOWNS.update(t for a in accounts for t in tokens(a["town"]) if len(t) >= 4)
    idx_exact, idx_variant, idx_domain = {}, {}, {}
    def index_account(a):
        idx_exact.setdefault(norm(a["name"]), []).append(a)
        for v in name_variants(a["name"]):
            idx_variant.setdefault(v, []).append(a)
    def index_domain(a, em):
        d = email_domain(em)
        if d and len(d) >= 4:
            idx_domain.setdefault(d, [])
            if a not in idx_domain[d]: idx_domain[d].append(a)
    for a in accounts: index_account(a)
    acc_by_id = {a["id"]: a for a in accounts}
    for c in contacts:
        if c["email"] and c["account_id"]: index_domain(acc_by_id[c["account_id"]], c["email"])

    contact_emails = {}
    for c in contacts:
        if c["email"]: contact_emails.setdefault(c["email"], []).append(c)

    def find_account(company, email=""):
        """Return (account or None, match_type). Order: exact name > name variant > known email > email domain > fuzzy.
        A company name that matches an account outright always wins — the same buyer can cover several
        companies (Resinate's buyers also handle BeWell), so email is only a fallback."""
        n = norm(company) if company else ""
        if n and n in idx_exact: return idx_exact[n][0], "exact"
        if company:
            for v in name_variants(company):
                if v in idx_variant: return idx_variant[v][0], "variant"
        # 0. this exact email is already on a contact that has an account
        if email and email in contact_emails:
            for ec in contact_emails[email]:
                if ec.get("account_id"): return acc_by_id[ec["account_id"]], "known-email"
        # 1. email domain — strongest company-level signal
        d = email_domain(email)
        if d and d in idx_domain and len(idx_domain[d]) == 1:
            return idx_domain[d][0], "domain"
        if not company: return None, ""
        # 3. domain-looking company names ('bostonbudfactory') -> match against domain index
        if n in idx_domain and len(idx_domain[n]) == 1: return idx_domain[n][0], "domain-name"
        # 4. fuzzy: loose forms contain each other, min 6 chars, and share a distinctive token
        nl = norm_loose(company)
        dt = distinctive_tokens(company)
        probes = {nl} | (domain_variants(raw_single_token(company)) if raw_single_token(company) else set())
        cands = []
        for pv in probes:
            if len(pv) < 5: continue
            for k, accs in idx_variant.items():
                if len(k) < 5: continue
                if pv == k or (len(pv) >= 8 and k.startswith(pv)) or (len(k) >= 8 and pv.startswith(k)):
                    for a in accs: cands.append((abs(len(k) - len(pv)), a))       # strong prefix evidence
                elif dt and (pv in k or k in pv):
                    for a in accs:
                        if dt & distinctive_tokens(a["name"]): cands.append((abs(len(k) - len(pv)) + 10, a))
        if cands:
            cands.sort(key=lambda x: x[0])
            return cands[0][1], "fuzzy"
        return None, ""

    def dup_candidates(company, exclude_id=None):
        """Accounts sharing a distinctive token with this name — for human review, not auto-merge."""
        dt = distinctive_tokens(company)
        if not dt: return []
        hits = {}
        for a in accounts:
            if a["id"] == exclude_id: continue
            shared = dt & distinctive_tokens(a["name"])
            if shared: hits[a["id"]] = len(shared)
        return [k for k, _ in sorted(hits.items(), key=lambda x: -x[1])][:3]

    def add_contact(account, name, email, phone, role, source):
        """One contact row per (account, person). The same person may legitimately sit on several
        accounts (a chain buyer covers every store), so email is only de-duplicated WITHIN an account."""
        if not email and name and account:
            for ec in contacts:
                if ec["account_id"] == account["id"] and ec["name"].lower() == name.lower():
                    if not ec["phone"] and phone: ec["phone"] = phone
                    if not ec["role"] and role: ec["role"] = role
                    return None
        if email and email in contact_emails:
            same = [ec for ec in contact_emails[email] if account and ec["account_id"] == account["id"]]
            if same:                                    # already on this account -> enrich it
                for ec in same:
                    if not ec["name"] and name: ec["name"] = name
                    if not ec["phone"] and phone: ec["phone"] = phone
                    if not ec["role"] and role: ec["role"] = role
                return None
            if not account:                             # rogue email already known somewhere -> skip
                return None
            orphan = [ec for ec in contact_emails[email] if not ec["account_id"]]
            if orphan:                                  # adopt a rogue row instead of duplicating it
                ec = orphan[0]; ec["account_id"] = account["id"]; index_domain(account, email)
                if not ec["name"] and name: ec["name"] = name
                if not ec["phone"] and phone: ec["phone"] = phone
                if not ec["role"] and role: ec["role"] = role
                return None
            # known on another account: fall through and add a row for THIS account too
        c = {"id": cid(account["id"] if account else None, email, name), "account_id": account["id"] if account else None,
             "name": name, "email": email, "phone": phone, "role": role, "source": source, "primary": False}
        contacts.append(c)
        if email:
            contact_emails.setdefault(email, []).append(c)
            if account: index_domain(account, email)
        report["contacts_added"] += 1
        if not account: report["rogue_emails"] += 1
        return c

    def looks_like_domain(name):
        n = name.strip().lower()
        return bool(re.search(r"\.(com|net|org|co|us|io|biz|life|shop)$", n))   # only a real domain, never a plain one-word name

    def sanitize_company(raw):
        """Return a usable company string or '' (meaning: rogue contact, no account)."""
        c = clean(raw)
        if not c: return ""
        if "@" in c: return ""                                  # an email, not a company
        if c.lower().split(".")[0] in FREE_EMAIL_DOMAINS: return ""
        if "," in c: c = c.split(",")[0].strip()                # 'INSA, INSA AVON' -> 'INSA'
        c = re.sub(r"\s*\(.*?\)\s*$", "", c).strip()            # 'RISE (Dracut)' -> 'RISE'
        return c

    def new_lead_account(company, source, town="", address="", extra_tags=None):
        nonlocal next_a
        company = company.strip()
        flags = ["unassigned"]
        if looks_like_domain(company):
            flags.append("name-from-domain")
            company = company.lower()
        cands = dup_candidates(company)
        a = {
            "id": f"A{next_a:04d}", "sheet_key": "", "source": source,
            "name": company, "parent": "", "town": town, "address": address,
            "license_type": "Dispensary", "status": "Prospect", "grade": "",
            "owner": "Unassigned", "last_contact": "", "next_contact": "", "notes": "",
            "stage": "New", "stage_entered": TODAY, "delivery_notes": "", "standing_requests": "", "license_number": "",
            "tags": ["Unprocessed Lead"] + (extra_tags or []), "flags": flags,
            "dup_candidates": cands,
            "created": TODAY, "sheet_row": None,
        }
        if cands: a["flags"].append("possible-duplicate")
        next_a += 1
        accounts.append(a); index_account(a); acc_by_id[a["id"]] = a
        report["leads_new_accounts"] += 1
        return a

    # ---------------- 2. People tabs (31826, 1926)
    for tab in ("Random Data 31826", "Random Data 1926"):
        df = sheet(xl, tab, header=0)
        if df is None: continue
        for _, r in df.iterrows():
            cols = list(r)
            email = email_of(cols[0] if len(cols) > 0 else "")
            first = clean(cols[1] if len(cols) > 1 else "")
            last = clean(cols[2] if len(cols) > 2 else "")
            role = clean(cols[4] if len(cols) > 4 else "") or clean(cols[3] if len(cols) > 3 else "")
            if role.lower() in ("person", "role"): role = ""
            biz = clean(cols[7] if len(cols) > 7 else "")
            if not biz:
                dom = clean(cols[5] if len(cols) > 5 else "").lower()
                biz = dom if dom and dom.split(".")[0] not in FREE_EMAIL_DOMAINS else ""
            biz = sanitize_company(biz)
            street = clean(cols[8] if len(cols) > 8 else "")
            city = clean(cols[9] if len(cols) > 9 else "")
            nm = (first + " " + last).strip()
            if not email and not nm: continue
            acc, mt = find_account(biz, email)
            if acc: report["leads_matched"] += 1
            elif biz: acc = new_lead_account(biz, tab, town=city, address=street)
            if acc and not acc.get("address") and street:
                acc["address"] = street; report["addresses_filled"] += 1
            add_contact(acc, nm, email, "", role, tab)

    # ---------------- 3. Random Data 6 (company, first, email)
    df = sheet(xl, "Random Data 6", header=None)
    if df is not None:
        for _, r in df.iterrows():
            cols = list(r)
            biz = sanitize_company(cols[0] if len(cols) > 0 else "")
            nm = clean(cols[1] if len(cols) > 1 else "")
            email = email_of(cols[2] if len(cols) > 2 else "")
            if not email and not nm: continue
            acc, _ = find_account(biz, email)
            if acc: report["leads_matched"] += 1
            elif biz: acc = new_lead_account(biz, "Random Data 6")
            add_contact(acc, nm, email, "", "", "Random Data 6")

    # ---------------- 4. Random Data 1 (company, AR email, secondary)
    df = sheet(xl, "Random Data 1", header=0)
    if df is not None:
        for _, r in df.iterrows():
            cols = list(r)
            biz = sanitize_company(cols[0] if len(cols) > 0 else "")
            e1 = email_of(cols[1] if len(cols) > 1 else "")
            acc, _ = find_account(biz, e1)
            if acc: report["leads_matched"] += 1
            elif biz: acc = new_lead_account(biz, "Random Data 1")
            for i, role in ((1, "AR / Accounting"), (2, "")):
                em = email_of(cols[i] if len(cols) > i else "")
                if em: add_contact(acc, "", em, "", role, "Random Data 1")

    # ---------------- 5. Random Data 2 (company, position, email)
    df = sheet(xl, "Random Data 2", header=0)
    if df is not None:
        for _, r in df.iterrows():
            cols = list(r)
            biz = sanitize_company(cols[0] if len(cols) > 0 else "")
            role = clean(cols[1] if len(cols) > 1 else "")
            email = email_of(cols[2] if len(cols) > 2 else "")
            if not email: continue
            acc, _ = find_account(biz, email)
            if acc: report["leads_matched"] += 1
            elif biz: acc = new_lead_account(biz, "Random Data 2")
            add_contact(acc, "", email, "", role, "Random Data 2")

    # ---------------- 6. Random Data 3 (account+city, MSO)
    df = sheet(xl, "Random Data 3", header=0)
    if df is not None:
        for _, r in df.iterrows():
            cols = list(r)
            combo = sanitize_company(cols[0] if len(cols) > 0 else "")
            if not combo: continue
            acc, _ = find_account(combo)
            if not acc:
                # try dropping the last token (city)
                parts = combo.split()
                if len(parts) > 1:
                    acc, _ = find_account(" ".join(parts[:-1]))
            if acc:
                report["leads_matched"] += 1
                if clean(cols[1] if len(cols) > 1 else "") and "MSO" not in acc["tags"]:
                    acc["tags"].append("MSO")
            else:
                new_lead_account(combo, "Random Data 3")

    # ---------------- 7. Random Data 4 (company, address) — address-only
    df = sheet(xl, "Random Data 4", header=None)
    if df is not None:
        for _, r in df.iterrows():
            cols = [clean(c) for c in r]
            if not cols or not cols[0]: continue
            company = sanitize_company(cols[0])
            if not company: continue
            addr = ", ".join([c for c in cols[1:] if c])
            acc, _ = find_account(company)
            if acc:
                report["leads_matched"] += 1
                if not acc.get("address") and addr:
                    acc["address"] = addr; report["addresses_filled"] += 1
            else:
                new_lead_account(company, "Random Data 4", address=addr)

    # ---------------- 8. Blob tabs (5, 51926)
    for tab in ("Random Data 5", "Random Data 51926"):
        df = sheet(xl, tab, header=None)
        if df is None: continue
        blob = " ".join(str(v) for _, r in df.iterrows() for v in r if pd.notna(v))
        for entry in re.split(r"[,;]", blob):
            entry = entry.strip()
            if not entry or entry.upper().startswith("THIS CAME FROM"): continue
            mm = re.match(r'^"?([^"<]+?)"?\s*<?([\w\.\+\-\']+@[\w\-]+\.[\w\.\-]+)>?$', entry)
            if mm:
                nm, em = clean(mm.group(1)), mm.group(2).lower()
            else:
                em = email_of(entry); nm = ""
            if not em: continue
            acc, _ = find_account("", em)
            if acc: report["leads_matched"] += 1
            add_contact(acc, nm, em, "", "", tab)

    # ---------------- 9. CCGL Current Accounts (QBO billing) — phones + billing address
    df = sheet(xl, "CCGL Current Accounts", header=3)
    if df is not None:
        for _, r in df.iterrows():
            cols = list(r)
            cust = clean(cols[0] if len(cols) > 0 else "")
            phone = clean(cols[1] if len(cols) > 1 else "")
            email = email_of(cols[2] if len(cols) > 2 else "")
            bill = clean(cols[4] if len(cols) > 4 else "")
            if not cust: continue
            acc, _ = find_account(cust, email)
            if not acc: continue
            report["leads_matched"] += 1
            if bill and not acc.get("address"):
                acc["address"] = bill.replace("\n", ", "); report["addresses_filled"] += 1
            if email or phone:
                c = add_contact(acc, "", email, phone, "Billing", "CCGL Current Accounts")
                if c is None and phone:
                    for ec in contact_emails.get(email, []):
                        if not ec["phone"]: ec["phone"] = phone; report["phones_filled"] += 1

    # ---------------- 10. CCGL Prospect List — grade fallback + POC
    df = sheet(xl, "CCGL Prospect List", header=2)
    if df is not None:
        for _, r in df.iterrows():
            cols = list(r)
            acct = clean(cols[0] if len(cols) > 0 else "")
            poc = clean(cols[2] if len(cols) > 2 else "")
            grade = clean(cols[3] if len(cols) > 3 else "").upper()
            if not acct: continue
            acc, _ = find_account(acct)
            if not acc: continue
            if grade and not acc.get("grade"):
                acc["grade"] = grade; report["grades_filled"] += 1
            if poc:
                em = email_of(poc)
                nm = re.sub(EMAIL_RE, "", poc).strip(" ,;<>()-")
                nm = re.sub(r"\(.*?\)", "", nm).strip()
                if em or nm: add_contact(acc, nm, em, "", "", "CCGL Prospect List")

    # ---------------- 11. CCC New Licenses
    df = sheet(xl, "CCC New Licenses Jan - June", header=0)
    if df is not None:
        seen_lic = set()
        for v in df.iloc[:, 0].dropna().tolist():
            s = clean(v)
            mm = re.match(r"^(.*?)\s*\(#\s*([A-Za-z0-9/\-]+)\)\s*,?\s*(.*)$", s)
            if mm:
                company, lic, ltype = clean(mm.group(1)), clean(mm.group(2)), clean(mm.group(3))
            else:
                company, lic, ltype = s, "", ""
            if (company.lower(), lic) in seen_lic: continue
            seen_lic.add((company.lower(), lic))
            acc, _ = find_account(company)
            if acc:
                if lic and not acc.get("license_number"): acc["license_number"] = lic
                if "New License 2026" not in acc["tags"]: acc["tags"].append("New License 2026")
                report["licenses_attached"] += 1
            else:
                a = new_lead_account(company, "CCC New Licenses", extra_tags=["New License 2026"])
                a["tags"] = [t for t in a["tags"] if t != "Unprocessed Lead"]
                a["license_number"] = lic
                if ltype: a["license_type"] = ltype
                report["licenses_new_prospects"] += 1

    # ---------------- 11o. ORDER TRACKER workbook (Customer Tracker + Order Tracker 2026)
    orders = load_json(os.path.join(DATA, "orders.json"), []) if (merge and not orders_path) else []
    if orders_path:
        oxl = pd.ExcelFile(orders_path)
        lic_index = {}
        def index_license(a):
            for l in re.findall(r"[A-Z]{2,4}\d{3,7}(?:-[A-Z])?", (a.get("license_number") or "").upper()):
                lic_index.setdefault(l, a)
        for a in accounts: index_license(a)

        def find_by_town(company, town, email=""):
            """find_account, but when several accounts share the name, prefer the one whose town matches."""
            acc, mt = find_account(company, email)
            if acc and town:
                n_t = norm(town)
                pool = {acc["id"]: acc}
                for v in name_variants(company):
                    for x in idx_variant.get(v, []): pool[x["id"]] = x
                for x in accounts:
                    if norm_loose(x["name"]) == norm_loose(acc["name"]): pool[x["id"]] = x
                for x in pool.values():
                    xt = norm(x.get("town", ""))
                    if xt and (xt == n_t or n_t in xt or xt in n_t): return x, mt + "+town"
            return acc, mt

        def parse_phone(v):
            v = clean(v)
            m = re.search(r"(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})", v)
            return m.group(1).strip() if m else ""

        def split_emails(v):
            return [e.lower() for e in EMAIL_RE.findall(clean(v))]

        # ---- Customer Tracker
        ct = sheet(oxl, "Customer Tracker", header=0)
        if ct is not None:
            ct = ct.dropna(subset=[ct.columns[0]])
            for _, r in ct.iterrows():
                name = clean(r.get("Customer Name", "")); town = clean(r.get("Location", ""))
                if not name: continue
                lic = clean(r.get("License Number (s)", "")).upper()
                b_email = split_emails(r.get("Buyer Email", "")); b_email = b_email[0] if b_email else ""
                acc = None
                for l in re.findall(r"[A-Z]{2,4}\d{3,7}(?:-[A-Z])?", lic):
                    if l in lic_index: acc = lic_index[l]; break
                matched_by_license = acc is not None
                # A location row this import created earlier under the wrong company name (e.g. "BeWell Organics / Douglas"
                # that is really Resinate Douglas): the tracker row that owns the license is the authority — adopt its name.
                if acc is not None and acc.get("source") == "Customer Tracker" and norm_loose(acc["name"]) != norm_loose(name) \
                        and not (name_variants(acc["name"]) & name_variants(name)):
                    base_acc, _ = find_account(name)
                    acc["name"] = base_acc["name"] if base_acc and norm_loose(base_acc["name"]) == norm_loose(name) else name
                    acc["parent"] = (base_acc.get("parent") or base_acc["name"]) if base_acc else ""
                    acc["flags"] = [f for f in acc.get("flags", []) if f != "possible-duplicate"]; acc["dup_candidates"] = []
                    index_account(acc)
                    report["tracker_locations_renamed"] = report.get("tracker_locations_renamed", 0) + 1
                if not acc: acc, _ = find_by_town(name, town, b_email)
                def _new_location(display_name, parent_name=""):
                    x = new_lead_account(display_name, "Customer Tracker", town=town)
                    x["status"] = "Current"; x["stage"] = "Customer"; x["tags"] = ["Not in Master Sheet"]
                    x["flags"] = [f for f in x["flags"] if f not in ("unassigned", "possible-duplicate")]; x["flags"].append("not-in-master")
                    x["dup_candidates"] = []
                    if parent_name: x["parent"] = parent_name
                    report["tracker_new_accounts"] = report.get("tracker_new_accounts", 0) + 1
                    return x
                if not acc:
                    acc = _new_location(name)
                elif not matched_by_license and town and acc.get("town") and norm(town) != norm(acc["town"]) and norm(town) not in norm(acc["town"]) and norm(acc["town"]) not in norm(town):
                    # same company, different town, no existing row for this town -> new location row under the same parent
                    base = re.sub(r"\s*[-–/]\s*" + re.escape(acc["town"]) + r"\s*$", "", acc["name"], flags=re.I).strip()
                    base = re.sub(r"\s+" + re.escape(acc["town"]) + r"\s*$", "", base, flags=re.I).strip() or acc["name"]
                    acc = _new_location(base, acc.get("parent") or base)
                    report["tracker_new_locations"] = report.get("tracker_new_locations", 0) + 1
                report["tracker_matched"] = report.get("tracker_matched", 0) + 1
                if lic and not acc.get("license_number"): acc["license_number"] = lic
                elif lic and lic not in (acc.get("license_number") or ""): acc["license_number"] = (acc["license_number"] + " / " + lic).strip(" /")
                index_license(acc)
                sr = clean(r.get("Consistent Requests", ""))
                if sr and not acc.get("standing_requests"): acc["standing_requests"] = sr   # seed only; CRM owns it once edited
                dn = clean(r.get("Delivery Notes", ""))
                if dn and not acc.get("delivery_notes"): acc["delivery_notes"] = dn
                if acc["status"] != "Current" and "tracker-says-customer" not in acc["flags"]: acc["flags"].append("tracker-says-customer")
                acc["in_customer_tracker"] = True
                # tracker row => this is an active account, whatever the Sheet's status column says
                if acc["status"] != "Current":
                    acc["status"] = "Current"; acc["stage"] = "Customer"
                    acc["flags"] = [f for f in acc["flags"] if f not in ("tracker-says-customer", "ordered-but-not-current")]
                    report["tracker_set_active"] = report.get("tracker_set_active", 0) + 1
                if town and not acc.get("town"): acc["town"] = town
                JUNK_SOURCES = ("Random Data",)
                def better_name(ec, nm):
                    """Tracker names beat guesses from the Random Data tabs and empty/one-token junk; Master/CRM names stay."""
                    if not nm: return False
                    cur = (ec.get("name") or "").strip()
                    if not cur: return True
                    if str(ec.get("source", "")).startswith(JUNK_SOURCES): return True
                    if " " not in cur and len(cur) < 4: return True
                    if re.fullmatch(r"(purchasing|inventory|general|store|retail|wholesale)?\s*(manager|buyer|owner|director|email|gm|contact|inventory|purchasing|accounting|ap|ar)s?", cur, re.I): return True   # a job title, not a person
                    return False
                def add_role(ec, role):
                    """Roles are additive: the same person can be Buyer and Intake and Finance."""
                    cur = [x.strip() for x in (ec.get("role") or "").split("/") if x.strip()]
                    cur = [x for x in cur if x not in ("Billing", "AR", "Accounting", "Secondary")]  # weaker labels give way
                    if role not in cur: cur.append(role)
                    ec["role"] = " / ".join(cur)
                def upsert(em, nm, ph, role):
                    c = add_contact(acc, nm, em, ph, role, "Customer Tracker")
                    if c is None:
                        for ec in contact_emails.get(em, []) if em else [x for x in contacts if x["account_id"] == acc["id"] and x["name"].lower() == (nm or "").lower()]:
                            if ec["account_id"] == acc["id"]:
                                add_role(ec, role)
                                if not ec["phone"] and ph: ec["phone"] = ph
                                if better_name(ec, nm): ec["name"] = nm; ec["source"] = "Customer Tracker"
                # buyer(s): every email in the cell; names split on & , 'and' / newlines and paired by position when the counts match
                b_emails = split_emails(r.get("Buyer Email", ""))
                b_names = [x.strip(" .") for x in re.split(r"\s*(?:&|,|\band\b|\n|;|/)\s*", clean(r.get("Buyer Name", ""))) if x.strip(" .")]
                b_phone = parse_phone(r.get("Buyer Phone Number", ""))
                if not b_emails and b_names:
                    upsert("", " ".join(b_names) if len(b_names) == 1 else b_names[0], b_phone, "Buyer")
                for i, em in enumerate(b_emails):
                    nm = b_names[i] if len(b_names) == len(b_emails) else (b_names[0] if i == 0 and b_names else "")
                    upsert(em, nm, b_phone if i == 0 else "", "Buyer")
                # intake
                for em in split_emails(r.get("Intake Emails", "")):
                    upsert(em, "", "", "Intake")
                # finance
                f_phone = parse_phone(r.get("Finance Phone Number", ""))
                f_name = re.sub(r"[-–].*$", "", clean(r.get("Finance Phone Number", ""))).strip() if f_phone else ""
                for i, em in enumerate(split_emails(r.get("Finance Contacts", ""))):
                    upsert(em, f_name if i == 0 else "", f_phone if i == 0 else "", "Finance / AP")

        # ---- Order Tracker 2026
        ot = sheet(oxl, "Order Tracker 2026", header=0)
        if ot is not None:
            ot = ot.rename(columns={ot.columns[0]: "Date"})
            ot = ot.dropna(subset=["Location / License #"])
            for ridx, r in ot.iterrows():
                raw = clean(r.get("Location / License #", ""))
                if not raw: continue
                parts = re.split(r"\s*(?:\||\sI\s|\sl\s)\s*", raw, maxsplit=1)
                name_part = clean(parts[0]); lic = clean(parts[1]).upper() if len(parts) > 1 else ""
                licm = re.search(r"[A-Z]{2,4}\d{3,7}(?:-[A-Z])?", lic or raw.upper())
                lic = licm.group(0) if licm else ""
                contact_raw = clean(r.get("Contact", ""))
                c_email = split_emails(contact_raw); c_email = c_email[0] if c_email else ""
                c_name = re.sub(EMAIL_RE, "", contact_raw).strip(" -–:|,")
                acc = lic_index.get(lic) if lic else None
                if not acc:
                    # 'Silver Therapeutics Palmer' -> try full, then with trailing town stripped
                    acc, _ = find_account(name_part, c_email)
                    if acc:
                        # prefer sibling whose town appears in name_part
                        same = [x for x in accounts if norm_loose(x["name"]) == norm_loose(acc["name"]) and x.get("town")]
                        for x in same:
                            if norm(x["town"]) and norm(x["town"]) in norm(name_part): acc = x; break
                if acc and lic:
                    if not acc.get("license_number"): acc["license_number"] = lic; index_license(acc)
                    elif lic not in acc["license_number"] and "order-license-mismatch" not in acc["flags"]:
                        acc["flags"].append("order-license-mismatch")
                total = r.get("Order Total:", None)
                try: total = round(float(total), 2) if total is not None and not pd.isna(total) else None
                except Exception: total = None
                status = clean(r.get("Pending / Confirmed", ""))
                if not status: status = "Planned"          # blank in the tracker = placeholder, NOT a confirmed order
                elif status.lower().startswith("conf"): status = "Confirmed"
                elif status.lower().startswith("pend"): status = "Pending"
                o = {
                    "id": "O" + hashlib.sha1(f"{date_of(r.get('Date',''))}|{raw}|{ridx}".encode()).hexdigest()[:8],
                    "date": date_of(r.get("Date", "")), "account_id": acc["id"] if acc else None, "account_raw": raw,
                    "license": lic, "status": status, "contact_name": c_name, "contact_email": c_email,
                    "delivery_date": date_of(r.get("Req. Delivery Day(s)", "")), "total": total,
                    "owner": ("" if clean(r.get("Account Owner", "")).lower() == "allen" else clean(r.get("Account Owner", ""))), "mix": clean(r.get("Finished Goods / Bulk", "")),
                    "special": clean(r.get("Special Requests / Discount", "")), "samples": clean(r.get("Samples Given (Y/N)", "")).lower().startswith("y"),
                    "row": int(ridx) + 2,
                }
                orders.append(o)
                if acc:
                    report["orders_matched"] = report.get("orders_matched", 0) + 1
                    if c_email or c_name:
                        c = add_contact(acc, c_name, c_email, "", "Buyer", "Order Tracker 2026")
                        if c is None and c_email:
                            for ec in contact_emails.get(c_email, []):
                                if ec["account_id"] == acc["id"] and not ec["role"]: ec["role"] = "Buyer"
                    if status.lower().startswith("conf") and acc["status"] != "Current" and "ordered-but-not-current" not in acc["flags"]:
                        acc["flags"].append("ordered-but-not-current")
                else:
                    report["orders_unmatched"] = report.get("orders_unmatched", 0) + 1
        if merge:
            gone = set((existing_settings or {}).get("deleted_orders", []))
            if gone:
                before = len(orders); orders = [o for o in orders if o["id"] not in gone]
                report["tracker_orders_deleted_in_crm"] = before - len(orders)
            live = {o["id"]: o for o in orders}
            for o in existing_orders:
                if str(o.get("source", "")).startswith("CRM") and o["id"] not in live:
                    orders.append(o); report["crm_orders_preserved"] = report.get("crm_orders_preserved", 0) + 1
                elif o.get("edited_in_crm") and o["id"] in live:
                    # someone corrected this tracker order in the CRM: their version of the editable fields wins
                    for f in ("date", "status", "total", "delivery_date", "owner", "mix", "special", "samples", "contact_name", "contact_email", "edited_in_crm", "edited_by", "edited_at"):
                        if f in o: live[o["id"]][f] = o[f]
                    report["crm_order_edits_kept"] = report.get("crm_order_edits_kept", 0) + 1
        report["orders_total"] = len(orders)

    # ---------------- 11a. Merge mode: carry over CRM-created contacts and CRM-owned contact fields
    if merge and existing_contacts:
        live_ids = {c["id"] for c in contacts}
        acc_ids = {a["id"] for a in accounts}
        ex_by_id = {c["id"]: c for c in existing_contacts}
        for c in contacts:                       # CRM-owned fields on re-imported contacts
            e = ex_by_id.get(c["id"])
            if e:
                for f in ("phone", "role", "title", "notes", "do_not_contact"):
                    if e.get(f) and not c.get(f): c[f] = e[f]
        for c in existing_contacts:              # contacts that were created inside the CRM
            if c["id"] not in live_ids and str(c.get("source", "")).startswith("CRM") and (c.get("account_id") in acc_ids or not c.get("account_id")):
                contacts.append(c)
                report["crm_contacts_preserved"] = report.get("crm_contacts_preserved", 0) + 1

    # ---------------- 11b. Lead-vs-lead merge: leads that resolve to another (earlier) lead by variant/domain
    lead_ids = [a["id"] for a in accounts if a["source"] != "Master"]
    merged_into = {}
    for lid in lead_ids:
        a = acc_by_id[lid]
        if lid in merged_into: continue
        if a["source"] == "Customer Tracker": continue          # deliberate location rows — never auto-merge
        # any contact email on this lead whose domain maps to a *different* single account?
        emails = [c["email"] for c in contacts if c["account_id"] == lid and c["email"]]
        target = None
        for em in emails:
            d = email_domain(em)
            if d and d in idx_domain and len(idx_domain[d]) == 1 and idx_domain[d][0]["id"] != lid:
                target = idx_domain[d][0]; break
        if target is None:
            for v in name_variants(a["name"]):
                hits = [x for x in idx_variant.get(v, []) if x["id"] != lid and x["id"] not in merged_into]
                if hits: target = hits[0]; break
        if target is None: continue
        # same name but clearly different towns -> different locations, keep both
        if a.get("town") and target.get("town") and norm(a["town"]) != norm(target["town"]) and norm(a["town"]) not in norm(target["town"]) and norm(target["town"]) not in norm(a["town"]): continue
        # An account created inside the CRM that now also appears in the Sheet: ADOPT it — keep the CRM record
        # (its id is what activities, orders and contacts point at), take the Sheet's identity + Sheet-owned fields,
        # and drop the freshly-created Master duplicate.
        if str(a.get("source", "")).startswith("CRM") and target.get("source") == "Master":
            for f in ("sheet_key", "sheet_row", "name", "town", "address", "license_type", "status", "last_contact", "next_contact", "notes"):
                if target.get(f): a[f] = target[f]
            a["source"] = "Master"
            if (not a.get("owner") or a["owner"] == "Unassigned") and target.get("owner") not in (None, "", "Unassigned"): a["owner"] = target["owner"]
            if not a.get("grade") and target.get("grade"): a["grade"] = target["grade"]
            if not a.get("license_number") and target.get("license_number"): a["license_number"] = target["license_number"]
            a["tags"] = sorted(set((a.get("tags") or []) + (target.get("tags") or [])))
            a["flags"] = sorted(set(f for f in (a.get("flags") or []) + (target.get("flags") or []) if f != "unassigned" or a.get("owner") in (None, "", "Unassigned")))
            for c in contacts:
                if c["account_id"] == target["id"]: c["account_id"] = a["id"]
            for o in orders:
                if o["account_id"] == target["id"]: o["account_id"] = a["id"]
            merged_into[target["id"]] = a["id"]
            report["crm_accounts_adopted_by_sheet"] = report.get("crm_accounts_adopted_by_sheet", 0) + 1
            continue
        # prefer keeping a Master account, else the earlier-created lead
        if target["source"] != "Master" and a["source"] == "Master": a, target = target, a
        for c in contacts:
            if c["account_id"] == a["id"]: c["account_id"] = target["id"]
        for o in orders:
            if o["account_id"] == a["id"]: o["account_id"] = target["id"]
        for f in ("address", "town", "license_number"):
            if not target.get(f) and a.get(f): target[f] = a[f]
        for t in a.get("tags", []):
            if t not in target["tags"]: target["tags"].append(t)
        merged_into[a["id"]] = target["id"]
        report["leads_merged_into_other"] = report.get("leads_merged_into_other", 0) + 1
    accounts = [x for x in accounts if x["id"] not in merged_into]
    acc_by_id = {x["id"]: x for x in accounts}
    # recompute dup candidates now that the account set is final
    for a in accounts:
        if a["source"] != "Master":
            a["dup_candidates"] = [d for d in dup_candidates(a["name"], exclude_id=a["id"]) if d in acc_by_id]
            a["flags"] = [f for f in a["flags"] if f != "possible-duplicate"] + (["possible-duplicate"] if a["dup_candidates"] else [])

    # ---------------- 11c. Drop empty-shell leads (people-tab origin, nothing attached)
    PEOPLE_TABS = {"Random Data 31826", "Random Data 1926", "Random Data 6", "Random Data 1", "Random Data 2",
                   "Random Data 5", "Random Data 51926"}
    has_contact = {c["account_id"] for c in contacts if c["account_id"]}
    shells = [a["id"] for a in accounts if a["source"] in PEOPLE_TABS and a["id"] not in has_contact
              and not a.get("address") and not a.get("license_number")]
    accounts = [a for a in accounts if a["id"] not in shells]
    acc_by_id = {x["id"]: x for x in accounts}
    report["empty_shells_dropped"] = len(shells)

    # ---------------- 12. Parent chain detection (multi-location by identical name)
    by_name = {}
    for a in accounts: by_name.setdefault(norm_loose(a["name"]), []).append(a)
    for k, group in by_name.items():
        if len(group) >= 2 and k:
            for a in group:
                if not a.get("parent"): a["parent"] = group[0]["name"].strip()

    # ---------------- write
    if orders_path or orders:
        save_json(os.path.join(DATA, "orders.json"), sorted(orders, key=lambda o: (o["date"] or "", o.get("row", 0))))
    accounts.sort(key=lambda a: (a["name"].lower(), a["town"].lower()))
    contacts.sort(key=lambda c: ((c["account_id"] or "zzzz"), not c.get("primary"), c["name"].lower(), c["email"]))
    # The Master (and tracker-verified locations, and anything a rep has claimed) is the book.
    # Everything else is the Prospects pool — kept separate until someone claims it.
    def is_book(a):
        return a["source"] in ("Master", "Customer Tracker") or a.get("claimed") or a.get("in_customer_tracker")
    book  = [a for a in accounts if is_book(a)]
    leads = [a for a in accounts if not is_book(a)]
    for b in book: b["tags"] = [t for t in b.get("tags", []) if t != "Unprocessed Lead"]
    for l in leads:
        l["tags"] = [t for t in l.get("tags", []) if t != "Unprocessed Lead"]
        l["stage"] = ""; l["stage_entered"] = ""
    save_json(os.path.join(DATA, "accounts.json"), book)
    save_json(os.path.join(DATA, "leads.json"), leads)
    report["book_accounts"] = len(book); report["pool_leads"] = len(leads)
    save_json(os.path.join(DATA, "contacts.json"), contacts)

    sp = os.path.join(DATA, "settings.json")
    settings = load_json(sp, {})
    settings.setdefault("stages", DEFAULT_STAGES)
    settings.setdefault("statuses", ["Current", "Prospect", "Non-prospect", "Unspecified"])
    settings.setdefault("grades", ["A+", "A", "B", "C", "D"])
    owners = sorted({a["owner"] for a in accounts if a["owner"] and a["owner"] != "Unassigned" and "allen" not in a["owner"].lower()})
    settings["owners"] = sorted({o for o in set(settings.get("owners", []) + owners) if "allen" not in o.lower()})
    settings.setdefault("activity_types", ["Call", "Visit", "Text", "Email", "Note"])
    settings.setdefault("repo", {"owner": "dangillan1", "name": "ccgl-crm", "branch": "main"})
    settings["last_import"] = {"date": TODAY, "file": os.path.basename(path), "report": report}
    save_json(sp, settings)

    up = os.path.join(DATA, "users.json")
    if not os.path.exists(up):
        save_json(up, [{**u, "pw_hash": ""} for u in USERS])
    for u in USERS:
        ap = os.path.join(DATA, "activities", f"{u['id']}.json")
        if not os.path.exists(ap): save_json(ap, [])

    print(json.dumps(report, indent=2))
    if orders:
        print(f"Orders: {len(orders)}  matched to accounts: {report.get('orders_matched',0)}  unmatched: {report.get('orders_unmatched',0)}")
        um = [o["account_raw"] for o in orders if not o["account_id"]]
        if um: print("  unmatched examples:", sorted(set(um))[:12])
    print(f"\nBook accounts: {len(book)}   Prospect pool: {len(leads)}   Contacts: {len(contacts)}   Rogue (no account): {sum(1 for c in contacts if not c['account_id'])}")
    st = {}
    for a in accounts: st[a["status"]] = st.get(a["status"], 0) + 1
    print("Status:", st)
    ow = {}
    for a in accounts: ow[a["owner"]] = ow.get(a["owner"], 0) + 1
    print("Owner:", ow)
    tg = {}
    for a in accounts:
        for t in a["tags"]: tg[t] = tg.get(t, 0) + 1
    print("Tags:", tg)

if __name__ == "__main__":
    main()
