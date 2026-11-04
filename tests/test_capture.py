"""Easy logging: quick add, voice/Telegram parsing, pasted SMS, bills, approvals, Telegram bot."""

import datetime as dt
import json
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from capture import parsing
from capture.models import QuickTemplate, TelegramLink, TelegramLinkCode
from capture.services import bills_due, mark_bill_paid
from core.periods import add_months, month_end, today
from ledger import services
from ledger.models import ObligationPayment, RecurringObligation, Transaction
from merchants.models import BusinessMembership
from passport import metrics

from .helpers import Fixtures, months_ago, this_month

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
SMS_IN = ("Dear Customer, your Account 1000****1234 has been credited with ETB 25,000.00 from Modjo Dry Port Agent "
          "on 03/11/2026. Ref No FT26307ABC12. Your available balance is ETB 140,250.55.")
SMS_OUT = ("You have paid 1,850.00 Birr to Kality Fuel Station on 2026-11-02. Your transaction number is CK21XZ9P0Q. "
           "Service fee 2.00 Birr. Your telebirr balance is 3,410.00 Birr.")


class ParsingTests(Fixtures):
    def test_free_text_in_english_and_amharic(self):
        r = parsing.parse_text("expense 3200 diesel cash")
        self.assertEqual((r["direction"], r["amount"], r["category"], r["payment_method"]), ("out", Decimal("3200"), "transport", "cash"))
        r = parsing.parse_text("sale 4,500 freight Adama telebirr")
        self.assertEqual((r["direction"], r["amount"], r["category"], r["payment_method"]), ("in", Decimal("4500"), "service_income", "telebirr"))
        r = parsing.parse_text("ወጪ 2,300 ነዳጅ ትናንት")
        self.assertEqual((r["direction"], r["amount"], r["category"]), ("out", Decimal("2300"), "transport"))
        self.assertEqual(r["occurred_on"], today() - dt.timedelta(days=1))
        self.assertEqual(parsing.parse_text("2.5k tyres")["amount"], Decimal("2500"))
        self.assertTrue(parsing.parse_text("hello")["errors"])

    def test_sms_reads_transaction_not_balance_or_fee(self):
        r = parsing.parse_sms(SMS_IN)
        self.assertEqual((r["direction"], r["amount"], r["reference"], r["counterparty"], r["payment_method"]),
                         ("in", Decimal("25000.00"), "FT26307ABC12", "Modjo Dry Port Agent", "bank"))
        self.assertEqual(r["occurred_on"], dt.date(2026, 11, 3))
        r = parsing.parse_sms(SMS_OUT)
        self.assertEqual((r["direction"], r["amount"], r["reference"], r["payment_method"]),
                         ("out", Decimal("1850.00"), "CK21XZ9P0Q", "telebirr"))
        self.assertTrue(parsing.looks_like_sms(SMS_OUT))
        self.assertFalse(parsing.looks_like_sms("expense 3200 diesel"))


class ApprovalTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff@a.test", "pw-123456789x")
        BusinessMembership.objects.create(user=self.staff, business=self.business, role="staff", permissions=["record", "reports"])

    def quick(self, user, **data):
        self.login(user)
        payload = {"direction": "in", "amount": "5000", "category": "sales", "payment_method": "cash", "when": "yesterday", **data}
        return self.client.post(reverse("quick_add"), payload, HTTP_ACCEPT="application/json")

    def test_owner_entries_count_staff_entries_wait(self):
        self.assertTrue(self.quick(self.owner).json()["ok"])
        body = self.quick(self.staff, amount="700").json()
        self.assertTrue(body["pending"])
        statuses = dict(Transaction.objects.values_list("amount", "status"))
        self.assertEqual(statuses[Decimal("5000.00")], "active")
        self.assertEqual(statuses[Decimal("700.00")], "pending")
        data = metrics.compute(self.business, this_month(), this_month())
        self.assertNotEqual(data["monthly"][0]["revenue"], "5700.00")
        self.assertTrue(any("awaiting approval" in line for line in data["limitations"]))

    def test_approver_approves_and_rejects_with_reason(self):
        self.quick(self.staff, amount="700")
        self.quick(self.staff, amount="900")
        a, b = Transaction.objects.filter(status="pending").order_by("amount")
        self.login(self.staff)
        self.assertEqual(self.client.get(reverse("approvals")).status_code, 403)
        with self.assertRaises(ValidationError):
            services.decide(a, self.staff, True)
        self.login(self.owner)
        self.client.post(reverse("approvals"), {"txn": [a.public_id], "decision": "approve"})
        self.client.post(reverse("approvals"), {"txn": [b.public_id], "decision": "reject"})  # no reason -> refused
        b.refresh_from_db()
        self.assertEqual(b.status, "pending")
        self.client.post(reverse("approvals"), {"txn": [b.public_id], "decision": "reject", "reason": "Duplicate"})
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.status, b.status), ("active", "rejected"))
        self.assertEqual(a.approval_decisions.get().decided_by, self.owner)
        self.assertEqual(b.approval_decisions.get().reason, "Duplicate")

    def test_staff_with_approve_permission_skip_the_queue_and_others_cannot_correct(self):
        txn = services.record_manual(self.business, self.owner, {"direction": "in", "category": "sales", "amount": Decimal("10"),
                                                                 "occurred_on": months_ago(1)})
        with self.assertRaises(ValidationError):
            services.reverse(txn, self.staff, "mine")
        BusinessMembership.objects.filter(user=self.staff).update(permissions=["record", "reports", "approve"])
        self.assertFalse(self.quick(self.staff, amount="300").json()["pending"])

    def test_staff_csv_import_waits_for_approval(self):
        from ledger import importing

        batch, _ = self.import_csv(f"date,amount\n{months_ago(1)},100\n")
        batch.uploaded_by = self.staff
        importing.commit(batch, self.staff)
        self.assertEqual(Transaction.objects.get().status, "pending")


class QuickAddTests(Fixtures):
    def test_validation_photo_and_favourites(self):
        self.login(self.owner)
        bad = self.client.post(reverse("quick_add"), {"direction": "out", "amount": "-5", "category": "sales"}, HTTP_ACCEPT="application/json")
        self.assertEqual(bad.status_code, 400)
        response = self.client.post(reverse("quick_add"), {
            "direction": "out", "amount": "3,200", "category": "transport", "payment_method": "cash", "when": "today",
            "save_as": "Diesel", "photo": SimpleUploadedFile("receipt.jpg", JPEG, content_type="image/jpeg"),
        }, HTTP_ACCEPT="application/json")
        self.assertTrue(response.json()["ok"])
        txn = Transaction.objects.get()
        self.assertEqual((txn.amount, txn.payment_method, txn.evidence_class), (Decimal("3200.00"), "cash", "evidence_supported"))
        self.assertTrue(QuickTemplate.objects.filter(business=self.business, label="Diesel", category="transport").exists())
        self.assertContains(self.client.get(reverse("quick_add")), "Diesel")

    def test_voice_parse_endpoint_and_service_worker(self):
        self.login(self.owner)
        body = self.client.post(reverse("quick_parse"), {"text": "expense 450 airtime telebirr"}).json()
        self.assertEqual((body["direction"], body["amount"], body["category"], body["payment_method"]), ("out", "450", "telecom", "telebirr"))
        sw = self.client.get(reverse("service_worker"))
        self.assertEqual(sw["Content-Type"], "application/javascript")


class SmsPasteTests(Fixtures):
    def test_preview_then_save_and_no_double_count(self):
        self.login(self.owner)
        preview = self.client.post(reverse("sms"), {"text": SMS_IN + "\n\n" + SMS_OUT})
        self.assertContains(preview, "FT26307ABC12")
        form = {"step": "save", "count": "2"}
        for i, (raw, direction, amount, category, method, day) in enumerate([
            (SMS_IN, "in", "25000.00", "sales", "bank", "2026-11-03"), (SMS_OUT, "out", "1850.00", "transport", "telebirr", "2026-11-02")]):
            form.update({f"keep-{i}": "1", f"raw-{i}": raw, f"direction-{i}": direction, f"amount-{i}": amount, f"category-{i}": category,
                         f"payment_method-{i}": method, f"occurred_on-{i}": day, f"counterparty-{i}": "", f"description-{i}": ""})
        self.client.post(reverse("sms"), form)
        self.assertEqual(Transaction.objects.filter(source_kind="sms").count(), 2)
        self.assertEqual(Transaction.objects.get(direction="in").source_event.payload["raw"], " ".join(SMS_IN.split()))
        self.client.post(reverse("sms"), form)
        self.assertEqual(Transaction.objects.filter(source_kind="sms").count(), 2)
        self.assertContains(self.client.post(reverse("sms"), {"text": SMS_IN}), "Already recorded")


class BillTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.rent = RecurringObligation.objects.create(business=self.business, name="Shop rent", counterparty="Landlord", kind="rent",
                                                       amount=Decimal("15000"), frequency="monthly", start_date=add_months(this_month(), -1))

    def test_due_bills_mark_paid_once(self):
        due = bills_due(self.business)
        self.assertIn(add_months(this_month(), -1), [d["due_date"] for d in due])
        txn = mark_bill_paid(self.rent, add_months(this_month(), -1), self.owner, "bank")
        self.assertEqual((txn.category, txn.amount, txn.payment_method, txn.source_kind), ("rent", Decimal("15000.00"), "bank", "bill"))
        self.assertTrue(ObligationPayment.objects.filter(obligation=self.rent, transaction=txn).exists())
        with self.assertRaises(ValidationError):
            mark_bill_paid(self.rent, add_months(this_month(), -1), self.owner)
        self.assertNotIn(add_months(this_month(), -1), [d["due_date"] for d in bills_due(self.business)])

    def test_rent_already_recorded_normally_isnt_nagged(self):
        self.record("out", "rent", "15000", this_month())
        self.assertNotIn(this_month(), [d["due_date"] for d in bills_due(self.business)])


@override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_WEBHOOK_SECRET="s3cret")
class TelegramTests(Fixtures):
    def post(self, message, secret="s3cret"):
        return self.client.post(reverse("telegram_webhook"), json.dumps({"message": message}), content_type="application/json",
                                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret)

    def test_webhook_requires_secret(self):
        self.assertEqual(self.post({"chat": {"id": 1}, "text": "hi"}, secret="wrong").status_code, 404)

    @mock.patch("capture.telegram.call")
    def test_link_then_log_text_sms_and_photo(self, call):
        call.return_value = {"ok": True, "result": {"file_path": "p.jpg", "file_size": 100}}
        membership = self.business.memberships.get(user=self.owner)
        code = TelegramLinkCode.new(membership, timezone.now() + dt.timedelta(minutes=5))
        self.post({"chat": {"id": 42}, "from": {"username": "sam"}, "text": f"/link {code.code}"})
        self.assertTrue(TelegramLink.objects.filter(chat_id=42, membership=membership).exists())
        self.post({"chat": {"id": 42}, "text": "expense 3200 diesel cash"})
        self.post({"chat": {"id": 42}, "text": SMS_IN})
        self.post({"chat": {"id": 42}, "text": SMS_IN})  # forwarded twice: still one record
        with mock.patch("capture.telegram.download_photo", return_value=SimpleUploadedFile("t.jpg", JPEG, content_type="image/jpeg")):
            self.post({"chat": {"id": 42}, "caption": "expense 2500 tyres", "photo": [{"file_id": "x"}]})
        txns = Transaction.objects.filter(business=self.business, source_kind="telegram")
        self.assertEqual(txns.count(), 3)
        self.assertEqual(txns.get(amount=Decimal("2500")).evidence_class, "evidence_supported")
        self.assertEqual(txns.get(amount=Decimal("25000")).reference, "FT26307ABC12")
        sent = " ".join(str(c.kwargs.get("text", "")) for c in call.call_args_list)
        self.assertIn("Recorded", sent)
        self.assertIn("already recorded", sent)

    @mock.patch("capture.telegram.call")
    def test_unlinked_chat_cannot_log(self, call):
        call.return_value = {"ok": True}
        self.post({"chat": {"id": 7}, "text": "expense 100 fuel"})
        self.assertEqual(Transaction.objects.count(), 0)


class DigitalShareTests(Fixtures):
    def test_digital_payment_share(self):
        for n in (1, 2, 3):
            services.record_manual(self.business, self.owner, {"direction": "in", "category": "sales", "amount": Decimal("6000"),
                                                               "occurred_on": months_ago(n), "payment_method": "telebirr"})
            services.record_manual(self.business, self.owner, {"direction": "out", "category": "rent", "amount": Decimal("4000"),
                                                               "occurred_on": months_ago(n, 6), "payment_method": "cash"})
        data = metrics.compute(self.business, add_months(this_month(), -3), month_end(add_months(this_month(), -1)))
        share = {m["key"]: m for m in data["credit_metrics"]["metrics"]}["digital_share"]
        self.assertEqual(share["value"], "60.0")
