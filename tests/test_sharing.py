"""Consent enforcement in the analyst portal, and the merchant consent workflow."""

import datetime as dt

from django.urls import reverse
from django.utils import timezone

from core.models import AuditEvent
from core.periods import add_months
from sharing.models import AccessReceipt, ConsentGrant, DataCategory

from .helpers import Fixtures, months_ago, this_month


class PortalConsentTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.record("in", "sales", "5000", months_ago(1))
        self.url = reverse("bank_business", args=[self.business.public_id])
        self.login(self.analyst)

    def test_no_consent_no_access_and_denial_is_audited(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "Alpha Shop", status_code=403)
        self.assertEqual(AccessReceipt.objects.count(), 0)
        denied = AuditEvent.objects.get(action="bank.access", outcome="denied")
        self.assertEqual((denied.institution, denied.detail["reason"]), (self.mfi, "no_consent"))
        self.assertNotContains(self.client.get(reverse("bank_home")), "Alpha Shop")

    def test_active_consent_allows_access_and_issues_receipt(self):
        consent = self.consent()
        response = self.client.get(self.url)
        self.assertContains(response, "Alpha Shop")
        self.assertContains(response, "This report provides financial evidence and is not a credit decision.")
        receipt = AccessReceipt.objects.get()
        self.assertEqual((receipt.institution, receipt.consent, receipt.requester_user, receipt.channel),
                         (self.mfi, consent, self.analyst, "portal"))
        self.assertIsNotNone(receipt.snapshot)
        self.assertEqual(receipt.calculation_version, receipt.snapshot.calculation_version.version)
        self.assertContains(self.client.get(reverse("bank_home")), "Alpha Shop")
        # The merchant sees the receipt.
        self.login(self.owner)
        self.assertContains(self.client.get(reverse("sharing")), receipt.reference)

    def test_every_access_issues_a_receipt(self):
        self.consent()
        self.client.get(self.url)
        self.client.get(self.url)
        self.client.get(reverse("bank_pdf", args=[self.business.public_id]))
        self.assertEqual(AccessReceipt.objects.count(), 3)
        self.assertEqual(AccessReceipt.objects.filter(channel="pdf").count(), 1)

    def test_revoked_consent_blocks_immediately(self):
        consent = self.consent()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.login(self.owner)
        response = self.client.post(reverse("consent_revoke", args=[consent.public_id]), {"understood": "on"})
        self.assertEqual(response.status_code, 302)
        consent.refresh_from_db()
        self.assertIsNotNone(consent.revoked_at)
        self.login(self.analyst)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(reverse("bank_pdf", args=[self.business.public_id])).status_code, 403)
        self.assertEqual(AuditEvent.objects.filter(outcome="denied").last().detail["reason"], "consent_inactive")

    def test_expired_consent_blocks(self):
        consent = self.consent()
        ConsentGrant.objects.filter(pk=consent.pk).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_only_consented_categories_are_shown(self):
        self.consent(categories=[DataCategory.PROFILE, DataCategory.FINANCIAL_SUMMARY])
        response = self.client.get(self.url)
        self.assertContains(response, "Monthly cash movement")
        self.assertNotContains(response, "Evidence breakdown")
        self.assertNotContains(response, "Data-quality indicators")
        snapshot = AccessReceipt.objects.get().snapshot
        self.assertNotIn("evidence", snapshot.data)
        self.assertNotIn("data_quality", snapshot.data)
        # PDF wasn't authorised.
        self.assertEqual(self.client.get(reverse("bank_pdf", args=[self.business.public_id])).status_code, 403)

    def test_other_institution_cannot_use_this_consent(self):
        self.consent()
        self.login(self.other_analyst)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_merchants_cannot_open_the_analyst_portal(self):
        self.consent()
        self.login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 403)


class ConsentWorkflowTests(Fixtures):
    def form(self, **extra):
        return {"institution": self.mfi.pk, "purpose": "financing_application", "categories": ["profile", "financial_summary"],
                "period_from": add_months(this_month(), -5).strftime("%Y-%m"), "period_to": this_month().strftime("%Y-%m"),
                "expires_on": (timezone.localdate() + dt.timedelta(days=30)).isoformat(), **extra}

    def test_owner_reviews_scope_before_granting(self):
        self.login(self.owner)
        response = self.client.post(reverse("consent_new"), self.form())
        self.assertContains(response, "Review before you authorise")
        self.assertContains(response, "can't retrieve")
        self.assertEqual(ConsentGrant.objects.count(), 0)
        response = self.client.post(reverse("consent_new"), self.form(confirm="yes"))
        consent = ConsentGrant.objects.get()
        self.assertEqual(consent.categories, ["profile", "financial_summary"])
        self.assertEqual(consent.granted_by, self.owner)
        self.assertTrue(AuditEvent.objects.filter(action="consent.granted").exists())

    def test_consent_validation(self):
        self.login(self.owner)
        too_long = (timezone.localdate() + dt.timedelta(days=400)).isoformat()
        self.client.post(reverse("consent_new"), self.form(expires_on=too_long, confirm="yes"))
        self.client.post(reverse("consent_new"), self.form(period_to="2099-01", confirm="yes"))
        self.assertEqual(ConsentGrant.objects.count(), 0)

    def test_staff_cannot_authorise_sharing(self):
        staff = self.make_staff(["record", "reports"])
        self.login(staff)
        self.assertEqual(self.client.get(reverse("consent_new")).status_code, 403)
        self.assertEqual(self.client.post(reverse("consent_new"), self.form(confirm="yes")).status_code, 403)
        consent = self.consent()
        self.assertEqual(self.client.post(reverse("consent_revoke", args=[consent.public_id]), {"understood": "on"}).status_code, 403)
        self.assertEqual(ConsentGrant.objects.count(), 1)

    def make_staff(self, permissions):
        from accounts.models import User
        from merchants.models import BusinessMembership

        staff = User.objects.create_user("staff@a.test", "pw-123456789x")
        BusinessMembership.objects.create(user=staff, business=self.business, role="staff", permissions=permissions)
        return staff
