"""
How a QuickBooks customer finds its CRM account. Shared by tools/build_qbo.py (the 2026 feed) and
tools/qbo_history.py (invoices from before the 2026 Order Tracker).

  1. tools/qbo_map.json override for the customer name: one account id, or a list of ids for a chain-level
     customer (the location whose town matches the ship-to; no match -> every id, flagged split).
  2. Otherwise a name match against every book account and unclaimed lead; several locations -> the ship-to
     town decides; still ambiguous -> split.
  3. No match -> None (the caller lists it as unmapped).

    from qbo_resolve import Resolver
    r = Resolver(accounts, leads, cmap)
    target, split = r.resolve(customer_name, ship_town)   # target: account dict, list of dicts (split) or None
"""
import re

STOP = {"llc", "inc", "co", "corp", "company", "the", "of", "ma", "dba", "d/b/a", "cannabis", "dispensary", "wellness", "organics",
        "organic", "medicine", "collective", "naturals", "retail", "and", "&"}
LEGAL = {"llc", "inc", "co", "corp", "company", "the", "of", "ma", "dba"}

def tok(s): return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower().replace("&", " and ")) if t not in STOP}
def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())
def same_town(a, b): a, b = norm(a), norm(b); return bool(a and b) and (a == b or a in b or b in a)

class Resolver:
    def __init__(self, accounts, leads, cmap):
        self.accounts, self.leads, self.cmap = accounts, leads, cmap
        self.acc = {a["id"]: a for a in accounts}; self.lead_by_id = {l["id"]: l for l in leads}
        self.TOWN_NAMES = sorted({a["town"].strip() for a in accounts + leads if a.get("town")} | {"Boston", "Worcester", "Springfield", "Indian Orchard", "New Bedford"}, key=len, reverse=True)
        self._town_re = re.compile(r"\b(" + "|".join(re.escape(t) for t in self.TOWN_NAMES if t) + r")\b", re.I)
        self.by_family = {}
        for a in accounts + leads:
            for t in self.name_tok(a["name"]) | (tok(a.get("parent", "")) - {""}): self.by_family.setdefault(t, []).append(a)

    def strip_towns(self, s): return self._town_re.sub(" ", s or "")
    def name_tok(self, s): return tok(self.strip_towns(s)) or tok(s)                 # name without the town words (unless that leaves nothing)

    def candidates(self, customer):
        qn = self.name_tok(customer)
        if not qn: return []
        scored = []
        for a in {x["id"]: x for t in qn for x in self.by_family.get(t, [])}.values():
            an = self.name_tok(a["name"]) or self.name_tok(a.get("parent", "")); inter = qn & an
            if not inter: continue
            scored.append((len(inter) / len(qn | an), a))
        scored.sort(key=lambda x: -x[0])
        if not scored or scored[0][0] < 0.34: return []
        raw = lambda x: {t for t in re.findall(r"[a-z0-9]+", (x or "").lower()) if t not in LEGAL}
        exact = [a for s, a in scored if raw(a["name"]) == raw(customer)]        # 'Peak Cannabis Co' -> 'Peak Cannabis', not 'Peak Collective'
        if len(exact) == 1: return exact
        best = scored[0][0]
        return [a for s, a in scored if s >= best - 1e-9]                      # the family: same name, several towns

    def acc_town(self, a): return a.get("town") or next((t for t in self.TOWN_NAMES if t and re.search(r"\b" + re.escape(t) + r"\b", a["name"], re.I)), "")

    def pick(self, cands, customer, ship_town):
        """one account, or a list to split across"""
        if len(cands) == 1: return cands[0], False
        if ship_town:
            hit = [a for a in cands if same_town(self.acc_town(a), ship_town)]
            if len(hit) == 1: return hit[0], False
        towns_in_name = [a for a in cands if self.acc_town(a) and norm(self.acc_town(a)) in norm(customer)]
        if len(towns_in_name) == 1: return towns_in_name[0], False
        return cands, True

    def resolve(self, customer, ship_town=""):
        if customer in self.cmap:
            v = self.cmap[customer]; ids = v if isinstance(v, list) else [v]
            missing = [i for i in ids if i not in self.acc and i not in self.lead_by_id]
            if missing: raise SystemExit(f"qbo_map.json: {customer} -> unknown account id(s) {missing}")
            return self.pick([self.acc.get(i) or self.lead_by_id[i] for i in ids], customer, ship_town)
        c = self.candidates(customer)
        if c: return self.pick(c, customer, ship_town)
        return None, False
