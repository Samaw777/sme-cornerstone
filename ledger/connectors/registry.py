from django.db import transaction as db_transaction
from django.utils import timezone

from core import audit

from ..models import Category, ConnectedSource, SourceEvent, SourceKind, StatementBalance, Transaction
from .mock_bank import MockBankConnector
from .partner_bank import PartnerBankConnector

CONNECTORS = {c.key: c for c in (MockBankConnector(), PartnerBankConnector())}


def get(key):
    return CONNECTORS[key]


def connect(business, user, key):
    connector = get(key)
    account = connector.list_accounts(business)[0]
    source, created = ConnectedSource.objects.get_or_create(
        business=business, connector_key=key, external_account_id=account.external_account_id,
        defaults={"display_name": account.display_name, "account_mask": account.account_mask,
                  "is_demo": connector.is_demo, "connected_by": user},
    )
    if created:
        audit.record("source.connected", user=user, business=business, source=source.pk, connector=key)
    return source


@db_transaction.atomic
def sync(source, user=None):
    """Pull events and balances. Each external id is stored once, so syncing
    again never duplicates a transaction."""
    connector = get(source.connector_key)
    known = set(source.events.values_list("external_id", flat=True))
    added = 0
    for event in connector.fetch_events(source.external_account_id):
        if event.external_id in known:
            continue
        stored = SourceEvent.objects.create(
            business=source.business, source_kind=SourceKind.CONNECTOR, connected_source=source,
            external_id=event.external_id, payload=event.raw, received_by=user,
        )
        category = event.category_hint if event.category_hint in Category.values else (
            Category.OTHER_INCOME if event.direction == "in" else Category.OTHER_EXPENSE)
        txn = Transaction.objects.create(
            business=source.business, source_kind=SourceKind.CONNECTOR, source_event=stored, connected_source=source,
            direction=event.direction, category=category, amount=event.amount, occurred_on=event.booked_on,
            description=event.description, counterparty=event.counterparty, reference=event.external_id,
            is_demo=source.is_demo, created_by=user,
        )
        txn.refresh_evidence_class()
        added += 1
    for balance in connector.fetch_balances(source.external_account_id):
        StatementBalance.objects.update_or_create(
            source=source, month=balance.month, defaults={"opening": balance.opening, "closing": balance.closing}
        )
    source.last_synced_at = timezone.now()
    source.save(update_fields=["last_synced_at"])
    audit.record("source.synced", user=user, business=source.business, source=source.pk, added=added)
    return added
