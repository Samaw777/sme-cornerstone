"""Stable integration interface for external transaction sources.

A connector turns an authorised external account (bank, MFI or mobile-money
wallet) into source events. Transactions synced through a connector are the
only ones classed as system-verified, and only because the original event and
its source identifier are preserved.

No live Ethiopian bank or wallet connector exists in this codebase. A real one
needs a signed data-sharing agreement, the partner's API specification and
credentials, and legal review — see docs/INTEGRATIONS.md.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


class ConnectorNotConfigured(Exception):
    pass


@dataclass(frozen=True)
class ExternalAccount:
    external_account_id: str
    display_name: str
    account_mask: str


@dataclass(frozen=True)
class ExternalEvent:
    external_id: str  # the source's own immutable transaction id
    booked_on: date
    amount: Decimal  # always positive
    direction: str  # "in" or "out"
    description: str
    counterparty: str = ""
    category_hint: str = ""
    raw: dict = field(default_factory=dict)  # the event exactly as the source returned it


@dataclass(frozen=True)
class MonthlyBalance:
    month: date
    opening: Decimal
    closing: Decimal


class SourceConnector:
    key = ""
    name = ""
    is_demo = False

    def list_accounts(self, business):
        raise NotImplementedError

    def fetch_events(self, account_id, since=None):
        raise NotImplementedError

    def fetch_balances(self, account_id):
        raise NotImplementedError
