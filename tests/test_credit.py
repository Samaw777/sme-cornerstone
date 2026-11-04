"""Credit-relevant metrics, and that institutions only ever receive aggregates."""

import datetime as dt
from decimal import Decimal

from django.urls import reverse

from core.periods import add_months, month_end, today
from ledger import services
from ledger.models import Invoice, RecurringObligation
from passport import metrics
from sharing.models import DataCategory, PartnerCredential

from .helpers import Fixtures, months_ago, this_month


def credit(data):
    cm = data["credit_metrics"]
    return {m["key"]: m for m in cm["scores"] + cm["metrics"]}


class CreditMetricTests(Fixtures):
    def setUp(self):
        super().setUp()
        for n in (1, 2, 3, 4):
            self.record("in", "sales", "10000", months_ago(n))
            self.record("out", "rent", "6000", months_ago(n, 6))
            self.record("out", "loan_repayment", "1000", months_ago(n, 7))
        self.start, self.end = add_months(this_month(), -3), month_end(add_months(this_month(), -1))

    def test_ratios_match_records_and_methods(self):
        m = credit(metrics.compute(self.business, add_months(this_month(), -4), self.end))
        self.assertEqual(m["operating_margin"]["value"], "40.0")
        self.assertEqual(m["surplus_months"]["value"], "4 of 4")
        self.assertEqual(m["debt_service_coverage"]["value"], "4.00")  # (10000-6000) / 1000
        self.assertEqual(m["evidence_strength"]["value"], "20")         # all self-reported, weight 0.2
        self.assertFalse(m["revenue_stability"]["available"])            # needs 6 months
        self.assertIn("6 complete months", m["revenue_stability"]["reason"])

    def test_unknowns_are_not_invented(self):
        m = credit(metrics.compute(self.business, add_months(this_month(), -4), self.end))
        self.assertFalse(m["cash_buffer"]["available"])
        self.assertIn("unknown", m["cash_buffer"]["reason"])
        empty = credit(metrics.compute(self.other, self.start, self.end))
        self.assertFalse(empty["debt_service_coverage"]["available"])
        self.assertFalse(empty["evidence_strength"]["available"])

    def test_no_recorded_debt_is_not_reported_as_no_debt(self):
        m = credit(metrics.compute(self.other, self.start, self.end))
        for n in (1, 2, 3):
            self.record("in", "sales", "100", months_ago(n), business=self.other, user=self.other_owner)
        m = credit(metrics.compute(self.other, self.start, self.end))
        self.assertFalse(m["debt_service_coverage"]["available"])
        self.assertIn("does not mean there are none", m["debt_service_coverage"]["reason"])

    def test_receivable_days(self):
        Invoice.objects.create(business=self.business, number="I-1", customer="C", issue_date=today() - dt.timedelta(days=40),
                               due_date=today() - dt.timedelta(days=10), amount=Decimal("5000"))
        m = credit(metrics.compute(self.business, add_months(this_month(), -4), self.end))
        self.assertEqual(m["receivable_days"]["value"], "15")  # 5000 / 10000 * 30
        inputs = {i["key"]: i for i in m["receivable_days"]["inputs"]}
        self.assertEqual(inputs["overdue_share_percent"]["value"], "100.0")
        self.assertEqual(inputs["outstanding"]["unit"], "ETB")

    def test_no_composite_credit_score(self):
        data = metrics.compute(self.business, self.start, self.end)
        self.assertEqual({s["key"] for s in data["credit_metrics"]["scores"]}, {"evidence_strength", "data_completeness"})
        self.assertIn("not a credit score", data["credit_metrics"]["note"])
        self.assertEqual(data["disclaimer"], "This report provides financial evidence and is not a credit decision.")


class InstitutionPrivacyTests(Fixtures):
    def setUp(self):
        super().setUp()
        txn = self.record("out", "stock", "18000", months_ago(1), counterparty="Secret Supplier PLC")
        services.annotate(txn, self.owner, "dispute", "Refund from Secret Supplier not shown")
        RecurringObligation.objects.create(business=self.business, name="Truck loan", counterparty="Private Lender X",
                                           kind="loan", amount=Decimal("3000"), frequency="monthly", start_date=dt.date(2025, 1, 1))
        self.consent()

    def test_bank_portal_sees_aggregates_not_records(self):
        self.login(self.analyst)
        response = self.client.get(reverse("bank_business", args=[self.business.public_id]))
        self.assertContains(response, "Credit-relevant metrics")
        self.assertContains(response, "Disputed record")
        for private in ("Secret Supplier", "Private Lender X", "Truck loan", "18,000.00 on"):
            self.assertNotContains(response, private)
        # The merchant's own passport keeps the detail.
        self.login(self.owner)
        own = self.client.get(reverse("passport"))
        self.assertContains(own, "Truck loan")
        self.assertContains(own, "Refund from Secret Supplier")

    def test_api_credit_metrics_need_scope_and_category(self):
        _, token = PartnerCredential.issue(self.mfi, "t", [s.value for s in PartnerCredential.Scope])
        url = f"/api/v1/businesses/{self.business.public_id}/credit-metrics"
        body = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}").json()
        self.assertIn("credit_metrics", body)
        self.assertNotIn("Secret Supplier", str(body))
        dq = self.client.get(f"/api/v1/businesses/{self.business.public_id}/data-quality", HTTP_AUTHORIZATION=f"Bearer {token}").json()
        self.assertNotIn("Secret Supplier", str(dq))
        _, narrow = PartnerCredential.issue(self.mfi, "n", [PartnerCredential.Scope.PROFILE])
        self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {narrow}").status_code, 403)
        self.consent(business=self.other, categories=[DataCategory.PROFILE])
        other = self.client.get(f"/api/v1/businesses/{self.other.public_id}/credit-metrics", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(other.json()["error"]["code"], "category_not_permitted")
