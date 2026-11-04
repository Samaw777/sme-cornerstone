import datetime as dt
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from core.periods import add_months, month_end, month_start, today
from ledger import importing, services
from ledger.models import ImportBatch
from merchants.models import Business, BusinessMembership
from sharing.models import ConsentGrant, DataCategory, FinancialInstitution, InstitutionMembership

PASSWORD = "a-strong-test-password"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def this_month():
    return month_start(today())


def months_ago(n, day=5):
    """A date in the month n months before this one (day clamped for safety)."""
    return add_months(this_month(), -n) + dt.timedelta(days=day - 1)


class Fixtures(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("owner@a.test", PASSWORD, name="Owner A")
        self.business = self.make_business("Alpha Shop", self.owner)
        self.other_owner = User.objects.create_user("owner@b.test", PASSWORD, name="Owner B")
        self.other = self.make_business("Beta Shop", self.other_owner)
        self.mfi = FinancialInstitution.objects.create(name="Test MFI", kind="mfi")
        self.bank = FinancialInstitution.objects.create(name="Other Bank", kind="bank")
        self.analyst = User.objects.create_user("analyst@mfi.test", PASSWORD)
        InstitutionMembership.objects.create(user=self.analyst, institution=self.mfi)
        self.other_analyst = User.objects.create_user("analyst@bank.test", PASSWORD)
        InstitutionMembership.objects.create(user=self.other_analyst, institution=self.bank)

    def make_business(self, name, owner):
        business = Business.objects.create(name=name, sector="retail", operating_start_date=dt.date(2020, 1, 1),
                                           region="addis_ababa", city="Addis Ababa")
        BusinessMembership.objects.create(user=owner, business=business, role="owner")
        return business

    def record(self, direction, category, amount, day, business=None, user=None, **extra):
        return services.record_manual(business or self.business, user or self.owner, {
            "direction": direction, "category": category, "amount": Decimal(amount), "currency": "ETB",
            "occurred_on": day, "description": extra.get("description", ""), "counterparty": extra.get("counterparty", ""),
            "reference": extra.get("reference", ""),
        })

    def import_csv(self, text, mapping=None, business=None, include_repeats=False, name="file.csv"):
        upload = SimpleUploadedFile(name, text.encode(), content_type="text/csv")
        raw, digest = importing.read_upload(upload)
        headers, _ = importing.parse(raw)
        batch = ImportBatch.objects.create(business=business or self.business, uploaded_by=self.owner, filename=name,
                                           file_sha256=digest, raw_text=raw, status=ImportBatch.Status.MAPPED,
                                           mapping=mapping or {**importing.guess_mapping(headers), "date_format": "iso"})
        return batch, importing.preview(batch)

    def consent(self, business=None, institution=None, categories=None, months=12, expires_in_days=30, **extra):
        start = add_months(this_month(), -(months - 1))
        return ConsentGrant.objects.create(
            business=business or self.business, institution=institution or self.mfi, purpose="financing_application",
            categories=categories or [c.value for c in DataCategory], period_start=start, period_end=month_end(today()),
            expires_at=timezone.now() + dt.timedelta(days=expires_in_days), granted_by=self.owner, **extra,
        )

    def login(self, user):
        self.client.force_login(user)
