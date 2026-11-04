"""Pilot measurements computed from platform records. Anything the platform
can't observe (review time, effort, satisfaction) comes only from manual entries;
nothing here is estimated or filled in."""

import datetime as dt
import statistics
from collections import Counter
from decimal import Decimal

from django.db.models import Avg, Count
from django.utils import timezone

from core.models import AuditEvent, Evidence
from core.periods import add_months, month_start, today
from ledger.models import DataQualityFinding, Transaction
from ledger.quality import LARGE_AMOUNT
from merchants.models import Business
from passport.models import PassportSnapshot
from sharing.models import AccessReceipt

from .models import PilotFeedback, PilotMetricEntry


def compute(pilot=None, include_demo=False):
    businesses = Business.objects.all() if include_demo else Business.objects.filter(is_demo=False)
    txns = Transaction.objects.active().filter(business__in=businesses)
    since_30 = timezone.now() - dt.timedelta(days=30)
    window_start = add_months(month_start(today()), -11)

    active = businesses.filter(transactions__created_at__gte=since_30).distinct().count()
    months_per_business = []
    for business in businesses:
        months = {month_start(d) for d in txns.filter(business=business, occurred_on__gte=window_start).values_list("occurred_on", flat=True)}
        months_per_business.append(len(months))
    history = Counter("12" if m >= 12 else "6–11" if m >= 6 else "1–5" if m >= 1 else "0" for m in months_per_business)

    recent = txns.filter(occurred_on__gte=window_start)
    gross = sum(recent.values_list("amount", flat=True), Decimal("0"))
    coverage = {}
    for cls in (Evidence.SYSTEM_VERIFIED, Evidence.EVIDENCE_SUPPORTED, Evidence.SELF_REPORTED):
        amount = sum(recent.filter(evidence_class=cls).values_list("amount", flat=True), Decimal("0"))
        coverage[cls.label] = (amount / gross * 100).quantize(Decimal("0.1")) if gross else None

    large = recent.filter(amount__gte=LARGE_AMOUNT).exclude(evidence_class=Evidence.SYSTEM_VERIFIED)
    large_count = large.count()
    undocumented = large.filter(evidence_class=Evidence.SELF_REPORTED).count()

    receipts = AccessReceipt.objects.filter(business__in=businesses)
    snapshots = PassportSnapshot.objects.filter(business__in=businesses)
    findings = (DataQualityFinding.objects.filter(business__in=businesses).exclude(status=DataQualityFinding.Status.RESOLVED)
                .values("code").annotate(n=Count("id")).order_by("-n"))

    manual = {}
    feedback = {}
    if pilot is not None:
        for row in PilotMetricEntry.objects.filter(pilot=pilot).values("metric").annotate(avg=Avg("value"), n=Count("id")):
            manual[row["metric"]] = {"average": Decimal(row["avg"]).quantize(Decimal("0.1")), "entries": row["n"]}
        for row in PilotFeedback.objects.filter(pilot=pilot).values("role").annotate(avg=Avg("rating"), n=Count("id")):
            feedback[row["role"]] = {"average": f"{row['avg']:.1f}", "responses": row["n"]}

    review = manual.get("review_minutes", {}).get("average")
    baseline = manual.get("baseline_review_minutes", {}).get("average")
    return {
        "include_demo": include_demo,
        "merchants_total": businesses.count(),
        "merchants_active_30d": active,
        "history_distribution": [(k, history.get(k, 0)) for k in ("12", "6–11", "1–5", "0")],
        "median_usable_months": statistics.median(months_per_business) if months_per_business else None,
        "passports_merchant": snapshots.filter(consent__isnull=True).count(),
        "passports_shared": snapshots.filter(consent__isnull=False).count(),
        "accesses_by_channel": list(receipts.values("channel").annotate(n=Count("id")).order_by("channel")),
        "accesses_total": receipts.count(),
        "denied_attempts": AuditEvent.objects.filter(action="bank.access", outcome=AuditEvent.Outcome.DENIED, business__in=businesses).count(),
        "coverage": coverage,
        "coverage_basis": gross,
        "missing_document_rate": (Decimal(undocumented) / large_count * 100).quantize(Decimal("0.1")) if large_count else None,
        "large_records": large_count,
        "findings": list(findings),
        "manual": manual,
        "review_change": ((review - baseline) / baseline * 100).quantize(Decimal("0.1")) if review is not None and baseline else None,
        "feedback": feedback,
    }
