"""One save path for every quick way of logging (quick add, voice, pasted SMS,
Telegram, bills), so they all follow the same integrity rules as typed entries:
original preserved as a source event, approval for non-approvers, duplicate
references blocked, receipts attached as evidence."""

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from core.periods import add_months, today
from ledger import services
from ledger.models import (
    INFLOW_CATEGORIES, OUTFLOW_CATEGORIES, Category, Direction, ObligationPayment, PaymentMethod, RecurringObligation,
    SourceKind, Transaction,
)

from .models import QuickTemplate


def clean_entry(data):
    """Validate a draft from any capture channel. Returns cleaned data or raises ValidationError."""
    errors = []
    direction = data.get("direction")
    if direction not in Direction.values:
        errors.append("Choose money in or money out.")
    try:
        amount = Decimal(str(data.get("amount", "")).replace(",", "")).quantize(Decimal("0.01"))
        if amount <= 0 or amount >= Decimal("1e13"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        errors.append("Enter an amount greater than zero.")
        amount = None
    category = data.get("category")
    allowed = INFLOW_CATEGORIES if direction == Direction.INFLOW else OUTFLOW_CATEGORIES
    if category not in [c.value for c in allowed]:
        errors.append("Choose a category that matches money in / money out.")
    method = data.get("payment_method") or ""
    if method and method not in PaymentMethod.values:
        errors.append("Unknown payment method.")
    day = data.get("occurred_on") or today()
    if isinstance(day, str):
        try:
            day = dt.date.fromisoformat(day)
        except ValueError:
            errors.append("Use a valid date.")
            day = today()
    if day > today():
        errors.append("The date can't be in the future.")
    if errors:
        raise ValidationError(errors)
    return {
        "direction": direction, "category": category, "amount": amount, "currency": "ETB", "occurred_on": day,
        "payment_method": method, "description": (data.get("description") or "")[:200],
        "counterparty": (data.get("counterparty") or "")[:120], "reference": (data.get("reference") or "")[:80],
    }


@db_transaction.atomic
def log_entry(business, user, data, *, source_kind=SourceKind.MANUAL, raw=None, fingerprint=None, photo=None, template=None):
    entry = clean_entry(data)
    txn = services.record_manual(business, user, entry, source_kind=source_kind, raw=raw, fingerprint=fingerprint)
    if photo is not None:
        document = services.store_document(business, user, photo, "receipt",
                                           f"Receipt — {entry['description'] or Category(entry['category']).label} {entry['occurred_on']:%d %b}")
        services.link_document(document, user, transaction=txn)
        txn.refresh_from_db()
    if template is not None:
        QuickTemplate.objects.filter(pk=template.pk).update(uses=template.uses + 1)
    return txn


# ---------- Bills due ----------

OBLIGATION_CATEGORY = {
    RecurringObligation.Kind.RENT: Category.RENT,
    RecurringObligation.Kind.LOAN: Category.LOAN_REPAYMENT,
    RecurringObligation.Kind.SALARY: Category.WAGES,
    RecurringObligation.Kind.UTILITY: Category.UTILITIES,
    RecurringObligation.Kind.SUPPLIER: Category.STOCK,
    RecurringObligation.Kind.TAX: Category.TAXES_FEES,
    RecurringObligation.Kind.OTHER: Category.OTHER_EXPENSE,
}
LOOKBACK_DAYS = 62
LOOKAHEAD_DAYS = 7
MATCH_DAYS = 15


def instalments(obligation, until):
    """Due dates from the start date, stepping by the frequency."""
    step = RecurringObligation.FREQUENCY_MONTHS[obligation.frequency]
    day, i = obligation.start_date, 0
    while day <= until and (obligation.end_date is None or day <= obligation.end_date):
        yield day
        i += 1
        day = add_months(obligation.start_date.replace(day=1), i * step).replace(
            day=min(obligation.start_date.day, 28))


def bills_due(business, on=None):
    """Unpaid instalments from the last ~2 months and the coming week."""
    on = on or today()
    earliest, latest = on - dt.timedelta(days=LOOKBACK_DAYS), on + dt.timedelta(days=LOOKAHEAD_DAYS)
    paid = set(ObligationPayment.objects.filter(obligation__business=business).values_list("obligation_id", "due_date"))
    # A matching expense recorded the ordinary way also counts as paid (same category
    # and amount within MATCH_DAYS of the due date), so bills aren't nagged twice.
    recorded = list(Transaction.objects.filter(
        business=business, direction=Direction.OUTFLOW, status__in=[Transaction.Status.ACTIVE, Transaction.Status.PENDING],
        occurred_on__gte=earliest - dt.timedelta(days=MATCH_DAYS)).values_list("category", "amount", "occurred_on"))
    due = []
    for obligation in RecurringObligation.objects.filter(business=business):
        category = OBLIGATION_CATEGORY[obligation.kind]
        for day in instalments(obligation, latest):
            if day < earliest or (obligation.pk, day) in paid:
                continue
            if any(c == category and a == obligation.amount and abs((d - day).days) <= MATCH_DAYS for c, a, d in recorded):
                continue
            due.append({"obligation": obligation, "due_date": day, "overdue": day < on, "days": (day - on).days})
    return sorted(due, key=lambda d: d["due_date"])


@db_transaction.atomic
def mark_bill_paid(obligation, due_date, user, payment_method="", paid_on=None, amount=None):
    if due_date not in set(instalments(obligation, due_date)):
        raise ValidationError("That isn't a due date for this obligation.")
    if ObligationPayment.objects.filter(obligation=obligation, due_date=due_date).exists():
        raise ValidationError("This instalment is already marked paid.")
    paid_on = min(paid_on or today(), today())
    txn = log_entry(obligation.business, user, {
        "direction": Direction.OUTFLOW, "category": OBLIGATION_CATEGORY[obligation.kind], "amount": amount or obligation.amount,
        "occurred_on": paid_on, "payment_method": payment_method, "description": f"{obligation.name} — due {due_date:%d %b %Y}",
        "counterparty": obligation.counterparty,
    }, source_kind=SourceKind.BILL)
    ObligationPayment.objects.create(obligation=obligation, due_date=due_date, transaction=txn, recorded_by=user)
    return txn
