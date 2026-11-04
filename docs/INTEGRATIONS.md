# Integrations

## Architecture

```
 Public web app (merchant, analyst portal, console)
        │  calls only service functions — never writes records directly
        ▼
 Financial services            ledger/services.py, ledger/importing.py, ledger/quality.py,
 (records, evidence, passport) passport/metrics.py, sharing/access.py
        │                              ▲
        ▼                              │ consent check on every request
 Source connectors (inbound)    Partner API (outbound, /api/v1)
 ledger/connectors/             partner_api/
```

The merchant application depends only on the connector interface and the service functions. A
real connector, a separately hosted API, or a move to hybrid or local hosting can therefore change
infrastructure without rewriting the merchant screens. The only external dependency is a
Postgres connection string.

## Source connectors

`ledger/connectors/base.py` defines the stable interface:

```python
class SourceConnector:
    key: str; name: str; is_demo: bool
    def list_accounts(self, business) -> list[ExternalAccount]
    def fetch_events(self, account_id, since=None) -> list[ExternalEvent]   # immutable external ids
    def fetch_balances(self, account_id) -> list[MonthlyBalance]            # for reconciliation
```

`registry.sync()` stores each event once as a `SourceEvent` (unique per source and external id)
and creates a system-verified `Transaction` that points to it. Syncing again is a no-op. Monthly
statement balances feed the reconciliation check.

| Connector | Status |
| --- | --- |
| `mock_bank` | **Demo.** Deterministic synthetic data, always `is_demo=True`, and it never contacts a bank. It includes one deliberate statement/feed difference so the reconciliation check can be seen working. |
| `partner_bank` | **Not configured.** Raises `ConnectorNotConfigured`. |

### To add a real connector you need

1. A signed data-sharing agreement with the bank, MFI or wallet provider.
2. Its API specification, sandbox and production endpoints, and institution-issued credentials, stored as environment secrets.
3. A legal review of consent wording for pulling the merchant's account data.
4. An implementation of `SourceConnector` in a new module, registered in `registry.CONNECTORS`, with `is_demo=False`.
5. Tests with recorded sandbox responses.

No working Ethiopian bank endpoint is included or implied by this repository.

## Partner API

See [API.md](API.md). Before a live pilot, agree the authentication upgrade (mTLS or OAuth client
credentials), field list, rate limits and support process with the partner. Record these in the
pilot agreement.
