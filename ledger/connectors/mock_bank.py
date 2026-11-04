"""A demo bank-account connector that returns deterministic synthetic data.

Everything it produces is flagged is_demo and labelled "Demo source" in the
interface, passport, PDF and API. It does not contact any bank."""

import datetime as dt
import hashlib
import random
from decimal import Decimal

from core.periods import add_months, month_end, month_start, today

from .base import ExternalAccount, ExternalEvent, MonthlyBalance, SourceConnector

HISTORY_MONTHS = 14


class MockBankConnector(SourceConnector):
    key = "mock_bank"
    name = "Demo Bank (mock connector)"
    is_demo = True

    def list_accounts(self, business):
        account_id = "DEMO-" + hashlib.sha256(str(business.public_id).encode()).hexdigest()[:10].upper()
        return [ExternalAccount(account_id, "Demo Bank current account", "•••• " + account_id[-4:])]

    def _rng(self, account_id):
        return random.Random(account_id)

    def _months(self):
        end = month_start(today())
        return [add_months(end, -i) for i in range(HISTORY_MONTHS - 1, -1, -1)]

    def fetch_events(self, account_id, since=None):
        rng = self._rng(account_id)
        events = []
        counter = 0
        for month in self._months():
            last_day = min(month_end(month), today())
            # Coffee-style seasonality: busier from October to January.
            season = 1.35 if month.month in (10, 11, 12, 1) else 1.0
            for _ in range(rng.randint(6, 9)):
                counter += 1
                day = month + dt.timedelta(days=rng.randint(0, (last_day - month).days))
                amount = Decimal(rng.randint(18, 60) * 500) * Decimal(str(season))
                events.append(self._event(account_id, counter, day, amount.quantize(Decimal("1")), "in",
                                          "Deposit — wholesale customer", rng.choice(["Wholesale buyer A", "Hotel client B", "Café chain C"]), "sales"))
            for desc, low, high, hint in (("Rent transfer", 18000, 18000, "rent"), ("Salary transfer", 24000, 26000, "wages"),
                                          ("Supplier payment", 30000, 70000, "stock"), ("Electricity bill", 1800, 3200, "utilities")):
                counter += 1
                day = month + dt.timedelta(days=min(rng.randint(1, 27), (last_day - month).days))
                amount = Decimal(rng.randint(low // 100, high // 100) * 100)
                events.append(self._event(account_id, counter, day, amount, "out", desc, desc.split()[0] + " payee", hint))
        if since:
            events = [e for e in events if e.booked_on >= since]
        return events

    def _event(self, account_id, n, day, amount, direction, description, counterparty, hint):
        external_id = f"{account_id}-{n:05d}"
        raw = {"id": external_id, "bookingDate": day.isoformat(), "amount": str(amount), "currency": "ETB",
               "creditDebit": "CRDT" if direction == "in" else "DBIT", "narrative": description, "party": counterparty,
               "demo": True}
        return ExternalEvent(external_id, day, amount, direction, description, counterparty, hint, raw)

    def fetch_balances(self, account_id):
        rng = self._rng(account_id + "-bal")
        balance = Decimal(rng.randint(40, 90) * 1000)
        events = self.fetch_events(account_id)
        balances = []
        for i, month in enumerate(self._months()):
            net = sum((e.amount if e.direction == "in" else -e.amount) for e in events if month_start(e.booked_on) == month)
            if i == 5:
                # Demo of a reconciliation difference: a service charge that appears
                # on the statement but was never delivered in the event feed.
                net -= Decimal("350")
            opening = balance
            balance = opening + net
            balances.append(MonthlyBalance(month, opening, balance))
        return balances
