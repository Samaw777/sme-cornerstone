"""Financial integrity: imports, duplicates, corrections, evidence classes."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Evidence
from ledger import importing, services
from ledger.connectors import registry
from ledger.models import Correction, Document, SourceEvent, Transaction

from .helpers import PDF, Fixtures, months_ago


class CsvImportTests(Fixtures):
    def csv(self):
        a, b = months_ago(1), months_ago(2)
        return ("date,type,amount,category,description\n"
                f"{a},in,1200.00,Sales,Shop sales\n"
                f"{a},out,300,Rent,Rent\n"
                f"{b},in,\"2,500.50\",Sales,Market\n"
                f"not-a-date,in,10,Sales,Bad date\n"
                f"{b},in,abc,Sales,Bad amount\n")

    def test_preview_validates_rows_before_anything_is_saved(self):
        batch, rows = self.import_csv(self.csv())
        statuses = [r["status"] for r in rows]
        self.assertEqual(statuses, ["ok", "ok", "ok", "error", "error"])
        self.assertIn("isn't a YYYY-MM-DD date", rows[3]["errors"][0])
        self.assertIn("isn't a number", rows[4]["errors"][0])
        self.assertEqual(Transaction.objects.count(), 0)

    def test_commit_imports_valid_rows_as_self_reported_with_source_events(self):
        batch, _ = self.import_csv(self.csv())
        importing.commit(batch, self.owner)
        txns = Transaction.objects.filter(business=self.business)
        self.assertEqual(txns.count(), 3)
        self.assertTrue(all(t.evidence_class == Evidence.SELF_REPORTED for t in txns))
        self.assertEqual(SourceEvent.objects.filter(import_batch=batch).count(), 3)
        self.assertEqual(txns.get(amount=Decimal("2500.50")).direction, "in")

    def test_reimporting_the_same_file_does_not_double_count(self):
        batch, _ = self.import_csv(self.csv())
        importing.commit(batch, self.owner)
        total_before = Transaction.objects.active().filter(business=self.business).total()
        batch2, rows = self.import_csv(self.csv(), name="again.csv")
        self.assertEqual(importing.summarise(rows)["duplicate"], 3)
        batch2 = importing.commit(batch2, self.owner)
        self.assertEqual(batch2.rows_imported, 0)
        self.assertEqual(Transaction.objects.active().filter(business=self.business).total(), total_before)

    def test_overlapping_export_with_different_number_formatting_is_detected(self):
        a = months_ago(1)
        batch, _ = self.import_csv(f"date,amount,description\n{a},1200,Shop sales\n")
        importing.commit(batch, self.owner)
        batch2, rows = self.import_csv(f"date,amount,description\n{a},\"ETB 1,200.00\",Shop sales\n{a},50,New\n")
        self.assertEqual([r["status"] for r in rows], ["duplicate", "ok"])

    def test_identical_rows_in_one_file_need_confirmation(self):
        a = months_ago(1)
        text = f"date,amount,description\n{a},100,Tea\n{a},100,Tea\n"
        batch, rows = self.import_csv(text)
        self.assertEqual([r["status"] for r in rows], ["ok", "repeat"])
        self.assertEqual(importing.commit(batch, self.owner).rows_imported, 1)
        batch2, _ = self.import_csv(text, name="b.csv")
        self.assertEqual(importing.commit(batch2, self.owner, include_repeats=True).rows_imported, 1)
        batch3, rows3 = self.import_csv(text, name="c.csv")
        self.assertEqual([r["status"] for r in rows3], ["duplicate", "duplicate"])

    def test_negative_amount_means_money_out_and_future_dates_are_rejected(self):
        future = months_ago(-2)
        batch, rows = self.import_csv(f"date,amount\n{months_ago(1)},-450\n{future},10\n")
        self.assertEqual(rows[0]["direction"], "out")
        self.assertEqual(rows[1]["status"], "error")


class CorrectionTests(Fixtures):
    def test_amendment_preserves_original_and_audit_trail(self):
        original = self.record("out", "rent", "45000", months_ago(1))
        replacement = services.amend(original, self.owner, {
            "direction": "out", "category": "rent", "amount": Decimal("4500"), "currency": "ETB",
            "occurred_on": months_ago(1), "description": "", "counterparty": "", "reference": "",
        }, "Typo")
        original.refresh_from_db()
        self.assertEqual(original.status, Transaction.Status.SUPERSEDED)
        self.assertEqual(original.amount, Decimal("45000"))
        correction = Correction.objects.get(original=original)
        self.assertEqual((correction.replacement, correction.author, correction.reason), (replacement, self.owner, "Typo"))
        self.assertIsNotNone(correction.created_at)
        self.assertEqual(Transaction.objects.active().filter(business=self.business).total(), Decimal("4500"))
        self.assertEqual(replacement.lineage(), [original, replacement])

    def test_reversal_excludes_record_but_keeps_it(self):
        txn = self.record("in", "sales", "100", months_ago(1))
        services.reverse(txn, self.owner, "Entered twice")
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.REVERSED)
        self.assertEqual(Transaction.objects.active().filter(business=self.business).count(), 0)
        self.assertEqual(Transaction.objects.filter(business=self.business).count(), 1)

    def test_correction_requires_reason_and_current_version(self):
        txn = self.record("in", "sales", "100", months_ago(1))
        with self.assertRaises(ValidationError):
            services.reverse(txn, self.owner, "  ")
        services.reverse(txn, self.owner, "dup")
        with self.assertRaises(ValidationError):
            services.reverse(txn, self.owner, "again")

    def test_records_and_source_events_cannot_be_edited_in_place(self):
        txn = self.record("in", "sales", "100", months_ago(1))
        txn.amount = Decimal("1")
        with self.assertRaises(TypeError):
            txn.save()
        with self.assertRaises(TypeError):
            txn.source_event.save()
        with self.assertRaises(TypeError):
            SourceEvent.objects.all().delete()

    def test_connector_records_can_be_disputed_but_not_amended(self):
        source = registry.connect(self.business, self.owner, "mock_bank")
        registry.sync(source, self.owner)
        txn = Transaction.objects.filter(business=self.business, source_kind="connector").first()
        with self.assertRaises(ValidationError):
            services.reverse(txn, self.owner, "no")
        services.annotate(txn, self.owner, "dispute", "Refunded")
        self.assertEqual(txn.annotations.count(), 1)


class EvidenceTests(Fixtures):
    def upload(self, name="r.pdf", data=PDF):
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def test_linking_a_document_makes_a_record_evidence_supported_and_rejection_reverts(self):
        txn = self.record("out", "stock", "900", months_ago(1))
        self.assertEqual(txn.evidence_class, Evidence.SELF_REPORTED)
        document = services.store_document(self.business, self.owner, self.upload(), "receipt", "Receipt")
        services.link_document(document, self.owner, transaction=txn)
        txn.refresh_from_db()
        self.assertEqual(txn.evidence_class, Evidence.EVIDENCE_SUPPORTED)
        services.set_document_validation(document, self.owner, Document.Validation.REJECTED)
        txn.refresh_from_db()
        self.assertEqual(txn.evidence_class, Evidence.SELF_REPORTED)

    def test_csv_with_receipt_is_never_system_verified(self):
        batch, _ = self.import_csv(f"date,amount\n{months_ago(1)},100\n")
        importing.commit(batch, self.owner)
        txn = Transaction.objects.get(business=self.business)
        document = services.store_document(self.business, self.owner, self.upload(), "receipt", "R")
        services.link_document(document, self.owner, transaction=txn)
        txn.refresh_from_db()
        self.assertEqual(txn.evidence_class, Evidence.EVIDENCE_SUPPORTED)

    def test_mock_connector_is_system_verified_structure_but_labelled_demo_and_sync_is_idempotent(self):
        source = registry.connect(self.business, self.owner, "mock_bank")
        added = registry.sync(source, self.owner)
        self.assertGreater(added, 0)
        self.assertEqual(registry.sync(source, self.owner), 0)
        txns = Transaction.objects.filter(business=self.business)
        self.assertEqual(txns.count(), added)
        self.assertTrue(all(t.is_demo and t.evidence_class == Evidence.SYSTEM_VERIFIED for t in txns))
        self.assertTrue(source.is_demo)

    def test_uploads_are_checked_by_content_and_deduplicated(self):
        with self.assertRaises(ValidationError):
            services.store_document(self.business, self.owner, self.upload("fake.pdf", b"MZ\x90 not a pdf"), "receipt", "x")
        services.store_document(self.business, self.owner, self.upload(), "receipt", "one")
        with self.assertRaises(ValidationError):
            services.store_document(self.business, self.owner, self.upload("copy.pdf"), "receipt", "two")

    def test_documents_cannot_link_across_businesses(self):
        txn = self.record("out", "stock", "900", months_ago(1), business=self.other, user=self.other_owner)
        document = services.store_document(self.business, self.owner, self.upload(), "receipt", "R")
        with self.assertRaises(ValidationError):
            services.link_document(document, self.owner, transaction=txn)
