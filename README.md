# CCGL CRM

Web CRM for Cape Cod Grow Lab Wholesale. Same pattern as the Finance and Inventory dashboards:
a single static page on GitHub Pages, data stored as JSON in this repo, edits saved back through the
GitHub API with a Personal Access Token that lives only in your browser.

```
index.html              the app (no build step, no external dependencies)
data/accounts.json      THE BOOK — Master sheet accounts + tracker-verified locations + claimed leads (367)
data/leads.json         UNCLAIMED LEADS — deduped companies from Random Data / CCC tabs, not yet claimed (150)
data/contacts.json      one row per person, role-tagged Buyer / Intake / Finance where known (1,398)
data/orders.json        one row per 2026 order (318 from Order Tracker + any entered in the CRM)
data/insights.json      AI assessments shown on the Insights tab (regenerated at each import)
data/users.json         who can sign in + password hashes
data/settings.json      pipeline stages, owner names, last-import report
data/activities/*.json  one file per user — calls / visits / notes / claims / stage moves / next steps
tools/import_sheet.py   import / re-import from the Google Sheet + Order Tracker exports
tools/make_insights.py  assembles data/insights.json
tools/make_preview.py   builds preview.html (read-only, data inlined) for opening from disk
```

## Deploy (one time)

1. Create the repo `dangillan1/ccgl-crm` and push this folder (GitHub Desktop: *File → Add Local
   Repository* → pick the folder → *create a repository* → *Publish repository*).
2. Repo → Settings → Pages → Source: **Deploy from a branch** → `main` / `/ (root)` → Save.
3. Open `https://dangillan1.github.io/ccgl-crm/` (first build takes about a minute).

**About visibility.** GitHub Pages publishes the site publicly whatever the repo's visibility — a
private repo hides the source on github.com, but the live files (including `data/*.json`) are
reachable by anyone with the URL. That is the same trade-off the Inventory dashboard makes; the
sign-in is a convenience gate, not security. If you later want real access control, move the same
files to Cloudflare Pages with Access (free for a small team) — nothing in the app would change.

## First sign-in

Pick your name. **Any password works until you set one** — you're prompted to set it immediately.
Saving anything (including your password) needs the GitHub token:

1. github.com/settings/personal-access-tokens/new → name `CCGL CRM`
2. Repository access → *Only select repositories* → `ccgl-crm`
3. Permissions → Repository → **Contents: Read and write**
4. Generate, copy `github_pat_…`, paste into the app when asked (also under Settings).

One token is fine for the whole team: each person pastes it once into their own browser. The app
password is what identifies *who* made a change; the token is what lets the browser write to the repo.
Remove a token any time under Settings.

Reading works without a token (the Pages site serves the JSON); only saving needs it.

## Users and roles

| User    | Role    | Can                                                                 |
|---------|---------|---------------------------------------------------------------------|
| Dan     | admin   | everything, plus users/passwords                                    |
| Matt    | manager | edit, reassign owners (incl. bulk), merge duplicates, settings      |
| Joey    | rep     | edit accounts/people, log activity, move pipeline stages            |
| Charley | ops     | read everything; edit Delivery Notes                                |

Reps see *Today* scoped to their own book. Manager/admin see the whole book plus ownership and
duplicate cleanup queues. Add or rename users in `data/users.json` (set `pw_hash` to `""` for a new
user — they'll set a password at first sign-in). Reset a password from Settings (admin).

## How the app is organised

**The Master is the book.** Everything from the Random Data and CCC tabs sits in a separate
Unclaimed Leads list until a person claims it.

- **Insights** (home) — AI assessments (written at import) plus live ranked lists: revenue at risk,
  due for reorder this week, unowned revenue, rising / fading accounts, pipeline closest to first
  order, new CCC licenses, one-and-done, "Active" with no 2026 orders.
- **Active Accounts** — Master accounts with status Current. *Contacts* view shows the Customer
  Tracker per location (Buyer / Finance / Intake / standing note); *Orders* view shows reorder
  rhythm, last order, 2026 $. Managers can multi-select and bulk-assign owners.
- **Prospects** — Master accounts not yet active (Sheet status Prospect / Non-prospect), with stages
  (table or drag-and-drop board).
- **Unclaimed Leads** — the pool. **Claim** moves one into Prospects under your name at stage New and
  logs it. Domain-named leads ask for the real company name when claimed.
- **Orders 2026** — every order; **+ New order** (also in the top bar and on every account page).
  A confirmed first order moves a Prospect — or an unclaimed lead — to Active automatically.
- **Tasks** — open next steps grouped Overdue / Today / Upcoming, mine or everyone's.
- **People / Email List** — the directory. Email List hides generic inboxes (accounting@, info@,
  orders@ …) and personal domains by default; the *Finance* role chip does the opposite.
- **Account page** — action bar (Log call / visit / text / email / note · + New order · Set next
  step · Move stage · Claim), standing note in red (editable), key contacts by role, about, delivery
  notes, people, edit — and a timeline of activities and orders newest-first with the composer on top.
- **Activity Log** — everything logged plus the GitHub commit history (every save is a commit
  tagged `[user]`).

## Parallel run: who owns which fields

Until cutover, the Google Sheet and the Order Tracker stay the place you edit these:

| Source                       | Fields refreshed on re-import                                              |
|------------------------------|----------------------------------------------------------------------------|
| Sheet — main tab             | name, town, address, license type, status, last/next contact, notes, contact people 1–3 |
| Order Tracker — Customer Tracker | buyer / intake / finance contacts, license numbers                      |
| Order Tracker — Order Tracker 2026 | orders (rebuilt in full)                                              |

The CRM owns everything else and keeps it across re-imports: **owner, grade** (seeded from the Sheet,
then yours), **stage, tags, standing notes and delivery notes** (seeded from the tracker, then yours —
edit them on the account page), activities, and any people added inside the CRM. Master-sheet accounts show name/address/status read-only in the app for
this reason; lead accounts (not in the Sheet) are fully editable.

### Re-import

```bash
# export the Google Sheet and the Order Tracker as .xlsx, then:
python tools/import_sheet.py "CCGL Wholesale Contact List.xlsx" --orders "Order Tracker.xlsx" --merge
git add data && git commit -m "Re-import from Sheet" && git push
```

Accounts are matched by name+town, contacts by (account, email, name), so IDs are stable and nothing
the team logged in the CRM is lost. Rows that vanished from the Sheet are kept and flagged
`missing-from-sheet`.

### Cutover

When you trust the CRM: stop editing the Sheet, run one last `--merge`, and in `index.html` change
`const isLead = a.source!=='Master'` to `const isLead = true` so every account is fully editable.
(That one-line switch is deliberate — it's the only thing that changes.)

## Import notes (Sep 25, 2026)

- Book: 357 Master rows + 10 location rows the Customer Tracker had and the Sheet didn't = 367
  (138 Active, 229 Prospects: 136 Prospect + 87 Non-prospect + 6 unspecified).
- Unclaimed Leads: 150 companies after deduping every non-Master tab against the Master (1,956 people
  from those tabs matched existing accounts instead). 13 are new CCC licensees. 68 came in as a web
  domain and are flagged *name needed*.
- Orders: 318/318 matched, $1.18M confirmed 2026 to date. Multi-location chains split by license.
- Allen has left: his 125 accounts are Unassigned, "Allen/Joey" became Joey, his name is off the orders.
  197 accounts are Unassigned in total — Matt's bulk-assign queue.
- 21 accounts have confirmed 2026 orders but the Sheet still says Prospect/Non-prospect (there's a
  *Mark Active* button on each). 57 "Active" accounts have no 2026 orders.
- Sheet11 was skipped on purpose.

## Roadmap

1. ✅ Book / leads split, record pages, claim → prospect → first order flow, orders, tasks, Insights v1
2. Onboarding checklist per stage (intro call → sample drop → menu setup → launch promo → first reorder)
3. Weighted prioritisation in Insights (grade × staleness × stage × $) and per-rep daily queue
4. Manager view: activity volume by rep, pipeline movement, new accounts won, weekly roll-up
5. AI assessments regenerated automatically on import (API key in Settings)
6. Cutover: Sheet retired
