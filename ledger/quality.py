"""Data-quality checks. Findings are neutral observations to help the merchant
and any authorised reader understand the records. They are not evidence of
fraud, error or poor creditworthiness, and the wording keeps it that way."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

from core.models import Evidence
from core.periods import add_months, label, month_start, months_between, today

from .models import (
    NON_OPERATING_INFLOWS, OPERATING_EXPENSE_CATEGORIES, REVENUE_CATEGORIES, Annotation, ConnectedSource,
    DataQualityFinding, Transaction,
)

CALCULATION_VERSION = "2026.3"
LARGE_AMOUNT = Decimal("10000")
UNUSUAL_CHANGE_RATIO = Decimal("0.5")
UNUSUAL_MIN_BASE = Decimal("1000")

Code = DataQualityFinding.Code


def fmt(amount):
    return f"ETB {amount:,.2f}"


def monthly_totals(transactions):
    revenue, expenses, count = defaultdict(Decimal), defaultdict(Decimal), defaultdict(int)
    for t in transactions:
        month = month_start(t.occurred_on)
        count[month] += 1
        if t.category in REVENUE_CATEGORIES:
            revenue[month] += t.amount
        elif t.category in OPERATING_EXPENSE_CATEGORIES:
            expenses[month] += t.amount
    return revenue, expenses, count


def detect(business):
    """Return the findings that currently apply, as dicts keyed by (code, key)."""
    found = {}
    now_day = today()
    txns = list(Transaction.objects.active().filter(business=business))

    def add(code, key, month, title, detail):
        found[(code, key)] = {"month": month, "title": title, "detail": detail}

    if not txns:
        return found

    revenue, expenses, count = monthly_totals(txns)
    first = min(t.occurred_on for t in txns)
    last_complete = add_months(month_start(now_day), -1)
    for month in months_between(first, last_complete) if first <= last_complete else []:
        if not count.get(month):
            add(Code.MISSING_MONTH, month.strftime("%Y-%m"), month, f"No records for {label(month)}",
                "No income or expenses are recorded for this month. Totals for it are shown as unknown, not zero.")

    months = sorted(count)
    for line_name, series in (("Revenue", revenue), ("Operating expenses", expenses)):
        for month in months:
            prior = [add_months(month, -i) for i in (1, 2, 3)]
            if not all(count.get(p) for p in prior):
                continue
            base = sum(series.get(p, Decimal("0")) for p in prior) / 3
            if base < UNUSUAL_MIN_BASE:
                continue
            change = (series.get(month, Decimal("0")) - base) / base
            if abs(change) >= UNUSUAL_CHANGE_RATIO:
                direction = "higher" if change > 0 else "lower"
                add(Code.UNUSUAL_CHANGE, f"{line_name.lower()}:{month:%Y-%m}", month,
                    f"{line_name} in {label(month)} {abs(change) * 100:.0f}% {direction} than the prior 3-month average",
                    "This may reflect seasonality, a one-off event, or incomplete records for one of the months.")

    groups = defaultdict(list)
    for t in txns:
        groups[(t.occurred_on, t.amount, t.direction)].append(t)
    for (day, amount, _), items in groups.items():
        if len(items) < 2:
            continue
        kinds = {t.source_kind for t in items}
        descriptions = {" ".join(t.description.lower().split()) for t in items}
        if len(kinds) > 1 or len(descriptions) == 1:
            key = "dup:" + ",".join(sorted(str(t.pk) for t in items))
            add(Code.POSSIBLE_DUPLICATE, key[:80], month_start(day), f"{len(items)} similar records on {day:%d %b %Y}",
                f"{len(items)} records of {fmt(amount)} on the same day from {', '.join(sorted(kinds))}. "
                "They may be separate transactions or the same one recorded twice.")

    for source in ConnectedSource.objects.filter(business=business):
        by_month = defaultdict(Decimal)
        for t in txns:
            if t.connected_source_id == source.pk:
                by_month[month_start(t.occurred_on)] += t.signed_amount
        for balance in source.balances.all():
            expected = balance.closing - balance.opening
            gap = expected - by_month.get(balance.month, Decimal("0"))
            if abs(gap) >= Decimal("0.01"):
                add(Code.RECONCILIATION_GAP, f"{source.pk}:{balance.month:%Y-%m}", balance.month,
                    f"Statement and records differ by {fmt(abs(gap))} in {label(balance.month)}",
                    f"The {source.display_name} statement shows a net movement of {fmt(expected)}, while synced records "
                    f"total {fmt(by_month.get(balance.month, Decimal('0')))}. An event may not have been delivered by the source.")

    undocumented = defaultdict(list)
    for t in txns:
        if t.amount >= LARGE_AMOUNT and t.evidence_class == Evidence.SELF_REPORTED and t.category not in NON_OPERATING_INFLOWS:
            undocumented[month_start(t.occurred_on)].append(t)
    for month, items in undocumented.items():
        add(Code.MISSING_DOCUMENT, month.strftime("%Y-%m"), month,
            f"{len(items)} record(s) of {fmt(LARGE_AMOUNT)} or more without a document in {label(month)}",
            "Linking a receipt or invoice would make these records evidence-supported.")

    disputes = Annotation.objects.filter(business=business, kind=Annotation.Kind.DISPUTE, is_open=True,
                                         transaction__status=Transaction.Status.ACTIVE).select_related("transaction")
    for annotation in disputes:
        t = annotation.transaction
        add(Code.OPEN_DISPUTE, f"dispute:{annotation.pk}", month_start(t.occurred_on),
            f"Merchant disputes a record of {fmt(t.amount)} on {t.occurred_on:%d %b %Y}",
            f"Merchant note: {annotation.text[:300]}")

    for t in txns:
        if t.occurred_on > now_day:
            add(Code.FUTURE_DATED, f"future:{t.pk}", month_start(t.occurred_on), f"Record dated {t.occurred_on:%d %b %Y} is in the future",
                "It is counted in its own month like any other record; check whether the date is right.")
    return found


def refresh(business):
    """Bring stored findings in line with what's detected now."""
    current = detect(business)
    stored = {(f.code, f.key): f for f in DataQualityFinding.objects.filter(business=business)}
    for (code, key), values in current.items():
        finding = stored.get((code, key))
        if finding is None:
            DataQualityFinding.objects.create(business=business, code=code, key=key, calculation_version=CALCULATION_VERSION, **values)
        else:
            for field, value in values.items():
                setattr(finding, field, value)
            if finding.status == DataQualityFinding.Status.RESOLVED:
                finding.status = DataQualityFinding.Status.OPEN
            finding.calculation_version = CALCULATION_VERSION
            finding.save()
    for key, finding in stored.items():
        if key not in current and finding.status != DataQualityFinding.Status.RESOLVED:
            finding.status = DataQualityFinding.Status.RESOLVED
            finding.save(update_fields=["status", "last_detected_at"])
    return DataQualityFinding.objects.filter(business=business).exclude(status=DataQualityFinding.Status.RESOLVED)


def open_findings(business, start=None, end=None):
    qs = DataQualityFinding.objects.filter(business=business).exclude(status=DataQualityFinding.Status.RESOLVED)
    if start and end:
        qs = qs.filter(Q(month__isnull=True) | Q(month__gte=month_start(start), month__lte=end))
    return qs

