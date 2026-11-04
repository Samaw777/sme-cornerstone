"""End to end through the real views: onboard, record, import CSV, attach evidence, generate a passport."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ledger.models import ImportBatch, Transaction
from merchants.models import Business
from passport.models import PassportSnapshot

from .helpers import PDF, months_ago


class MerchantJourneyTests(TestCase):
    def test_full_journey(self):
        c = self.client
        c.post(reverse("signup"), {"name": "Tigist", "email": "tigist@shop.test",
                                   "password1": "a-strong-test-password", "password2": "a-strong-test-password"})
        response = c.post(reverse("onboarding"), {"name": "Tigist Spices", "sector": "retail", "operating_start_date": "2022-01-01",
                                                  "region": "oromia", "city": "Adama", "tin": "0011223344"})
        self.assertRedirects(response, reverse("staff"))
        business = Business.objects.get(name="Tigist Spices")
        self.assertEqual(business.tin_status, "merchant_provided")

        c.post(reverse("transaction_new"), {"direction": "out", "occurred_on": months_ago(1), "amount": "12000",
                                            "category": "stock", "description": "Spice stock"})
        txn = Transaction.objects.get(business=business)
        self.assertEqual(txn.evidence_class, "self_reported")

        upload = SimpleUploadedFile("sales.csv", f"date,amount,description\n{months_ago(1)},5000,Sales\n{months_ago(2)},4000,Sales\n".encode())
        c.post(reverse("import_list"), {"file": upload})
        batch = ImportBatch.objects.get(business=business)
        c.post(reverse("import_map", args=[batch.pk]), {"date": "date", "amount": "amount", "description": "description", "date_format": "iso"})
        self.assertContains(c.get(reverse("import_preview", args=[batch.pk])), "Ready to import")
        c.post(reverse("import_commit", args=[batch.pk]))
        self.assertEqual(Transaction.objects.filter(business=business).count(), 3)

        c.post(reverse("transaction_link", args=[txn.public_id]), {"file": SimpleUploadedFile("r.pdf", PDF), "kind": "receipt"})
        txn.refresh_from_db()
        self.assertEqual(txn.evidence_class, "evidence_supported")

        self.assertContains(c.get(reverse("dashboard")), "Evidence coverage")
        response = c.post(reverse("passport_generate") + "?preset=3m")
        snapshot = PassportSnapshot.objects.get(business=business)
        self.assertRedirects(response, reverse("passport_snapshot", args=[snapshot.public_id]))
        self.assertEqual(snapshot.data["totals"]["revenue"], "9000.00")
        self.assertEqual(snapshot.data["totals"]["operating_expenses"], "12000.00")
        pdf = c.get(reverse("passport_pdf", args=[snapshot.public_id]))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertContains(c.get(reverse("passport_snapshot", args=[snapshot.public_id])), "is not a credit decision")
