"""Passport figures match records; unknowns are never zero; limitations are explicit."""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Evidence
from core.periods import add_months, label, month_end, today
from ledger import services
from passport import metrics, pdf
from passport import services as passport_services
from passport.models import PassportSnapshot

from .helpers import PDF, Fixtures, months_ago, this_month


class PassportFigureTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.start = add_months(this_month(), -2)
        self.end = month_end(add_months(this_month(), -1))
        # Month -2: no records at all. Month -1: known figures.
        self.record("in", "sales", "10000", months_ago(1))
        self.record("in", "service_income", "2000.50", months_ago(1, 9))
        self.record("in", "loan_received", "50000", months_ago(1, 10))
        self.record("out", "rent", "3000", months_ago(1, 11))
        self.record("out", "loan_repayment", "1500", months_ago(1, 12))
        wrong = self.record("out", "stock", "9999", months_ago(1, 13))
        services.reverse(wrong, self.owner, "Entered by mistake")
        # A record before the period must not leak in.
        self.record("in", "sales", "777", months_ago(5))

    def compute(self, **kwargs):
        return metrics.compute(self.business, self.start, self.end, **kwargs)

    def test_monthly_figures_match_records_and_methods(self):
        data = self.compute()
        known = data["monthly"][1]
        self.assertEqual(known["revenue"], "12000.50")
        self.assertEqual(known["operating_expenses"], "3000.00")
        self.assertEqual(known["revenue_less_operating_expenses"], "9000.50")
        self.assertEqual(known["inflows"], "62000.50")          # loan received is cash in, not revenue
        self.assertEqual(known["outflows"], "4500.00")          # loan repayment is cash out, not expense
        self.assertEqual(known["net_cash_movement"], "57500.50")
        self.assertEqual(known["transactions"], 5)              # reversed record excluded
        self.assertEqual(data["totals"]["revenue"], "12000.50")

    def test_month_without_records_is_unknown_not_zero(self):
        data = self.compute()
        missing = data["monthly"][0]
        self.assertEqual(missing["state"], "no_records")
        self.assertIsNone(missing["revenue"])
        self.assertIsNone(missing["net_cash_movement"])
        self.assertIn(label(self.start), data["data_quality"]["missing_months"])
        self.assertTrue(any("unknown, not zero" in line for line in data["limitations"]))
        self.assertIn("1 of 2 months", data["totals"]["basis"])

    def test_months_before_first_record_are_marked_as_before_history(self):
        data = metrics.compute(self.business, add_months(this_month(), -8), self.end)
        self.assertEqual(data["monthly"][0]["state"], "before_history")
        self.assertIsNone(data["monthly"][0]["revenue"])

    def test_evidence_breakdown_uses_stated_denominator_and_excludes_derived(self):
        document = services.store_document(self.business, self.owner,
                                           SimpleUploadedFile("r.pdf", PDF),
                                           "receipt", "Rent receipt")
        rent = self.business.transactions.get(category="rent", status="active")
        services.link_document(document, self.owner, transaction=rent)
        evidence = self.compute()["evidence"]
        self.assertEqual(evidence["denominator_amount"], "66500.50")
        classes = {row["class"]: row for row in evidence["by_class"]}
        self.assertNotIn(Evidence.DERIVED.value, classes)
        self.assertEqual(classes["evidence_supported"]["amount"], "3000.00")
        self.assertEqual(classes["self_reported"]["amount"], "63500.50")
        self.assertEqual(classes["system_verified"]["amount"], "0.00")
        self.assertEqual(sum(Decimal(r["amount"]) for r in evidence["by_class"]), Decimal("66500.50"))

    def test_trend_and_seasonality_are_not_invented_from_short_periods(self):
        data = self.compute()
        self.assertFalse(data["trend"]["available"])
        self.assertFalse(data["seasonality"]["available"])
        self.assertIn("reason", data["trend"])

    def test_limited_history_is_flagged(self):
        self.assertFalse(self.compute()["history"]["limited"])  # first record 5 months ago: 6 months of history
        self.record("in", "sales", "10", months_ago(1), business=self.other, user=self.other_owner)
        data = metrics.compute(self.other, self.start, self.end)
        self.assertTrue(data["history"]["limited"])
        self.assertEqual(data["history"]["available_months"], 2)
        self.assertTrue(any(line.startswith("Limited history") for line in data["limitations"]))

    def test_disclaimer_scope_and_pdf(self):
        snapshot = passport_services.generate(self.business, self.start, self.end, user=self.owner)
        self.assertEqual(snapshot.data["disclaimer"], "This report provides financial evidence and is not a credit decision.")
        self.assertTrue(pdf.render(snapshot).startswith(b"%PDF"))
        limited = metrics.compute(self.business, self.start, self.end, ["profile"])
        self.assertNotIn("monthly", limited)
        self.assertNotIn("evidence", limited)
        self.assertIn("disclaimer", limited)

    def test_snapshots_are_frozen(self):
        snapshot = passport_services.generate(self.business, self.start, self.end, user=self.owner)
        self.record("in", "sales", "1", months_ago(1, 20))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.data["totals"]["revenue"], "12000.50")
        with self.assertRaises(TypeError):
            snapshot.save()
        newer = passport_services.generate(self.business, self.start, self.end, user=self.owner)
        self.assertEqual(newer.version, snapshot.version + 1)
        self.assertEqual(PassportSnapshot.objects.count(), 2)

    def test_business_with_no_records_can_still_generate(self):
        data = metrics.compute(self.other, self.start, self.end)
        self.assertIsNone(data["totals"]["revenue"])
        self.assertTrue(any("no records in this period" in line for line in data["limitations"]))
        self.assertTrue(data["history"]["limited"])

    def test_period_is_capped_at_today(self):
        data = metrics.compute(self.business, self.start, add_months(today(), 6))
        self.assertEqual(data["period"]["end"], month_end(today()).isoformat())
