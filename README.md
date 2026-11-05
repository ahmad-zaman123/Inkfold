<h1 align="center">📇 Inkfold</h1>

<p align="center">
  <b>Business documents into your CRM, checked by a person first.</b><br/>
  Upload or email in an invoice, receipt, purchase order, business card, lead form or resume. Inkfold reads it, cleans up the fields, shows you what it's unsure about and saves the contact, company, deal and note to HubSpot or Pipedrive, or a row to a Google Sheet. HubSpot can also go through an n8n workflow.
</p>

<p align="center">
  <a href="https://github.com/ahmad-zaman123/Inkfold/actions/workflows/ci.yml"><img src="https://github.com/ahmad-zaman123/Inkfold/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://inkfold-nine.vercel.app"><img src="https://img.shields.io/badge/live%20demo-inkfold--nine.vercel.app-000?logo=vercel&logoColor=white&style=flat-square" alt="Live demo" /></a>
  <img src="https://img.shields.io/badge/FastAPI-0.162-009688?logo=fastapi&logoColor=white&style=flat-square" alt="FastAPI" />
  <img src="https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black&style=flat-square" alt="React 18" />
  <img src="https://img.shields.io/badge/n8n-2.46-EA4B71?logo=n8n&logoColor=white&style=flat-square" alt="n8n" />
  <img src="https://img.shields.io/badge/HubSpot-CRM%20API-FF7A59?logo=hubspot&logoColor=white&style=flat-square" alt="HubSpot" />
  <img src="https://img.shields.io/badge/Pipedrive-API%20v1-017737?style=flat-square" alt="Pipedrive" />
  <img src="https://img.shields.io/badge/Google%20Sheets-API%20v4-34A853?logo=googlesheets&logoColor=white&style=flat-square" alt="Google Sheets" />
  <img src="https://img.shields.io/badge/Gemini-structured%20output-8E75B2?logo=googlegemini&logoColor=white&style=flat-square" alt="Gemini" />
  <img src="https://img.shields.io/badge/accuracy-312%2F312%20fields-2ea44f?style=flat-square" alt="312 of 312 fields read correctly" />
  <img src="https://img.shields.io/badge/tests-256%20passing-2ea44f?style=flat-square" alt="256 tests passing" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT license" /></a>
</p>

<p align="center">
  <a href="https://inkfold-nine.vercel.app"><b>Live demo → inkfold-nine.vercel.app</b></a><br/>
  <sub>Click <b>Try the live demo</b> to review three sample documents and send one to HubSpot. No sign-up needed.</sub>
</p>

<p align="center">
  <a href="#-quick-start">Quick start</a> ·
  <a href="#-how-it-works">How it works</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-api">API</a> ·
  <a href="#-limits">Limits</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/review-dark.png" />
    <img src="docs/screenshots/review.png" alt="The review screen for a lead form: extracted fields with where each came from, a phone number flagged for a missing country code, and the Send to HubSpot panel" width="860" />
  </picture>
</p>

---

## 🧭 What is Inkfold?

Copying details from PDFs and photos into a CRM is slow, and a model that does it unattended will sooner or later put a wrong amount on a deal. Inkfold keeps a person in the loop: the model extracts, code checks and normalizes, the person fixes what's flagged, and only then does anything reach the CRM.

| 📄 Reads | 🧹 Checks | 🔗 Saves |
|---|---|---|
| Invoices, receipts, purchase orders, business cards, lead forms and resumes, as PDFs, photos or text files, with each field's source text and a confidence score | Emails, phone numbers (E.164), dates, money, currencies and domains, plus whether line items add up to the total | A contact (matched by email), its company, a deal for an invoice or purchase order and a note, all linked, in HubSpot or Pipedrive. Or one row per document in a Google Sheet |

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/synced-dark.png" />
    <img src="docs/screenshots/synced.png" alt="An invoice after sending: the contact, company, deal and note IDs in HubSpot, links to open them, and the send history" width="860" />
  </picture>
</p>

### 🎯 Accuracy

On a test set of 24 made-up documents (invoices in five countries' formats, business cards including a blurry phone photo, hand-filled lead forms and emails), Inkfold read **312 of 312 fields correctly**, including leaving blank every field the document doesn't have, and recognised the document type in 23 of 24. The first run scored 99%: Gemini had read everything right, but Inkfold's own clean-up turned a German total of `2.444,26 €` into 2.44 and missed a `$` it could have taken the currency from. Both are fixed and tested. Full results, every mistake and how to rerun it are in [`evals/`](evals/results.md). The set doesn't cover receipts, purchase orders or resumes yet.

The sample documents in [`samples/`](samples/) are made up. All the people and companies in them are fictional.

## 🚀 Quick start

**Prerequisites:** Python 3.12, Node 18+ and a [Gemini API key](https://aistudio.google.com). Sending records needs a HubSpot service key (see **HubSpot setup** below). No Docker needed: `scripts/dev_db.py` starts a bundled Postgres.

**1. Backend** → `http://localhost:8020`

```bash
cd Backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                 # fill in the values
.venv/bin/python scripts/dev_db.py   # put the printed URL in .env as DATABASE_URL
.venv/bin/python scripts/init_db.py  # creates the tables
.venv/bin/uvicorn asgi:app --port 8020
```

**2. Frontend** → `http://localhost:5180`

```bash
cd Frontend
npm install
npm run dev
```

**3. n8n (optional)** → `http://localhost:5690`

To send records through n8n instead of straight to HubSpot, see [`n8n/README.md`](n8n/README.md). It's one script.

**4. Tests**

```bash
cd Backend && .venv/bin/python -m pytest      # 246 tests, real Postgres, fake Gemini, HubSpot, Pipedrive and Sheets
node --test n8n/test/*.test.mjs                # 10 tests for the n8n Code nodes
Backend/.venv/bin/python n8n/test/e2e.py       # API + n8n + fake HubSpot round trip
```

No test calls Gemini, HubSpot, Pipedrive or Google, so none of them need keys.

<details>
<summary><b>🔧 Environment variables</b> (<code>Backend/.env</code>)</summary>
<br/>

| Key | Required | Notes |
|---|---|---|
| `DATABASE_URL` | **yes** | Postgres. Use the pooled URL on Neon. |
| `JWT_SECRET` | **yes** | Signs session tokens. Use a long random string. |
| `GEMINI_API_KEY` | **yes** | Reads the documents. |
| `GEMINI_EXTRACT_MODELS` | no | Comma-separated, tried in order when one is busy, out of quota or retired. |
| `HUBSPOT_TOKEN` | to send | HubSpot service key, or a legacy private app token on older accounts. Without it (and without n8n), "Send to HubSpot" explains that HubSpot isn't connected. |
| `HUBSPOT_PORTAL_ID` | no | Your HubSpot account ID, so the app can link to the records it made. |
| `HUBSPOT_DEAL_STAGE` | no | Stage for new deals, by name or ID. Defaults to `closedwon`, since an invoice is a sale that happened. Newer HubSpot accounts use numeric stage IDs, so a name is looked up in the default pipeline. |
| `N8N_WEBHOOK_URL` | no | When set, records go through the n8n workflow instead of straight to HubSpot. |
| `PIPEDRIVE_API_TOKEN` | for Pipedrive | A personal API token (Pipedrive → **Personal preferences → API**). Adds Pipedrive to the team's choices. |
| `PIPEDRIVE_DOMAIN` | no | Your company subdomain, as in `acme` for acme.pipedrive.com, so the app can link to the records it made. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | for Sheets | The service account's JSON key, as one line. Adds Google Sheets to the team's choices. |
| `GOOGLE_SHEETS_ID` | for Sheets | The sheet's ID, the long part of its address between `/d/` and `/edit`. Share the sheet with the service account's email as an editor. |
| `GOOGLE_SHEETS_TAB` | no | The tab rows go in. Defaults to `Inkfold`, and is created with a header row if it isn't there. |
| `N8N_SHARED_SECRET` | with n8n | Signs requests both ways. Use a long random string. |
| `PUBLIC_API_URL` | with n8n | The address n8n can reach this API at, for its callback. |
| `CORS_ALLOWED_ORIGINS` | prod | The deployed frontend's address, comma-separated if more than one. The first is also used for links in emails. |
| `SMTP_USER`, `SMTP_PASSWORD` | for reset | Turn on password reset by email. For Gmail, use an [app password](https://myaccount.google.com/apppasswords). Without them, "Forgot password?" is hidden. |
| `SMTP_HOST`, `SMTP_PORT`, `EMAIL_FROM` | no | Default to Gmail (`smtp.gmail.com`, `587`) and the `SMTP_USER` address. |
| `GOOGLE_CLIENT_ID` | for Google | A Google OAuth web client ID. Turns on Continue with Google. Set the same value as `VITE_GOOGLE_CLIENT_ID` on the frontend. |
| `UPLOADS_PER_USER_DAY`, `UPLOADS_PER_DAY` | no | Upload limits, which emailed documents count against too. Default 10 per user and 100 in total per day. |
| `INTAKE_EMAIL`, `INTAKE_PASSWORD` | for email in | The inbox that forwarded documents arrive in, and its password. For Gmail, use an [app password](https://myaccount.google.com/apppasswords). It can be an account you use for other things: only mail sent to its `+inkfold-` addresses is read or marked read. Without them, email intake is hidden. |
| `INTAKE_IMAP_HOST` | no | Defaults to `imap.gmail.com`. Any IMAP inbox that delivers `name+anything@` addresses works. |
| `INTAKE_POLL_SECRET` | no | Lets the scheduled job check the inbox. Without it, mail is read when someone presses **Check inbox now**. |

Frontend (`Frontend/.env`): `VITE_API_URL` is the deployed backend's address, left empty locally because Vite proxies `/api`. `VITE_GOOGLE_CLIENT_ID` shows the Google button; add the site's address to the client's **Authorized JavaScript origins** in Google Cloud.

</details>

<details>
<summary><b>🔑 HubSpot setup</b> (free CRM is enough)</summary>
<br/>

1. In HubSpot, open **Settings → Development → Legacy apps** and choose **Create a service key** (new accounts no longer offer private apps there). Name it Inkfold. On older accounts a legacy private app works too.
2. Give it read and write access to `crm.objects.contacts`, `crm.objects.companies` and `crm.objects.deals`.
3. Put the key in `HUBSPOT_TOKEN`. Your account ID (in the account menu, top right) goes in `HUBSPOT_PORTAL_ID`, so the app can link to the records it creates.

Deals land in the default pipeline at the stage in `HUBSPOT_DEAL_STAGE`. Newer accounts use numeric stage IDs, so Inkfold reads the pipeline and finds "Closed won" for you.

</details>

<details>
<summary><b>🔑 Demo account</b> (live site)</summary>
<br/>

Click **Try the live demo**. You get a private account with three sample documents already read: an invoice, a business card and a lead form. Review them, fix the flagged phone number, and send one to the CRM. Uploading your own file uses Gemini as normal.

Demo accounts are deleted after two days. Each address can open 10 demos an hour, and 500 can be opened a day in total.

</details>

## 🧩 How it works

```
 upload or email ──▶ type check by file contents ──▶ duplicate check (sha256)
                                                   │
                                   Gemini, structured JSON output
                                                   │
                       normalize + cross-check (code, not the model)
                                                   │
                       review: fix flagged fields, see where each came from
                                                   │
                   ┌───────────── Send to HubSpot ─┴───────────────────┐
                   ▼ direct                                            ▼ n8n
     contact upsert, company, deal, note,              signed webhook ─▶ workflow ─▶ HubSpot
     default associations                              signed callback ◀────────────┘
                   └──────────────▶ synced, with links to the records ◀┘
```

The model only proposes values. Code decides what they mean: it parses the dates and amounts, works out the currency, formats phone numbers, checks emails and catches line items that don't add up. The document's text is passed as data, and the prompt says so, so instructions hidden in an upload are read as content rather than followed.

## ✨ Features

<table>
<tr>
<td width="50%" valign="top">

### 🔍 Every value shows its source
Each field keeps the text it was read from and a confidence score. Low-confidence fields are flagged, and anything you change is marked as edited, with who changed it.

### 📄 The original beside the fields
The uploaded file sits next to the fields, with every value highlighted where it was read. Hover or focus a field and its highlight lights up and scrolls into view; click a highlight to jump to its field. PDFs render with pdf.js and text files as text. Photos have zoom but no highlights, since they have no text layer.

### 🧮 Checks the model can't skip
Line items are added up and compared with the total. A due date before the issue date is flagged. A deal is only created when the total is clean.

### 👀 Warns before a duplicate
Before you send, the review page looks up what HubSpot already has. An existing contact or company is shown with when it was last updated, and sending updates it. A deal with the same name, such as an invoice sent once before from another file, is a warning, and the button changes to "Send anyway".

### 🔁 Safe to send twice
Contacts are upserted by email, and a re-send updates the same company, deal and note. A record deleted in HubSpot since the last send is made again. Sending is claimed with one atomic update, so a double click can't create two deals.

### 🧯 Model fallback
If a Gemini model is overloaded, out of quota or retired, the next one in the list is tried, within a 40 second budget. A bad API key is reported as a setup problem, not as a broken file.

### 🔐 Accounts
Email and password or Continue with Google, with scrypt hashes and 8-hour sessions. Google only signs in to an address it has confirmed, and it drops any password set on that address by someone who never proved they own it. Changing or resetting a password signs out every other session. Reset links are single use, expire in 15 minutes and are stored only as hashes, and the reply never reveals whether an email has an account.

</td>
<td width="50%" valign="top">

### 📬 Email them in
Every account gets a private address such as `you+inkfold-k3x9q2abcd@gmail.com`. Forwarded attachments are read, and an email with no attachment is read from its text, which suits lead emails. One inbox serves everyone, and the part after `+inkfold-` says whose mail it is. Only those addresses are touched, so the inbox can be an account you also use for other things. Each email is read once, even when two checks run together, and a scheduled GitHub Action checks every 10 minutes.

### 🔀 Direct or through n8n
The same HubSpot calls run either in the API or in an n8n workflow you can extend with your own steps. The workflow ships as JSON with its Code nodes kept as tested source files.

### ✍️ Signed both ways
Requests to n8n and its callbacks carry an HMAC-SHA256 signature over the timestamp and exact body, checked in constant time and refused after 5 minutes. A repeated callback changes nothing.

### 🎯 HubSpot, Pipedrive or Google Sheets
The team owner picks where documents go on the Team page, from whichever the server has keys for. Pipedrive gets a person, organization, a won deal for an invoice or purchase order, and a note, matched by email and name since it has no upsert. A sheet gets one row per document with the Inkfold ID in the first column, so sending again updates the same row even after the sheet is sorted. Values are written as plain text, so a document can't slip a formula into the sheet. A document already sent to one place can be sent to the new one after a switch.

### 👥 Teams
Invite teammates by email to review the same documents and send them to the same destination. Everyone sees who added each document, who edited each field and who sent it. If two people edit the same document, the second save is refused rather than quietly overwriting the first, and "Load their version" brings in the newer copy with your changes still in place. Invite links are single use, tied to one email address, expire in 7 days and are stored only as hashes. Someone working alone who joins brings their documents along.

### 📚 Batch upload
Drop up to 10 files at once. They're read two at a time, each with its own result, and "Next to review" walks you through whatever is still waiting.

### 🛡️ Careful uploads
4 MB limit, file type checked from the bytes rather than the name, filenames cleaned, duplicate uploads spotted, and per-user and global daily limits stored in Postgres.

### 🧪 Private demos
"Try the demo" creates a private account with three sample documents already read, so it costs no Gemini calls. Demo accounts are deleted after two days.

</td>
</tr>
</table>

## 🔌 API

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/auth/register`, `/api/auth/login` | Create an account or sign in. Returns a token. |
| `POST` | `/api/auth/google` | Sign in with a Google ID token. Creates the account on first use. |
| `POST` | `/api/auth/demo` | Create a private demo account with sample documents. |
| `GET` | `/api/me` | The signed-in user. |
| `GET` | `/api/auth/options` | Which sign-in extras are turned on: password reset and Google. |
| `POST` | `/api/password/forgot`, `/api/password/reset` | Email a reset link, then use it to set a new password. |
| `POST` | `/api/me/password` | Change the password. Signs out every other session and returns a new token. |
| `POST` | `/api/documents` | Upload a file (multipart `file`). Returns the extracted document. |
| `GET` | `/api/intake` | Your intake address and what your recent emails turned into. |
| `POST` | `/api/intake/check` | Read new emails now. |
| `POST` | `/api/intake/address` | Replace your intake address. The old one stops working. |
| `POST` | `/api/intake/poll` | The scheduled check, with `Authorization: Bearer INTAKE_POLL_SECRET`. |
| `GET` | `/api/documents/{id}/file` | The original upload, served sandboxed so it can't run as a page. |
| `GET` | `/api/documents/{id}/matches` | The contact, company and deal already in HubSpot or Pipedrive that a send would touch. |
| `GET` | `/api/documents`, `/api/documents/{id}` | List documents, or one with its fields, issues and sync history. |
| `PATCH` | `/api/documents/{id}/fields` | Save edits, as `{"fields": {"contact.phone": "+1 617 555 0123"}, "base_updated_at": "..."}`. Add `"doc_type": "receipt"` to correct the type. `409` if someone saved since `base_updated_at`. |
| `POST` | `/api/documents/{id}/sync` | Send to the team's destination. `422` with the missing fields if it isn't ready. |
| `POST` | `/api/sync/callback` | n8n's signed result. |
| `DELETE` | `/api/documents/{id}` | Delete a document from Inkfold (not from the CRM or sheet). |
| `GET`, `PATCH` | `/api/team` | Your team, its people and open invites. `PATCH` renames it (owner only). |
| `PATCH` | `/api/team/destination` | Choose HubSpot, Pipedrive or Google Sheets (owner only). `400` if the server isn't set up for it. |
| `POST` | `/api/team/invites` | Invite an email. Returns the link, and emails it when SMTP is set. |
| `DELETE` | `/api/team/invites/{id}`, `/api/team/members/{id}` | Cancel an invite, or remove someone (owner only). |
| `POST` | `/api/team/leave` | Leave the team. What you added stays with it. |
| `GET`, `POST` | `/api/invites/{token}`, `/api/invites/{token}/accept` | What an invite is for, then accept it. |

## 🚦 Limits

- One contact per document. Inkfold reads a single person from each file.
- Scanned PDFs work when Gemini can read them; there's no separate OCR step.
- Deleting a document in Inkfold leaves its CRM records and sheet row in place.
- Deals use HubSpot's default pipeline, and Pipedrive's default one. A job title goes in the Pipedrive note, since not every account has that field on people.
- n8n only carries HubSpot sends. Pipedrive and Sheets are sent from the API.
- Switching destinations doesn't move what was sent before. Each document remembers where it went.
- A team has one owner, who can't hand the role over or leave. The destinations are the accounts the server is set up with; teams choose among them but can't connect their own.
- An owner with teammates can't join another team until they've removed them. The demo can't invite or join.
- The duplicate warning needs `HUBSPOT_TOKEN`. With n8n alone there's nothing to search HubSpot with, so it doesn't show.
- A batch counts against the daily upload limit like single uploads, so the default of 10 per user is one full batch.
- Originals are kept for 30 days, in Postgres, so the database stays small. After that the fields stay and the original pane says the file is gone. Documents from before this feature have no original.
- Highlights match the quoted text, so a value the model reworded, or one on a scanned page without a text layer, isn't highlighted.
- One check reads up to 5 documents, so it stays inside Vercel's time limit. Anything more waits for the next check.
- GitHub runs scheduled jobs on a best-effort basis, so "every 10 minutes" can stretch at busy times. It also pauses them after 60 days without a commit.

## 🛠️ Tech stack

| Layer | Tech |
|---|---|
| **Backend** | Python 3.12, FastAPI, psycopg 3, PyJWT, phonenumbers, google-auth |
| **AI** | Google Gemini with structured JSON output and model fallback |
| **CRM** | HubSpot CRM API v3 and v4 associations, Pipedrive API v1, Google Sheets API v4 with a service account |
| **Automation** | n8n 2.x workflow with Code nodes and HMAC-signed webhooks |
| **Frontend** | React 18, Vite, React Router |
| **Data** | PostgreSQL on Neon |
| **Deployment** | Vercel (serverless Python + static React) |

## 🗂️ Project structure

```
Inkfold/
├── Backend/
│   ├── api/index.py        Vercel entry point
│   ├── app/
│   │   ├── config.py       settings from the environment
│   │   ├── crm.py          HubSpot client: upserts, search, associations, stage lookup
│   │   ├── demo_data.py    the demo's pre-read sample documents
│   │   ├── demo_files/     the sample files, served as the demo's originals
│   │   ├── extract.py      Gemini extraction with model fallback
│   │   ├── mailer.py       password reset emails over SMTP
│   │   ├── intake.py       email intake: addresses, parsing and IMAP
│   │   ├── main.py         API routes, auth, uploads, rate limits
│   │   ├── normalize.py    field cleaning and cross-checks
│   │   ├── pipedrive.py    Pipedrive client: persons, organizations, deals, notes
│   │   ├── schema.py       the fields Inkfold extracts
│   │   ├── sheets.py       Google Sheets: service account sign-in, one row per document
│   │   ├── store.py        schema and every SQL query
│   │   └── sync.py         signing and the n8n hand-off
│   ├── scripts/            local Postgres and table setup
│   ├── tests/              246 tests against a real Postgres
│   ├── asgi.py
│   ├── requirements.txt
│   └── vercel.json
├── Frontend/
│   ├── src/
│   │   ├── components/     upload, email intake, original viewer, field rows, line items, send panel, Google button
│   │   ├── pages/          landing, sign-in, password reset, account, team, join, documents, review
│   │   ├── api.js          API client
│   │   ├── highlight.js    finds each field's source text in the original
│   │   └── styles.css
│   ├── package.json
│   └── vercel.json
├── n8n/
│   ├── document-to-hubspot.json   the workflow to import
│   ├── src/                       its Code nodes as plain files
│   ├── test/                      Code node tests and the end-to-end check
│   └── run-local.sh
├── samples/                fictional invoice, business card and lead form
└── docs/screenshots/
```

## ☁️ Deployment & CI

- **Vercel**, as two projects from this repo: `Backend` (serverless Python) and `Frontend` (Vite). Postgres is on Neon. Set `DATABASE_URL`, `JWT_SECRET`, `GEMINI_API_KEY`, `HUBSPOT_TOKEN`, `HUBSPOT_PORTAL_ID` and `CORS_ALLOWED_ORIGINS` on the backend, plus the Pipedrive and Google Sheets keys if you use them, and `VITE_API_URL` on the frontend. Create or update the tables with `scripts/init_db.py`, which is safe to run again after an upgrade.
- **Email intake** (`intake.yml`) calls `/api/intake/poll` every 10 minutes. Set the `INKFOLD_API_URL` repository variable and the `INTAKE_POLL_SECRET` secret, the same value as on the backend. Until both are set, it does nothing.
- **CI** (`ci.yml`) runs the backend tests, the n8n Code node tests, a check that the workflow JSON matches its source files, and the frontend build on every push and pull request.

## 📄 License

[MIT](LICENSE) © 2026 Ahmad Zaman
