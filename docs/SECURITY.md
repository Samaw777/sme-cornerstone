# Security, privacy and operations

This file separates **what the code enforces**, which the test suite verifies, from **what the
deployment must provide**. Do not claim a control is in place until you've verified it in your
own environment.

## Enforced in code (tested)

| Control | Where |
| --- | --- |
| Server-side authorisation on every merchant view (role + permission per business) | `merchants/access.py` `merchant_view` |
| Tenant isolation: every lookup is filtered by the current business; foreign ids return 404 | All merchant views; `tests/test_tenancy.py` |
| Staff can never grant or revoke consent; owners only | `BusinessMembership.has`, `tests/test_sharing.py` |
| Bank/MFI access is checked against an active consent's institution, categories and period on **every** request; revoked or expired consent blocks immediately | `sharing/access.py` `authorize` |
| Allowed and denied bank access attempts are audited; access receipts are visible to the merchant | `AuditEvent`, `AccessReceipt` |
| Institutions never receive documents or individual transactions | Portal and API expose passport summaries only |
| Partner credentials: salted hash of the secret, scopes, expiry, revocation; secrets never logged | `PartnerCredential`, `core/audit.py` key filter |
| Rate limiting per credential and per IP for failed authentication | `partner_api/auth.py` |
| Append-only records: source events, corrections, audit events, access receipts and passport snapshots can't be updated or deleted through the ORM | `core.models.ImmutableModel` |
| Transactions can't be edited in place, only amended or reversed with author, time and reason | `Transaction.save`, `ledger/services.py` |
| Upload checks: size limit, extension, declared type and file signature must agree; SHA-256 de-duplication | `ledger/services.py` `validate_upload` |
| Documents are stored in the database and streamed only to members (or to admins with active support access), with `nosniff`, `no-store` and a restrictive CSP | `ledger/views.py` `document_download` |
| Platform admins see financial records only through reasoned, time-limited support access that is audited and shown to the merchant | `pilot/views.py`, `SupportAccess` |
| CSRF protection, secure session and CSRF cookies, HSTS, SSL redirect and nosniff when `DEBUG=0` | `config/settings.py` |
| Row Level Security enabled on every table (blocks the Supabase REST API) | `core/apps.py` |
| Secrets only from environment variables; the app refuses to start without `SECRET_KEY` in production | `config/settings.py` |

## The deployment must provide (not verifiable from this repository)

- **Encryption in transit.** HTTPS is terminated by the host (Render) and the DB connection requires SSL. Confirm TLS versions and cipher policy with your host.
- **Encryption at rest.** This depends on the database provider and plan. Confirm it for your Supabase/Postgres plan in writing; document blobs live in the same database. For sensitive production data, consider moving documents to object storage with server-side encryption and per-object keys.
- **Secrets management.** Use the host's environment-variable store. Restrict who can read it, and never commit `.env`.
- **Shared rate-limit cache.** The default cache is per process. With multiple workers or instances, configure Redis or Memcached so limits apply globally.
- **Stronger partner authentication.** Agree mTLS or OAuth 2.0 client credentials with the partner, plus IP allow-listing or a private network path if available.
- **MFA** for owners, analysts and administrators (not implemented).
- **Separation of duties.** Production database and shell access should be limited to named people. It must be separate from platform-admin accounts and logged by the provider.
- **Security testing.** Get an independent penetration test before a real pilot.

## Backup and restore

- Enable your provider's automated backups. Supabase Pro adds daily backups and point-in-time recovery; confirm the retention for your plan.
- Take a logical backup before every migration in production:

  ```bash
  pg_dump "$DATABASE_URL" -Fc -f cornerstone-$(date +%F).dump
  ```

- **Restore drill (quarterly):** restore into a fresh database, set `DATABASE_URL` to it, then run `python manage.py migrate --check` and `python manage.py test`. Spot-check that a passport snapshot's `content_sha256` matches its stored data:

  ```bash
  pg_restore --no-owner -d "$RESTORE_URL" cornerstone-YYYY-MM-DD.dump
  ```

- Record the date and result of each drill. Don't describe backups as tested until a restore has succeeded.

## Credential rotation

| Secret | How to rotate |
| --- | --- |
| `SECRET_KEY` | Set a new value in the host's environment and redeploy. This signs users out and invalidates password-reset links. |
| Database password | Reset it in Supabase, update `DATABASE_URL`, redeploy. |
| Partner API credentials | Console → Institutions → issue new → partner switches → revoke old. Expiry is enforced; the default is 90 days. |
| Analyst or staff access | Remove membership (staff: owner → People; analyst: platform admin), or suspend the institution. |

## Retention and deletion (configuration required)

There are no automatic deletion jobs yet, because retention periods need legal review. Decide and
record the following:

- How long to keep transactions, source events and documents after an account closes. Source events and corrections are append-only by design.
- Audit event and access receipt retention. These should be at least as long as any consent they evidence.
- Passport snapshots. A revoked consent stops future access, but **copies already downloaded by an institution can't be recalled**. The UI states this.
- The process for a merchant deletion request. Today, deleting a `Business` cascades to its records; protected rows (receipts, snapshots) need an explicit legal decision before removal.

## Incident response basics

1. **Detect.** Watch the denied-access entries in **Console → Audit log**, and check credential `last_used_at` for unexpected use.
2. **Contain.** Revoke the affected API credentials, suspend the institution, remove memberships, or rotate `SECRET_KEY` to end all sessions.
3. **Assess.** Use `AuditEvent` and `AccessReceipt` to see exactly what was accessed: which business, categories, period, report version and consent.
4. **Notify.** Inform affected merchants and the partner institution as the agreement and law require. Confirm the obligations with legal counsel in advance.
5. **Recover and review.** Fix the cause, restore from backup if needed, and write up the timeline.

## Regulatory items needing current legal and partner review

These include licensing triggers, KYC/AML responsibility allocation, consent and data-sharing
rules, retention, cybersecurity expectations, cloud and outsourcing restrictions, and cross-border
data transfer. **This codebase does not claim compliance with any regulation.**
