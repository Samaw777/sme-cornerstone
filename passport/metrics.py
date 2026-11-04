"""Business Financial Passport calculations (method version in CALCULATION_VERSION).

Rules that every figure follows:
* Only active records count. Superseded and reversed originals are excluded,
  so a correction never double counts.
* A month with no records is unknown (None), never zero.
* Cash movement (all money in minus all money out) is reported separately from
  revenue less operating expenses, and neither is accounting profit.
* Evidence coverage is a share of recorded transaction value; derived metrics
  are never counted as additional evidence.
"""

import hashlib
import json
import statistics
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from core.models import EVIDENCE_EXPLANATIONS, TRANSACTION_EVIDENCE_CLASSES, Evidence
from core.periods import label, month_end, month_start, months_between, today
from ledger import quality
from ledger.models import (
    NON_OPERATING_INFLOWS, NON_OPERATING_OUTFLOWS, OPERATING_EXPENSE_CATEGORIES, REVENUE_CATEGORIES,
    ConnectedSource, Direction, Document, Invoice, RecurringObligation, Transaction,
)
from merchants.models import IdentityStatus
from sharing.models import DataCategory

from . import credit

CALCULATION_VERSION = quality.CALCULATION_VERSION
DISCLAIMER = "This report provides financial evidence and is not a credit decision."
MIN_MONTHS_FOR_TREND = 6
MIN_MONTHS_FOR_SEASONALITY = 12
LIMITED_HISTORY_MONTHS = 6
ZERO = Decimal("0")
ALL_CATEGORIES = [c.value for c in DataCategory]

METHODS = [
    ("Records counted", "Active transactions in ETB dated within the reporting period. Superseded and reversed records are kept for audit but excluded from every total."),
    ("Revenue", "Sum of money-in records categorised as sales, service income or other operating income."),
    ("Operating expenses", "Sum of money-out records in operating categories (stock, rent, wages, utilities, transport, telecom, taxes and fees, maintenance, other)."),
    ("Revenue less operating expenses", "Revenue minus operating expenses on a cash basis. It is not accounting profit: there are no accruals, depreciation, inventory adjustments or tax provisions."),
    ("Cash inflows / outflows", "All money in / all money out, including loans received, owner capital, loan repayments, owner drawings and equipment purchases."),
    ("Net cash movement", "Cash inflows minus cash outflows for the month. A month with no records is shown as unknown, not zero."),
    ("Evidence coverage", "Share of the gross value of counted records (money in plus money out) in each evidence class. The denominator is stated with each figure. Derived metrics are not counted as evidence."),
    ("Revenue trend", f"Average monthly revenue of the latest 3 months versus the 3 months before them. Shown only when each of the last {MIN_MONTHS_FOR_TREND} months in the period has records."),
    ("Seasonality", f"Highest and lowest revenue months. Shown only with at least {MIN_MONTHS_FOR_SEASONALITY} months with records; with fewer than 24 it reflects a single cycle."),
    ("Revenue variability", "Coefficient of variation (standard deviation ÷ mean) of monthly revenue across months with records. Shown with at least 3 such months."),
    ("Receivables", "Open invoice amounts less recorded payments, as of the report date, aged by days past the due date."),
    ("Obligations", "Recurring obligations recorded by the business that are current at the report date, converted to a monthly equivalent. Obligations outside the platform are not discovered."),
    ("Data quality", "Neutral checks for missing months, unusual changes (≥50% vs the prior 3-month average), possible duplicates, statement reconciliation differences, large undocumented records and open disputes."),
    *credit.METHODS,
    ("Shared reports", "Reports shared with a bank or MFI contain aggregates and metrics only: data-quality indicators are counted by type and month, and obligations are totalled by type, so no individual record, counterparty or note about a specific record is disclosed."),
]


def money(value):
    return None if value is None else str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct(part, whole):
    if not whole:
        return None
    return str((Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def compute(business, start, end, categories=None, *, as_of=None, sharing=None, audience="merchant"):
    """Build the passport dictionary for one business and period.

    audience="institution" is used for everything a bank/MFI receives: it keeps
    aggregates and metrics only, with no record-level detail."""
    for_institution = audience == "institution"
    categories = [c for c in (categories or ALL_CATEGORIES) if c in ALL_CATEGORIES]
    as_of = as_of or timezone.now()
    report_day = today()
    start = month_start(start)
    end = min(month_end(end), month_end(report_day))
    quality.refresh(business)

    all_active = Transaction.objects.active().filter(business=business)
    first_record = all_active.order_by("occurred_on").values_list("occurred_on", flat=True).first()
    txns = list(all_active.filter(occurred_on__gte=start, occurred_on__lte=end))
    months = months_between(start, end) if start <= end else []

    by_month = defaultdict(list)
    for t in txns:
        by_month[month_start(t.occurred_on)].append(t)

    monthly = []
    for month in months:
        items = by_month.get(month, [])
        if items:
            rev = sum((t.amount for t in items if t.category in REVENUE_CATEGORIES), ZERO)
            opex = sum((t.amount for t in items if t.category in OPERATING_EXPENSE_CATEGORIES), ZERO)
            other_in = sum((t.amount for t in items if t.category in NON_OPERATING_INFLOWS), ZERO)
            other_out = sum((t.amount for t in items if t.category in NON_OPERATING_OUTFLOWS), ZERO)
            inflow = rev + other_in
            outflow = opex + other_out
            row = {"revenue": money(rev), "operating_expenses": money(opex), "revenue_less_operating_expenses": money(rev - opex),
                   "other_inflows": money(other_in), "other_outflows": money(other_out), "inflows": money(inflow),
                   "outflows": money(outflow), "net_cash_movement": money(inflow - outflow), "transactions": len(items)}
            state = "recorded"
        else:
            row = dict.fromkeys(["revenue", "operating_expenses", "revenue_less_operating_expenses", "other_inflows",
                                 "other_outflows", "inflows", "outflows", "net_cash_movement"])
            row["transactions"] = 0
            state = "before_history" if first_record is None or month < month_start(first_record) else "no_records"
        row.update({"month": month.strftime("%Y-%m"), "label": label(month), "state": state,
                    "in_progress": month == month_start(report_day)})
        monthly.append(row)

    recorded = [m for m in monthly if m["state"] == "recorded"]
    missing = [m["label"] for m in monthly if m["state"] == "no_records"]
    unknown_before = [m["label"] for m in monthly if m["state"] == "before_history"]

    def total(key):
        return money(sum((Decimal(m[key]) for m in recorded), ZERO)) if recorded else None

    history_months = len(months_between(first_record, report_day)) if first_record else 0
    data = {
        "disclaimer": DISCLAIMER,
        "calculation_version": CALCULATION_VERSION,
        "generated_at": as_of.isoformat(),
        "currency": "ETB",
        "period": {"start": start.isoformat(), "end": end.isoformat(), "label": f"{label(start)} – {label(end)}",
                   "months": len(months), "months_with_records": len(recorded)},
        "history": {
            "first_record": first_record.isoformat() if first_record else None,
            "available_months": history_months,
            "limited": history_months < LIMITED_HISTORY_MONTHS,
        },
        "sections": categories,
        "includes_demo_data": any(t.is_demo for t in txns),
        "sharing": sharing,
        "audience": "institution" if for_institution else "merchant",
    }

    if DataCategory.PROFILE in categories:
        data["profile"] = profile(business)

    if DataCategory.FINANCIAL_SUMMARY in categories:
        data["monthly"] = monthly
        data["totals"] = {key: total(key) for key in ("revenue", "operating_expenses", "revenue_less_operating_expenses",
                                                      "inflows", "outflows", "net_cash_movement")}
        data["totals"]["basis"] = f"{len(recorded)} of {len(months)} months have records; months without records are excluded, not counted as zero."
        data["trend"] = trend(monthly)
        data["seasonality"] = seasonality(recorded)
        data["variability"] = variability(recorded)

    if DataCategory.VERIFICATION_SUMMARY in categories:
        data["evidence"] = evidence(business, txns)

    if DataCategory.RECEIVABLES_OBLIGATIONS in categories:
        data["receivables"] = receivables(business, report_day)
        data["obligations"] = obligations(business, report_day, aggregate=for_institution)

    if DataCategory.DATA_QUALITY in categories:
        data["data_quality"] = data_quality(business, start, end, missing, unknown_before, aggregate=for_institution)

    if DataCategory.CREDIT_METRICS in categories:
        data["credit_metrics"] = credit.compute(business, txns, monthly, report_day, start, end)

    data["methods"] = [{"metric": m, "method": d} for m, d in METHODS]
    data["limitations"] = limitations(data, missing, txns, business)
    return data


def profile(business):
    def identity(value, status):
        return {"value": value or None, "status": status, "status_label": str(IdentityStatus(status).label)}

    return {
        "name": business.name,
        "legal_name": business.legal_name or None,
        "sector": business.get_sector_display(),
        "location": ", ".join(p for p in [business.sub_city, business.city, business.get_region_display()] if p),
        "operating_since": business.operating_start_date.isoformat(),
        "operating_since_status": "Merchant-provided",
        "tin": identity(business.tin, business.tin_status),
        "trade_license": identity(business.trade_license_number, business.trade_license_status),
        "authorized_users": business.memberships.filter(is_active=True).count(),
        "is_demo": business.is_demo,
    }


def trend(monthly):
    complete = [m for m in monthly if not m["in_progress"]]
    last = complete[-MIN_MONTHS_FOR_TREND:]
    if len(last) < MIN_MONTHS_FOR_TREND or any(m["state"] != "recorded" for m in last):
        return {"available": False, "reason": f"Needs records in each of the last {MIN_MONTHS_FOR_TREND} complete months of the period."}
    prior = sum(Decimal(m["revenue"]) for m in last[:3]) / 3
    recent = sum(Decimal(m["revenue"]) for m in last[3:]) / 3
    return {"available": True, "recent_average": money(recent), "prior_average": money(prior),
            "change_percent": pct(recent - prior, prior), "recent_months": f"{last[3]['label']} – {last[5]['label']}",
            "prior_months": f"{last[0]['label']} – {last[2]['label']}"}


def seasonality(recorded):
    if len(recorded) < MIN_MONTHS_FOR_SEASONALITY:
        return {"available": False, "reason": f"Needs at least {MIN_MONTHS_FOR_SEASONALITY} months with records; {len(recorded)} available."}
    peak = max(recorded, key=lambda m: Decimal(m["revenue"]))
    trough = min(recorded, key=lambda m: Decimal(m["revenue"]))
    note = "Based on a single annual cycle, so seasonal and one-off effects can't be separated." if len(recorded) < 24 else "Based on two or more annual cycles."
    return {"available": True, "peak": peak["label"], "peak_revenue": peak["revenue"], "trough": trough["label"],
            "trough_revenue": trough["revenue"], "note": note}


def variability(recorded):
    values = [float(Decimal(m["revenue"])) for m in recorded]
    if len(values) < 3 or not statistics.mean(values):
        return {"available": False, "reason": "Needs at least 3 months with revenue records."}
    cv = statistics.pstdev(values) / statistics.mean(values)
    return {"available": True, "coefficient_of_variation": f"{cv:.2f}", "months": len(values)}


def evidence(business, txns):
    gross = sum((t.amount for t in txns), ZERO)
    rows = []
    for cls in TRANSACTION_EVIDENCE_CLASSES:
        items = [t for t in txns if t.evidence_class == cls]
        amount = sum((t.amount for t in items), ZERO)
        inflow = sum((t.amount for t in items if t.direction == Direction.INFLOW), ZERO)
        outflow = amount - inflow
        rows.append({"class": cls.value, "label": str(cls.label), "explanation": str(EVIDENCE_EXPLANATIONS[cls]),
                     "amount": money(amount), "count": len(items), "percent": pct(amount, gross),
                     "inflow_amount": money(inflow), "outflow_amount": money(outflow),
                     "demo_amount": money(sum((t.amount for t in items if t.is_demo), ZERO))})
    inflow_total = sum((t.amount for t in txns if t.direction == Direction.INFLOW), ZERO)
    supported = [t for t in txns if t.evidence_class == Evidence.EVIDENCE_SUPPORTED]
    doc_ids = Document.objects.filter(links__transaction__in=supported).distinct()
    return {
        "denominator": "Gross value of counted records in the period (money in + money out).",
        "denominator_amount": money(gross),
        "inflow_denominator_amount": money(inflow_total),
        "outflow_denominator_amount": money(gross - inflow_total),
        "record_count": len(txns),
        "by_class": rows,
        "demo_amount": money(sum((t.amount for t in txns if t.is_demo), ZERO)),
        "documents": {
            "linked_documents": doc_ids.count(),
            "reviewed": doc_ids.filter(validation_status=Document.Validation.REVIEWED).count(),
            "checks_only": doc_ids.filter(validation_status=Document.Validation.CHECKS_PASSED).count(),
        },
        "sources": [
            {"name": s.display_name, "is_demo": s.is_demo, "account": s.account_mask}
            for s in ConnectedSource.objects.filter(business=business, is_active=True)
        ],
    }


def receivables(business, on):
    buckets = {"Not yet due": ZERO, "1–30 days overdue": ZERO, "31–90 days overdue": ZERO, "Over 90 days overdue": ZERO}
    count = 0
    for invoice in Invoice.objects.filter(business=business, status=Invoice.Status.OPEN):
        outstanding = invoice.outstanding
        if outstanding <= 0:
            continue
        count += 1
        late = (on - invoice.due_date).days
        key = ("Not yet due" if late <= 0 else "1–30 days overdue" if late <= 30 else
               "31–90 days overdue" if late <= 90 else "Over 90 days overdue")
        buckets[key] += outstanding
    total = sum(buckets.values(), ZERO)
    return {"as_of": on.isoformat(), "open_invoices": count, "outstanding_total": money(total),
            "aging": [{"bucket": k, "amount": money(v)} for k, v in buckets.items()],
            "note": "Invoices recorded by the business on this platform only."}


def obligations(business, on, aggregate=False):
    items = [o for o in RecurringObligation.objects.filter(business=business) if o.is_current(on)]
    if aggregate:
        # Totals by type only: no names, counterparties or dates of individual obligations.
        by_kind = defaultdict(list)
        for o in items:
            by_kind[o.get_kind_display()].append(o)
        return {
            "items": [{"name": f"{kind} ({len(group)})", "type": kind, "counterparty": None,
                       "amount": money(sum((o.monthly_equivalent for o in group), ZERO)), "frequency": "Monthly (combined)",
                       "monthly_equivalent": money(sum((o.monthly_equivalent for o in group), ZERO)), "since": None, "until": None}
                      for kind, group in sorted(by_kind.items())],
            "monthly_equivalent_total": money(sum((o.monthly_equivalent for o in items), ZERO)),
            "note": "Recorded by the business; totalled by type. The platform does not discover obligations held outside it.",
        }
    return {
        "items": [{"name": o.name, "type": o.get_kind_display(), "counterparty": o.counterparty, "amount": money(o.amount),
                   "frequency": o.get_frequency_display(), "monthly_equivalent": money(o.monthly_equivalent),
                   "since": o.start_date.isoformat(), "until": o.end_date.isoformat() if o.end_date else None}
                  for o in items],
        "monthly_equivalent_total": money(sum((o.monthly_equivalent for o in items), ZERO)),
        "note": "Recorded by the business. The platform does not discover obligations held outside it.",
    }


AGGREGATE_FINDING_TEXT = {
    "missing_month": "No income or expenses are recorded for this month; its figures are unknown, not zero.",
    "unusual_change": "Revenue or operating expenses moved by 50% or more against the prior 3-month average.",
    "possible_duplicate": "Records with the same date, amount and direction that may be one transaction recorded twice.",
    "reconciliation_gap": "The connected account statement and the synced records differ for this month.",
    "missing_document": "Records of ETB 10,000 or more without a supporting document.",
    "open_dispute": "The business has disputed a record; the original record is preserved.",
    "future_dated": "A record is dated in the future.",
}


def _aggregate_findings(findings):
    """For institutions: counts by type and month. No amounts, dates or notes
    that point to an individual record."""
    groups = defaultdict(list)
    for f in findings:
        groups[(f.code, f.month)].append(f)
    rows = []
    for (code, month), group in sorted(groups.items(), key=lambda kv: (kv[0][1] or month_start(today()), kv[0][0]), reverse=True):
        notes = [f.merchant_note for f in group if f.merchant_note and code != "open_dispute"]
        rows.append({"code": code, "type": group[0].get_code_display(),
                     "title": f"{group[0].get_code_display()}{f' — {label(month)}' if month else ''}" + (f" ({len(group)})" if len(group) > 1 else ""),
                     "detail": AGGREGATE_FINDING_TEXT.get(code, ""), "month": month.strftime("%Y-%m") if month else None,
                     "count": len(group), "status": "Explained by the business" if notes else "Open",
                     "merchant_note": " / ".join(notes) or None})
    return rows


def data_quality(business, start, end, missing, unknown_before, aggregate=False):
    findings = quality.open_findings(business, start, end)
    sources = ConnectedSource.objects.filter(business=business)
    gaps = [f for f in findings if f.code == "reconciliation_gap"]
    if not sources.exists():
        reconciliation = "Not possible — no connected source provides statement balances."
    elif gaps:
        reconciliation = f"{len(gaps)} month(s) where the statement and synced records differ."
    else:
        reconciliation = "Synced records match statement balances for every month available."
    return {
        "missing_months": missing,
        "before_recorded_history": unknown_before,
        "reconciliation": reconciliation,
        "findings": _aggregate_findings(findings) if aggregate else [
            {"code": f.code, "type": f.get_code_display(), "title": f.title, "detail": f.detail,
             "month": f.month.strftime("%Y-%m") if f.month else None, "status": f.get_status_display(),
             "merchant_note": f.merchant_note or None} for f in findings],
        "note": "Indicators are neutral observations to guide review. They are not findings of error, fraud or creditworthiness.",
    }


def limitations(data, missing, txns, business):
    notes = []
    history = data["history"]
    if not txns:
        notes.append("There are no records in this period, so no financial figures can be shown.")
    if history["limited"]:
        notes.append(f"Limited history: {history['available_months']} month(s) of records are available, fewer than {LIMITED_HISTORY_MONTHS}. Trends and seasonality can't be assessed.")
    if missing:
        notes.append(f"{len(missing)} month(s) in the period have no records: {', '.join(missing)}. Their figures are unknown, not zero.")
    if data["includes_demo_data"]:
        notes.append("Includes data from a demo connector or synthetic seed data. It is not real verification.")
    gross = sum((t.amount for t in txns), ZERO)
    self_reported = sum((t.amount for t in txns if t.evidence_class == Evidence.SELF_REPORTED), ZERO)
    if gross and self_reported / gross >= Decimal("0.5"):
        notes.append(f"{pct(self_reported, gross)}% of recorded value is self-reported and has not been independently verified.")
    if not ConnectedSource.objects.filter(business=business).exists():
        notes.append("No connected account: no figures are system-verified and statement reconciliation isn't possible.")
    notes.append("Only activity recorded on this platform is included. Cash, accounts or obligations kept elsewhere are not visible.")
    notes.append("Figures are on a cash basis and are not audited financial statements or accounting profit.")
    return notes


def content_hash(data):
    stable = {k: v for k, v in data.items() if k != "generated_at"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()

