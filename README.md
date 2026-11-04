# SME Cornerstone

An MVP financial platform for Ethiopian small businesses. Merchants keep financial records and
generate a **Business Financial Passport** that they can choose to share with a bank or microfinance
institution (MFI).

> The passport provides financial evidence and is not a credit decision. The platform does not lend,
> approve credit, score creditworthiness or replace a lender's underwriting.

It works on phones and desktops, and you can add it to your home screen. In Safari, use
**Share → Add to Home Screen**. In Chrome, use **Install app**.

## Implementation plan and assumptions

| Decision | Choice and reason |
| --- | --- |
| Stack | Django 5.2 + Postgres, server-rendered templates, no JS build step. The spec allowed a different stack "unless the existing environment dictates otherwise". This matches the owner's existing Steadfast app, its Supabase + Render deployment and its look. |
| Database | Postgres (Supabase session pooler works); SQLite for local dev and tests. Row Level Security is enabled on every table so Supabase's REST API exposes nothing. |
| Currency and dates | ETB only (`DecimalField(16, 2)` plus an explicit `currency` column). Gregorian calendar months in Africa/Addis_Ababa. Ethiopian-calendar display is a later localisation. |
| Localisation | English labels wrapped in `gettext`/`{% translate %}`, with `LANGUAGES` including Amharic. Translation files aren't written yet. |
| Scope | One merchant platform and one bank/MFI pilot. No billing, referral fees, lending, payroll or payments. |
| Files | Uploaded documents are stored in Postgres (`DocumentBlob`). They are never under a public URL and are streamed only through an authorised view. |
| Live integrations | None. A **mock bank connector** behind a stable interface produces demo data that is always labelled as demo. |
| Sharing authority | Only business owners can grant or revoke consent; staff cannot be given this permission. |
| Bank data | Institutions get monthly summaries, evidence breakdowns and indicators only, never individual transactions or documents. |

## What's in the box

### Roles (all checks are on the server)
- **Business owner**: everything for their business, including staff, connected sources, passport and sharing consent.
- **Business staff**: the owner grants any of *record*, *import*, *documents*, *receivables* and *reports*. Staff can never authorise sharing.
- **Bank/MFI analyst**: belongs to one institution. Sees only businesses with active consent for that institution, limited to the consented categories and period.
- **Platform administrator**: manages institutions, analysts, API credentials, identity validation, the pilot and operational issues. Cannot see merchant records unless they open **support access**, which needs a reason, lasts 30 minutes, is audited and is shown to the merchant.

### Merchant workspace
- **Onboarding**: business profile with optional TIN and trade licence. Each value is labelled *merchant-provided* until an administrator validates it against a document.
- **Dashboard**: period presets or a custom period. Shows revenue, operating expenses, net cash movement, unpaid invoices, evidence coverage, data-quality alerts and a monthly chart. Months with no records are drawn as "unknown", not zero.
- **Transactions**: record, filter (period, source, evidence class, category, status) and attach receipts. Amend or reverse with a reason; the original record is kept. Notes and disputes leave the record unchanged.
- **CSV import**: upload, then map columns, then preview (date and amount validation, duplicate and repeat detection, error list), then commit. Re-importing never double counts.
- **Receivables and obligations**: invoices with payments and aging, plus recurring obligations, all explicitly "recorded by the business".
- **Documents**: private uploads with type, size and signature checks, SHA-256 de-duplication, and a validation status.
- **Connected sources**: the demo connector, with idempotent sync and statement balances for reconciliation.
- **Data quality**: missing months, unusual changes, possible duplicates, reconciliation differences, large undocumented records, open disputes and future dates. All wording is neutral, and merchants can add explanations.
- **Business Financial Passport**: live preview, frozen versioned snapshots, PDF download and print.
- **Sharing and access**: authorise an institution in two steps (scope, then review), revoke, and see access receipts, blocked attempts and support access.

### Bank/MFI analyst portal
- Lists only authorised businesses.
- Shows the passport limited to the consented sections, plus a PDF if consented.
- Each view creates an access receipt and shows its reference.
- Receipts list for the institution.

### Partner API (v1)
Versioned, read-only and institution-scoped. Endpoints: profile, financial summary, verification
summary, data quality, consent status and audit receipts. See [docs/API.md](docs/API.md).

### Platform console
Overview and audit log, businesses (identity validation, support access, document review),
institutions (analysts, API credential issue/revoke/rotate, suspend), pilot measurement and
operational issues.

## Evidence classes

| Class | Meaning | How a record gets it |
| --- | --- | --- |
| System-verified | Imported from an authorised external source, original event and source id preserved | Synced through a connector (`source_kind=connector` with an external id) |
| Evidence-supported | Backed by a linked document; the document's validation status is shown | A non-rejected document linked to the record |
| Self-reported | Entered or imported by the merchant without independent verification | Default for manual entries and **all CSV imports** |
| Derived metric | Calculated from identified records with a documented method | Totals, trend, seasonality, variability. Never counted as evidence. |

Demo-connector data uses the system-verified mechanism but carries `is_demo`. It is labelled
**Demo** in the UI, passport, PDF ("CONTAINS DEMO DATA — NOT REAL VERIFICATION") and API
(`includes_demo_data`).

## Project layout

```
config/          Settings (all secrets from environment), root URLs, WSGI
accounts/        User model (email sign-in) and sign-up
core/            Evidence vocabulary, append-only base model, AuditEvent, period helpers,
                 template tags (icons, ETB formatting, SVG charts), demo seeding command
merchants/       Business, BusinessMembership (owner/staff permissions), tenant-access decorator,
                 onboarding, profile, staff, dashboard
ledger/          Transactions, SourceEvents, imports, corrections, annotations, documents, invoices,
                 obligations, connected sources, data-quality checks
  services.py    The only write paths for records (manual, amend, reverse, documents, links)
  importing.py   CSV parse/map/validate/dedupe/commit
  quality.py     Data-quality checks
  connectors/    Stable connector interface, mock bank, unconfigured partner placeholder
passport/        Calculation engine (metrics.py), CalculationVersion, PassportSnapshot, PDF
sharing/         Institutions, analysts, credentials, ConsentGrant, AccessReceipt,
                 consent enforcement (access.py), merchant consent views, analyst portal
partner_api/     /api/v1 — auth, rate limiting, endpoints
pilot/           Platform console, pilot program, manual metrics and feedback, support access
templates/       All HTML; static/css/app.css holds every style
tests/           Acceptance tests (integrity, consent, access control, API)
docs/            API, security/operations, integrations
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # set SECRET_KEY; leave DATABASE_URL empty for SQLite
python manage.py migrate
python manage.py seed_demo        # synthetic demo data + demo accounts
python manage.py runserver
```

Open <http://localhost:8000>. With `DEMO_MODE=1`, the sign-in page lists the demo accounts.

### Demo accounts (password `cornerstone-demo-2026`)

| Account | Role |
| --- | --- |
| `abebe@demo.cornerstone.et` | Owner, Abebe Coffee Roasters: 14 months of history, demo bank source, receipts, a CSV import, a correction, a dispute, invoices and obligations. Has active consent to the pilot MFI. |
| `meron@demo.cornerstone.et` | Staff at Abebe Coffee Roasters (record, import, documents, reports; no sharing) |
| `selam@demo.cornerstone.et` | Owner, Selam Tailoring: short history with a missing month; consent was **revoked** |
| `dawit@demo.cornerstone.et` | Owner, Hawassa Fresh Produce: consent to Second Demo Bank has **expired** |
| `analyst@pilot-mfi.demo` | Analyst, Pilot Partner MFI (Demo): sees Abebe only |
| `analyst@other-bank.demo` | Analyst, Second Demo Bank: sees nothing (expired consent) |
| `admin@demo.cornerstone.et` | Platform administrator |

Demo partner API token: `pk_demo_pilotmfi.demo-secret-not-for-production`

All businesses, institutions, documents and figures are synthetic. Set `DEMO_MODE=0` and never
run `seed_demo` against a database holding real data.

### Tests

```bash
python manage.py test
```

There are 57 tests in `tests/`. They cover these acceptance criteria:

| Criterion | Tests |
| --- | --- |
| Onboard, record, import CSV, attach evidence, generate a passport | `test_ledger`, `test_passport`, plus page smoke checks |
| Duplicate imports don't double count | `CsvImportTests.test_reimporting_the_same_file_does_not_double_count`, `…overlapping_export…`, `…identical_rows…` |
| Corrections preserve the original and the audit trail | `CorrectionTests.*` (incl. records/source events can't be edited in place) |
| Passport figures match records and documented methods | `PassportFigureTests.test_monthly_figures_match_records_and_methods`, `…evidence_breakdown…` |
| No access without valid consent | `PortalConsentTests.test_no_consent…`, `PartnerApiTests.test_no_consent…` |
| Revoked/expired consent blocks future access | `…test_revoked_consent_blocks_immediately`, `…expired…`, API equivalents |
| No cross-tenant access to records or documents | `TenantIsolationTests.*`, `AdminAccessTests.*` |
| Every bank access creates a receipt | `PortalConsentTests.test_every_access_issues_a_receipt`, API receipt tests |
| Demo sources visibly labelled | `DemoLabellingTests`, `EvidenceTests.test_mock_connector…` |
| Missing data never shown as zero | `PassportFigureTests.test_month_without_records_is_unknown_not_zero`, `…before_history…` |

## Deploying

The setup matches Steadfast: Supabase Postgres plus Render. See [DEPLOY.md](DEPLOY.md). Security,
backups, credential rotation, retention and incident response are covered in
[docs/SECURITY.md](docs/SECURITY.md).

## Status

| | |
| --- | --- |
| **Implemented and tested** | Everything listed under "What's in the box", the acceptance tests above, and PDF generation |
| **Simulated** | Bank/wallet data (mock connector), demo businesses and institutions, demo API credential |
| **Not implemented** | Live connectors, Amharic translations, Ethiopian-calendar display, email delivery of staff invites (owners get a one-time link), MFA, billing |

### Remaining production dependencies before a real bank pilot

1. **Legal and regulatory review** in Ethiopia. It needs to cover licensing, KYC/AML allocation, data-sharing, consent and retention rules, cybersecurity expectations, cloud and outsourcing rules, and cross-border data. Nothing here claims regulatory compliance.
2. **A signed pilot agreement with one bank/MFI**. It should cover the data-sharing terms, evaluation design and decision authority (100% retained by the institution).
3. **A live data-source connector**. This needs the partner's API specification, credentials and approval. Implement it in `ledger/connectors/` behind the existing interface (see [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)).
4. **Stronger partner authentication** (mTLS or OAuth 2.0 client credentials) agreed with the partner, plus a shared cache (e.g. Redis) for rate limits across workers.
5. **Hosting and data-residency decision**. Confirm encryption at rest, region and backup arrangements in your provider plan. Consider moving document storage to object storage with server-side encryption.
6. **MFA** for owners, administrators and analysts. Also add separation of duties for production access, security testing (penetration test) and tested restores.
7. **Amharic translation** of the interface, and optionally Ethiopian-calendar date display.
