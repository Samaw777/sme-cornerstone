"""Credit-relevant metrics: the ratios a lender looks at, calculated from recorded
data with stated methods. They are indicators, not a credit score, rating,
grade or recommendation; there is deliberately no single composite number.

Every metric says whether it could be calculated, and if not, why. Inputs that
aren't recorded (cash held elsewhere, debts outside the platform) are never
assumed to be zero."""

import statistics
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from core.models import Evidence
from core.periods import month_start
from ledger.models import (
    Category, ConnectedSource, DataQualityFinding, Document, EvidenceLink, Invoice, RecurringObligation, StatementBalance,
)
from ledger.quality import LARGE_AMOUNT

ZERO = Decimal("0")
MIN_MONTHS = 3
MIN_MONTHS_STABILITY = 6
EVIDENCE_WEIGHTS = {
    "system_verified": Decimal("1.0"),
    "evidence_supported_reviewed": Decimal("0.8"),
    "evidence_supported": Decimal("0.6"),
    "self_reported": Decimal("0.2"),
}
NOTE = ("These are calculated indicators, not a credit score, rating or recommendation. Each one shows its "
        "method and inputs; the receiving institution decides how, or whether, to use them.")

METHODS = [
    ("Operating cash margin", f"(Revenue − operating expenses) ÷ revenue across complete months with records. Needs {MIN_MONTHS} such months."),
    ("Months with operating surplus", f"Complete months with records where revenue exceeded operating expenses, out of all such months. Needs {MIN_MONTHS}."),
    ("Revenue stability", f"100 × (1 − coefficient of variation of monthly revenue), floored at 0, across complete months with records. Needs {MIN_MONTHS_STABILITY}."),
    ("Debt service coverage", "Average monthly (revenue − operating expenses) ÷ average monthly loan repayments recorded as transactions; if none are recorded, ÷ the monthly equivalent of recorded loan obligations. Debts not recorded on the platform are not included."),
    ("Receivable days", "Outstanding recorded invoices ÷ average monthly revenue × 30, with the overdue share. Only invoices recorded on the platform."),
    ("Cash buffer", "Latest statement closing balance of connected accounts ÷ average monthly operating expenses. Only available with a connected source; cash held elsewhere is unknown."),
    ("Evidence strength (0–100)", "Value-weighted evidence of counted records: system-verified 1.0, evidence-supported with a platform-reviewed document 0.8, evidence-supported with file checks only 0.6, self-reported 0.2. Demo sources are included but reported separately."),
    ("Data completeness (0–100)", "Average of the available parts: share of months since the first record that have records; share of non-verified records of ETB 10,000+ with a document; share of connected-account months that reconcile; share of open indicators the business has explained."),
]


def _q(value, places="0.01"):
    return str(Decimal(value).quantize(Decimal(places), rounding=ROUND_HALF_UP))


MONEY_INPUTS = {"revenue", "operating_expenses", "average_monthly_surplus", "average_monthly_repayments",
                "monthly_loan_obligations", "outstanding", "overdue", "average_monthly_revenue", "closing_balance",
                "average_monthly_operating_expenses"}


def _metric(key, label, *, value=None, unit="", reason=None, inputs=None, basis=""):
    """inputs become [{key, value, unit}] so every consumer knows which are ETB amounts."""
    rows = [{"key": k, "value": str(v), "unit": "ETB" if k in MONEY_INPUTS else ""} for k, v in (inputs or {}).items()]
    return {"key": key, "label": label, "available": value is not None, "value": value, "unit": unit,
            "reason": reason, "inputs": rows, "basis": basis}


def compute(business, txns, monthly, report_day, start, end):
    complete = [m for m in monthly if m["state"] == "recorded" and not m["in_progress"]]
    n = len(complete)
    revenue = [Decimal(m["revenue"]) for m in complete]
    opex = [Decimal(m["operating_expenses"]) for m in complete]
    total_rev, total_opex = sum(revenue, ZERO), sum(opex, ZERO)
    avg_rev = total_rev / n if n else None
    avg_opex = total_opex / n if n else None
    avg_surplus = (total_rev - total_opex) / n if n else None
    too_few = f"Needs at least {MIN_MONTHS} complete months with records; {n} available."
    complete_keys = {m["month"] for m in complete}
    metrics = []

    # Operating cash margin
    if n < MIN_MONTHS or not total_rev:
        metrics.append(_metric("operating_margin", "Operating cash margin", unit="%",
                               reason=too_few if n < MIN_MONTHS else "No revenue recorded in complete months."))
    else:
        metrics.append(_metric("operating_margin", "Operating cash margin", value=_q((total_rev - total_opex) / total_rev * 100, "0.1"),
                               unit="%", inputs={"revenue": _q(total_rev), "operating_expenses": _q(total_opex), "months": n},
                               basis="Cash basis; not accounting profit."))

    # Months with operating surplus
    if n < MIN_MONTHS:
        metrics.append(_metric("surplus_months", "Months with operating surplus", reason=too_few))
    else:
        positive = sum(1 for r, o in zip(revenue, opex) if r > o)
        metrics.append(_metric("surplus_months", "Months with operating surplus", value=f"{positive} of {n}",
                               inputs={"months_with_surplus": positive, "months": n}))

    # Revenue stability
    if n < MIN_MONTHS_STABILITY or not total_rev:
        metrics.append(_metric("revenue_stability", "Revenue stability", unit="/100",
                               reason=f"Needs at least {MIN_MONTHS_STABILITY} complete months with revenue; {n} available."))
    else:
        values = [float(r) for r in revenue]
        cv = statistics.pstdev(values) / statistics.mean(values)
        metrics.append(_metric("revenue_stability", "Revenue stability", value=str(max(0, round(100 * (1 - cv)))), unit="/100",
                               inputs={"coefficient_of_variation": f"{cv:.2f}", "months": n}))

    # Debt service coverage
    repayments = sum((t.amount for t in txns if t.category == Category.LOAN_REPAYMENT
                      and month_start(t.occurred_on).strftime("%Y-%m") in complete_keys), ZERO)
    loan_obligations = [o for o in RecurringObligation.objects.filter(business=business, kind=RecurringObligation.Kind.LOAN)
                        if o.is_current(report_day)]
    if n < MIN_MONTHS:
        metrics.append(_metric("debt_service_coverage", "Debt service coverage", unit="×", reason=too_few))
    elif repayments:
        service = repayments / n
        metrics.append(_metric("debt_service_coverage", "Debt service coverage", value=_q(avg_surplus / service), unit="×",
                               inputs={"average_monthly_surplus": _q(avg_surplus), "average_monthly_repayments": _q(service)},
                               basis="Loan repayments recorded as transactions."))
    elif loan_obligations:
        service = sum((o.monthly_equivalent for o in loan_obligations), ZERO)
        metrics.append(_metric("debt_service_coverage", "Debt service coverage", value=_q(avg_surplus / service), unit="×",
                               inputs={"average_monthly_surplus": _q(avg_surplus), "monthly_loan_obligations": _q(service)},
                               basis="Recorded loan obligations (no repayment transactions recorded)."))
    else:
        metrics.append(_metric("debt_service_coverage", "Debt service coverage", unit="×",
                               reason="No loan repayments or loan obligations are recorded, so coverage can't be calculated. That does not mean there are none."))

    # Receivable days
    open_invoices = [i for i in Invoice.objects.filter(business=business, status=Invoice.Status.OPEN) if i.outstanding > 0]
    outstanding = sum((i.outstanding for i in open_invoices), ZERO)
    overdue = sum((i.outstanding for i in open_invoices if i.due_date < report_day), ZERO)
    if not avg_rev:
        metrics.append(_metric("receivable_days", "Receivable days", unit="days", reason="Needs revenue in at least one complete month."))
    else:
        metrics.append(_metric("receivable_days", "Receivable days", value=str(round(outstanding / avg_rev * 30)), unit="days",
                               inputs={"outstanding": _q(outstanding), "overdue": _q(overdue),
                                       "overdue_share_percent": _q(overdue / outstanding * 100, "0.1") if outstanding else "0.0",
                                       "average_monthly_revenue": _q(avg_rev), "open_invoices": len(open_invoices)},
                               basis="Invoices recorded on the platform only."))

    # Cash buffer
    sources = list(ConnectedSource.objects.filter(business=business, is_active=True))
    latest = [StatementBalance.objects.filter(source=s, month__lte=end).order_by("-month").first() for s in sources]
    latest = [b for b in latest if b]
    if not latest:
        metrics.append(_metric("cash_buffer", "Cash buffer", unit="months",
                               reason="No connected account with statement balances. Cash held elsewhere is unknown."))
    elif not avg_opex:
        metrics.append(_metric("cash_buffer", "Cash buffer", unit="months", reason="Needs operating expenses in at least one complete month."))
    else:
        closing = sum((b.closing for b in latest), ZERO)
        demo = any(s.is_demo for s in sources)
        metrics.append(_metric("cash_buffer", "Cash buffer", value=_q(closing / avg_opex, "0.1"), unit="months",
                               inputs={"closing_balance": _q(closing), "average_monthly_operating_expenses": _q(avg_opex),
                                       "as_of_month": max(b.month for b in latest).strftime("%Y-%m")},
                               basis="Connected accounts only" + (" — DEMO SOURCE, not real verification." if demo else ".")))

    scores = [evidence_strength(txns), data_completeness(business, txns, monthly, start, end)]
    for item in metrics + scores:
        item["summary"] = _summary(item)
    for score in scores:
        score["band"] = band(score)
    return {"note": NOTE, "metrics": metrics, "scores": scores}


def _etb(value):
    return f"ETB {Decimal(value):,.0f}"


def _summary(m):
    """One short line for the report tile, built only from the metric's own inputs."""
    if not m["available"]:
        return m["reason"]
    i = {row["key"]: row["value"] for row in m["inputs"]}
    key = m["key"]
    if key == "operating_margin":
        return f"{_etb(i['revenue'])} revenue over {i['months']} months"
    if key == "surplus_months":
        return "Complete months where revenue beat running costs"
    if key == "revenue_stability":
        return f"Variation {i['coefficient_of_variation']} across {i['months']} months"
    if key == "debt_service_coverage":
        service = i.get("average_monthly_repayments") or i.get("monthly_loan_obligations")
        return f"{_etb(i['average_monthly_surplus'])}/mo surplus vs {_etb(service)}/mo debt"
    if key == "receivable_days":
        return f"{_etb(i['outstanding'])} outstanding · {i['overdue_share_percent']}% overdue"
    if key == "cash_buffer":
        return f"{_etb(i['closing_balance'])} balance" + (" · demo source" if "DEMO" in m["basis"] else "")
    if key == "evidence_strength":
        return f"{i['system_verified']} system-verified · {i['self_reported']} self-reported"
    if key == "data_completeness":
        return m["basis"]
    return ""


def band(score):
    """Describes the records behind the report (how well evidenced, how complete),
    never the business's creditworthiness."""
    if not score["available"]:
        return None
    value = int(score["value"])
    return "Strong" if value >= 70 else "Moderate" if value >= 40 else "Limited"


def evidence_strength(txns):
    gross = sum((t.amount for t in txns), ZERO)
    if not gross:
        return _metric("evidence_strength", "Evidence strength", unit="/100", reason="No records in the period.")
    supported = [t.pk for t in txns if t.evidence_class == Evidence.EVIDENCE_SUPPORTED]
    reviewed = set(EvidenceLink.objects.filter(transaction_id__in=supported, document__validation_status=Document.Validation.REVIEWED)
                   .values_list("transaction_id", flat=True))
    shares = defaultdict(Decimal)
    for t in txns:
        if t.evidence_class == Evidence.EVIDENCE_SUPPORTED:
            shares["evidence_supported_reviewed" if t.pk in reviewed else "evidence_supported"] += t.amount
        else:
            shares[t.evidence_class] += t.amount
    score = sum((shares[k] * w for k, w in EVIDENCE_WEIGHTS.items()), ZERO) / gross * 100
    demo = sum((t.amount for t in txns if t.is_demo), ZERO)
    return _metric("evidence_strength", "Evidence strength", value=str(round(score)), unit="/100",
                   inputs={k: _q(shares[k] / gross * 100, "0.1") + "%" for k in EVIDENCE_WEIGHTS} | {"demo_share": _q(demo / gross * 100, "0.1") + "%"},
                   basis="Includes demo data — not real verification." if demo else "")


def data_completeness(business, txns, monthly, start, end):
    parts = {}
    tracked = [m for m in monthly if m["state"] != "before_history" and not m["in_progress"]]
    if tracked:
        parts["months_with_records"] = sum(1 for m in tracked if m["state"] == "recorded") / len(tracked) * 100
    large = [t for t in txns if t.amount >= LARGE_AMOUNT and t.evidence_class != Evidence.SYSTEM_VERIFIED]
    if large:
        parts["large_records_documented"] = sum(1 for t in large if t.evidence_class == Evidence.EVIDENCE_SUPPORTED) / len(large) * 100
    balance_months = StatementBalance.objects.filter(source__business=business, month__gte=month_start(start), month__lte=end).count()
    if balance_months:
        gaps = DataQualityFinding.objects.filter(business=business, code=DataQualityFinding.Code.RECONCILIATION_GAP,
                                                 month__gte=month_start(start), month__lte=end).exclude(status=DataQualityFinding.Status.RESOLVED).count()
        parts["months_reconciled"] = max(balance_months - gaps, 0) / balance_months * 100
    findings = DataQualityFinding.objects.filter(business=business, month__gte=month_start(start), month__lte=end).exclude(
        status=DataQualityFinding.Status.RESOLVED)
    total = findings.count()
    parts["indicators_explained"] = (findings.filter(status=DataQualityFinding.Status.ACKNOWLEDGED).count() / total * 100) if total else 100
    if not tracked:
        return _metric("data_completeness", "Data completeness", unit="/100", reason="No complete months with records yet.")
    score = sum(parts.values()) / len(parts)
    return _metric("data_completeness", "Data completeness", value=str(round(score)), unit="/100",
                   inputs={k: f"{v:.0f}%" for k, v in parts.items()},
                   basis=f"Average of {len(parts)} available part(s).")
