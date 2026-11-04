"""Partner API: service authentication, scopes, consent on every request, receipts, rate limits."""

import datetime as dt

from django.test import override_settings
from django.utils import timezone

from core.models import AuditEvent
from sharing.models import AccessReceipt, PartnerCredential

from .helpers import Fixtures, months_ago


class PartnerApiTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.record("in", "sales", "5000", months_ago(1))
        self.credential, self.token = PartnerCredential.issue(self.mfi, "test", [s.value for s in PartnerCredential.Scope])
        self.base = f"/api/v1/businesses/{self.business.public_id}"

    def get(self, path, token=None, **params):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token or self.token}"} if token != "" else {}
        return self.client.get(path, params, **headers)

    def test_requires_valid_credentials(self):
        self.assertEqual(self.get(self.base + "/profile", token="").status_code, 401)
        self.assertEqual(self.get(self.base + "/profile", token=self.credential.key_id + ".wrong").status_code, 401)
        self.assertEqual(self.get(self.base + "/profile", token="garbage").status_code, 401)
        events = AuditEvent.objects.filter(action="api.auth", outcome="denied")
        self.assertEqual(events.count(), 3)
        for event in events:
            self.assertNotIn("wrong", str(event.detail))
            self.assertNotIn(self.token.split(".")[1], str(event.detail))

    def test_secret_is_hashed_not_stored(self):
        secret = self.token.split(".", 1)[1]
        self.assertNotIn(secret, self.credential.secret_hash)
        self.assertTrue(self.credential.check_secret(secret))

    def test_no_consent_is_denied_and_logged(self):
        response = self.get(self.base + "/financial-summary")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "no_consent")
        self.assertEqual(AccessReceipt.objects.count(), 0)
        self.assertTrue(AuditEvent.objects.filter(action="bank.access", outcome="denied", institution=self.mfi).exists())

    def test_unknown_business_looks_like_no_consent(self):
        response = self.get("/api/v1/businesses/00000000-0000-0000-0000-000000000000/profile")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "no_consent"))

    def test_allowed_request_returns_scoped_data_and_a_receipt(self):
        consent = self.consent()
        response = self.get(self.base + "/financial-summary")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("monthly", body)
        self.assertNotIn("evidence", body)
        self.assertNotIn("profile", body)
        self.assertEqual(body["meta"]["consent_reference"], consent.reference)
        self.assertEqual(body["meta"]["disclaimer"], "This report provides financial evidence and is not a credit decision.")
        receipt = AccessReceipt.objects.get(reference=body["meta"]["receipt_reference"])
        self.assertEqual((receipt.channel, receipt.requester_credential), ("api", self.credential))
        self.assertEqual(receipt.snapshot.version, body["meta"]["report_version"])
        for path in ("/profile", "/verification-summary", "/data-quality", "/consent-status"):
            self.assertEqual(self.get(self.base + path).status_code, 200, path)
        self.assertEqual(self.get("/api/v1/receipts").json()["pagination"]["total"], 5)

    def test_category_and_period_are_enforced(self):
        self.consent(categories=["profile"], months=3)
        self.assertEqual(self.get(self.base + "/financial-summary").json()["error"]["code"], "category_not_permitted")
        self.assertEqual(self.get(self.base + "/profile", **{"from": "2001-01"}).json()["error"]["code"], "period_out_of_scope")

    def test_revoked_and_expired_consent_block_future_requests(self):
        consent = self.consent()
        self.assertEqual(self.get(self.base + "/profile").status_code, 200)
        ConsentGrant = type(consent)
        ConsentGrant.objects.filter(pk=consent.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.get(self.base + "/profile").json()["error"]["code"], "consent_inactive")
        self.assertEqual(self.get(self.base + "/consent-status").json()["data"]["status"], "revoked")
        other = self.consent()
        ConsentGrant.objects.filter(pk=other.pk).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
        self.assertEqual(self.get(self.base + "/profile").status_code, 403)

    def test_scopes_limit_endpoints(self):
        self.consent()
        _, token = PartnerCredential.issue(self.mfi, "narrow", [PartnerCredential.Scope.PROFILE])
        self.assertEqual(self.get(self.base + "/profile", token=token).status_code, 200)
        self.assertEqual(self.get(self.base + "/financial-summary", token=token).status_code, 403)

    def test_revoked_or_expired_credentials_fail(self):
        self.consent()
        PartnerCredential.objects.filter(pk=self.credential.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.get(self.base + "/profile").status_code, 401)

    def test_other_institution_credential_cannot_read(self):
        self.consent()
        _, token = PartnerCredential.issue(self.bank, "bank", [s.value for s in PartnerCredential.Scope])
        self.assertEqual(self.get(self.base + "/profile", token=token).status_code, 403)
        self.assertEqual(self.get("/api/v1/businesses", token=token).json()["data"], [])

    def test_input_validation_and_pagination(self):
        self.consent()
        self.assertEqual(self.get(self.base + "/financial-summary", **{"from": "2026-13"}).status_code, 400)
        self.assertEqual(self.get("/api/v1/businesses", page_size="1000").status_code, 400)
        self.assertEqual(self.get("/api/v1/businesses/not-a-uuid/profile").status_code, 400)
        listing = self.get("/api/v1/businesses", page_size="1").json()
        self.assertEqual(listing["pagination"]["total"], 1)
        self.assertEqual(listing["data"][0]["business_id"], str(self.business.public_id))

    @override_settings(PARTNER_API_RATE_LIMIT=3)
    def test_rate_limited(self):
        self.consent()
        codes = [self.get(self.base + "/consent-status").status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])
