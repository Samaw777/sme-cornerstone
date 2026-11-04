"""Tenant isolation, role permissions, administrator access and demo labelling."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import User
from core.models import AuditEvent
from ledger import services
from ledger.connectors import registry
from merchants.models import BusinessMembership
from passport import metrics
from passport import services as passport_services
from pilot.models import SupportAccess

from .helpers import PDF, Fixtures, months_ago, this_month


class TenantIsolationTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.their_txn = self.record("in", "sales", "999", months_ago(1), business=self.other, user=self.other_owner)
        self.their_doc = services.store_document(self.other, self.other_owner, SimpleUploadedFile("r.pdf", PDF), "receipt", "Theirs")
        self.their_snapshot = passport_services.generate(self.other, this_month(), this_month(), user=self.other_owner)
        self.login(self.owner)

    def test_cannot_open_another_businesses_records(self):
        for url in (reverse("transaction_detail", args=[self.their_txn.public_id]),
                    reverse("passport_snapshot", args=[self.their_snapshot.public_id]),
                    reverse("passport_pdf", args=[self.their_snapshot.public_id]),
                    reverse("document_download", args=[self.their_doc.public_id])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        response = self.client.post(reverse("transaction_reverse", args=[self.their_txn.public_id]), {"reason": "x"})
        self.assertEqual(response.status_code, 404)
        self.their_txn.refresh_from_db()
        self.assertEqual(self.their_txn.status, "active")
        self.assertTrue(AuditEvent.objects.filter(action="document.denied").exists())

    def test_lists_only_show_own_records(self):
        self.record("in", "sales", "123", months_ago(1), description="Mine")
        response = self.client.get(reverse("transactions"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "999.00")
        self.assertNotContains(self.client.get(reverse("documents")), "Theirs")

    def test_cannot_switch_into_a_business_without_membership(self):
        self.assertEqual(self.client.post(reverse("switch_business", args=[self.other.public_id])).status_code, 404)

    def test_staff_permissions_are_enforced_server_side(self):
        staff = User.objects.create_user("staff@a.test", "pw-123456789x")
        BusinessMembership.objects.create(user=staff, business=self.business, role="staff", permissions=["record"])
        self.login(staff)
        self.assertEqual(self.client.get(reverse("import_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)  # no "reports" permission
        self.assertEqual(self.client.get(reverse("staff")).status_code, 403)
        response = self.client.post(reverse("transaction_new"), {"direction": "in", "occurred_on": months_ago(1),
                                                                  "amount": "50", "category": "sales"})
        self.assertEqual(response.status_code, 302)


class AdminAccessTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user("admin@x.test", "pw-123456789x", is_platform_admin=True)
        self.doc = services.store_document(self.business, self.owner, SimpleUploadedFile("r.pdf", PDF), "receipt", "R")

    def test_admin_needs_audited_support_access_for_documents(self):
        self.login(self.admin)
        url = reverse("document_download", args=[self.doc.public_id])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.post(reverse("console_business", args=[self.business.public_id]), {"action": "support", "reason": "Ticket 7"})
        self.assertTrue(SupportAccess.objects.filter(admin=self.admin, business=self.business).exists())
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="support.access_started", business=self.business).exists())
        self.login(self.owner)
        self.assertContains(self.client.get(reverse("sharing")), "Ticket 7")

    def test_console_is_admin_only(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("console_home")).status_code, 403)
        self.login(self.analyst)
        self.assertEqual(self.client.get(reverse("console_pilot")).status_code, 403)


class DemoLabellingTests(Fixtures):
    def test_demo_source_is_labelled_everywhere(self):
        source = registry.connect(self.business, self.owner, "mock_bank")
        registry.sync(source, self.owner)
        data = metrics.compute(self.business, this_month(), this_month())
        self.assertTrue(data["includes_demo_data"])
        self.assertTrue(any("demo" in line.lower() for line in data["limitations"]))
        self.assertTrue(data["evidence"]["sources"][0]["is_demo"])
        self.login(self.owner)
        self.assertContains(self.client.get(reverse("transactions")), "badge-demo")
        self.assertContains(self.client.get(reverse("sources")), "Demo source")
