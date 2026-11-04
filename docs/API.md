# Partner API v1

This is a read-only, institution-scoped API for an authorised bank or MFI. It returns only the
data categories that a business has consented to, for the consented period. It does not provide
database access, individual transactions or documents.

Base path: `/api/v1/`. Responses are JSON with `Cache-Control: no-store` and `X-API-Version: v1`.

## Authentication

```
Authorization: Bearer <key_id>.<secret>
```

- Platform administrators issue credentials per institution: **Console → Institutions → Issue a credential**. The secret is shown once and only a salted hash is stored.
- Each credential has **scopes**, an **expiry** (1–365 days) and can be **revoked** instantly.
- To rotate, issue a new credential, switch the client over, then revoke the old one.
- Suspending an institution blocks every credential and analyst it has.
- Production pilots should add mTLS or OAuth 2.0 client credentials as agreed with the partner. See `SECURITY.md`.

| Scope | Endpoint |
| --- | --- |
| `consent:read` | `GET businesses`, `GET businesses/{id}/consent-status` |
| `profile:read` | `GET businesses/{id}/profile` |
| `financial_summary:read` | `GET businesses/{id}/financial-summary` |
| `verification_summary:read` | `GET businesses/{id}/verification-summary` |
| `data_quality:read` | `GET businesses/{id}/data-quality` |
| `credit_metrics:read` | `GET businesses/{id}/credit-metrics` |
| `receipts:read` | `GET receipts` |

## What happens on every request

1. The credential is authenticated and its scope checked. A failure returns 401 or 403 and logs an `api.auth` audit event, without the secret.
2. Requests are rate limited per credential (`PARTNER_API_RATE_LIMIT`, default 60/min). Failed authentication is also limited per IP (default 20/min). Either limit returns 429 with `Retry-After`.
3. For business endpoints, the API checks for an **active consent** from that business to the credential's institution. The consent must cover the requested **category** and the whole requested **period**. Revoked and expired consents fail immediately.
4. Every allowed or denied attempt writes an audit event: identifiers and reasons only, never payloads.
5. Allowed requests create an **access receipt**. The merchant can see it, and it is returned as `meta.receipt_reference`.

## Endpoints

### `GET /api/v1/businesses?page=1&page_size=25`
Businesses with an active consent for your institution. `page_size` can be at most 100. `name`
is null unless `profile` is consented.

### `GET /api/v1/businesses/{business_id}/profile`
### `GET /api/v1/businesses/{business_id}/financial-summary?from=YYYY-MM&to=YYYY-MM`
### `GET /api/v1/businesses/{business_id}/verification-summary`
### `GET /api/v1/businesses/{business_id}/data-quality`
### `GET /api/v1/businesses/{business_id}/credit-metrics`

`from` and `to` are optional and default to the consented period. They must fall inside it.

Every response carries this `meta` block:

```json
"meta": {
  "api_version": "v1",
  "business_id": "…",
  "period": {"start": "2025-11-01", "end": "2026-10-31", "months": 12, "months_with_records": 12},
  "includes_demo_data": true,
  "calculation_version": "2026.3",
  "report_version": 7,
  "consent_reference": "CNS-…",
  "receipt_reference": "RCPT-…",
  "currency": "ETB",
  "disclaimer": "This report provides financial evidence and is not a credit decision.",
  "limitations": ["…"]
}
```

Money values are decimal **strings**, such as `"12000.50"`. A month without records has `null`
values and `"state": "no_records"`. It is never zero.

`financial-summary` returns `monthly[]` and `totals`. Revenue and operating expenses are shown
separately from cash in, cash out and net cash movement. It also returns `trend`, `seasonality` and
`variability`; each has `available: false` with a `reason` when history is insufficient.

`verification-summary` returns `evidence.by_class[]`, covering system-verified,
evidence-supported and self-reported records. Each class has an amount, count, percent and demo
amount. The response also gives the explicit `denominator`, a document review summary and the
connected `sources` (with `is_demo`).

`data-quality` returns missing months, reconciliation status and neutral `findings`, counted by
type and month. Notes the business wrote to explain an indicator are included, except for disputes.

`credit-metrics` returns `credit_metrics.scores[]` (Evidence strength and Data completeness, 0–100)
and `credit_metrics.metrics[]`: operating cash margin, months with operating surplus, revenue
stability, debt service coverage, receivable days, cash buffer and digital payment share. Each item has `available`,
`value`, `unit`, its `inputs` (with `unit: "ETB"` on money amounts), a `basis`, and a `reason` when it
can't be calculated. These are indicators, **not a credit score or recommendation**; there is no
composite number.

### Privacy: aggregates only

Everything an institution receives through the API, portal or PDF contains aggregates and metrics
only. That includes no individual transactions, documents, counterparties, or notes about a
specific record. Obligations are totalled by type. The business's own passport view keeps the full
detail.

### `GET /api/v1/businesses/{business_id}/consent-status`
Returns `active`, `revoked` or `expired`, with the consent's scope, for any consent this business
has ever granted your institution. If there has never been one, it returns 403 `no_consent`. This
call also creates a receipt.

### `GET /api/v1/receipts?page=1&page_size=25`
Your institution's access receipts. Each has the requester, timestamp, channel, resource,
categories, period, report version, calculation version and consent reference.

## Errors

```json
{"error": {"code": "consent_inactive", "message": "The business's authorisation has been revoked or has expired."}}
```

| Status | Codes |
| --- | --- |
| 400 | `invalid_parameter` |
| 401 | `missing_credentials`, `malformed_credentials`, `invalid_credentials`, `credential_inactive` |
| 403 | `scope_not_granted`, `no_consent` (also returned for unknown business ids, so ids can't be probed), `consent_inactive`, `category_not_permitted`, `period_out_of_scope`, `institution_inactive` |
| 429 | `rate_limited` |

## Try it with demo data

```bash
python manage.py seed_demo
python manage.py runserver
```

```bash
TOKEN=pk_demo_pilotmfi.demo-secret-not-for-production
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/businesses
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v1/businesses/<business_id>/financial-summary?from=2026-05"
```

The demo token belongs to the fictional "Pilot Partner MFI (Demo)". **No real Ethiopian bank
endpoint is implemented or implied.** A live partner integration needs an approved agreement, the
partner's API specification and institution-issued credentials.
