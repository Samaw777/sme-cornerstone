import datetime as dt
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import ImmutableModel


class DataCategory(models.TextChoices):
    PROFILE = "profile", _("Business profile and operating history")
    FINANCIAL_SUMMARY = "financial_summary", _("Monthly revenue, expenses and cash movement")
    VERIFICATION_SUMMARY = "verification_summary", _("Evidence and verification breakdown")
    DATA_QUALITY = "data_quality", _("Data-quality indicators")
    RECEIVABLES_OBLIGATIONS = "receivables_obligations", _("Recorded receivables and obligations")
    CREDIT_METRICS = "credit_metrics", _("Credit-relevant metrics")
    PASSPORT_PDF = "passport_pdf", _("Download of the passport PDF")


CATEGORY_HELP = {
    DataCategory.PROFILE: _("Name, sector, location, operating since, months of recorded history, and registration details with their validation status."),
    DataCategory.FINANCIAL_SUMMARY: _("Monthly totals only — never individual transactions."),
    DataCategory.VERIFICATION_SUMMARY: _("How much of the recorded activity is system-verified, evidence-supported or self-reported."),
    DataCategory.DATA_QUALITY: _("Missing months, reconciliation differences, unusual changes and similar neutral indicators."),
    DataCategory.RECEIVABLES_OBLIGATIONS: _("Outstanding invoice totals and recurring obligations you have recorded."),
    DataCategory.CREDIT_METRICS: _("Ratios such as operating margin, debt service coverage, revenue stability and receivable days, plus evidence-strength and data-completeness scores. Not a credit score."),
    DataCategory.PASSPORT_PDF: _("Lets the institution download a copy. A downloaded copy can't be recalled by revoking consent."),
}


class FinancialInstitution(models.Model):
    class Kind(models.TextChoices):
        BANK = "bank", _("Bank")
        MFI = "mfi", _("Microfinance institution")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=160, unique=True)
    kind = models.CharField(max_length=5, choices=Kind.choices)
    is_pilot_partner = models.BooleanField(default=False)
    is_demo = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    contact_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class InstitutionMembership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="institution_memberships")
    institution = models.ForeignKey(FinancialInstitution, on_delete=models.CASCADE, related_name="memberships")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "institution"], name="one_analyst_membership")]


class PartnerCredential(models.Model):
    """An institution-scoped API credential. Only a hash of the secret is stored;
    the secret is shown once when created. Scopes limit which endpoints it may call."""

    class Scope(models.TextChoices):
        PROFILE = "profile:read", _("Business profile")
        FINANCIAL = "financial_summary:read", _("Financial summary")
        VERIFICATION = "verification_summary:read", _("Verification summary")
        QUALITY = "data_quality:read", _("Data-quality summary")
        CONSENT = "consent:read", _("Consent status")
        RECEIPTS = "receipts:read", _("Audit receipts")
        CREDIT = "credit_metrics:read", _("Credit-relevant metrics")

    institution = models.ForeignKey(FinancialInstitution, on_delete=models.CASCADE, related_name="credentials")
    label = models.CharField(max_length=80)
    key_id = models.CharField(max_length=24, unique=True)
    secret_hash = models.CharField(max_length=200)
    scopes = models.JSONField(default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.institution} · {self.label} ({self.key_id})"

    @classmethod
    def issue(cls, institution, label, scopes, created_by=None, days=90, secret=None, key_id=None):
        """Create a credential and return (credential, token). The token is
        `<key_id>.<secret>` and is never stored."""
        key_id = key_id or "pk_" + secrets.token_hex(8)
        secret = secret or secrets.token_urlsafe(32)
        credential = cls.objects.create(
            institution=institution, label=label, key_id=key_id, secret_hash=make_password(secret),
            scopes=list(scopes), created_by=created_by, expires_at=timezone.now() + dt.timedelta(days=days),
        )
        return credential, f"{key_id}.{secret}"

    def check_secret(self, secret):
        return check_password(secret, self.secret_hash)

    def is_usable(self, now=None):
        now = now or timezone.now()
        return self.revoked_at is None and self.expires_at > now and self.institution.is_active


class ConsentGrant(models.Model):
    """The owner's explicit authorisation for one institution, purpose, set of
    data categories and reporting period, until an expiry date. The scope is
    fixed once granted: to change it, revoke and grant again."""

    class Purpose(models.TextChoices):
        FINANCING_APPLICATION = "financing_application", _("Supporting a financing application")
        ACCOUNT_REVIEW = "account_review", _("Existing customer relationship review")
        PILOT_EVALUATION = "pilot_evaluation", _("Pilot evaluation of the passport")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    reference = models.CharField(max_length=20, unique=True)
    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="consents")
    institution = models.ForeignKey(FinancialInstitution, on_delete=models.PROTECT, related_name="consents")
    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    purpose_detail = models.CharField(max_length=300, blank=True)
    categories = models.JSONField()
    period_start = models.DateField()
    period_end = models.DateField()
    expires_at = models.DateTimeField()
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    revocation_reason = models.CharField(max_length=300, blank=True)
    is_demo = models.BooleanField(default=False)

    class Meta:
        ordering = ["-granted_at"]

    def __str__(self):
        return self.reference

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = "CNS-" + secrets.token_hex(4).upper()
        super().save(*args, **kwargs)

    def status(self, now=None):
        now = now or timezone.now()
        if self.revoked_at:
            return "revoked"
        if self.expires_at <= now:
            return "expired"
        return "active"

    def is_active(self, now=None):
        return self.status(now) == "active"

    def category_labels(self):
        return [DataCategory(c).label for c in self.categories if c in DataCategory.values]


class AccessReceipt(ImmutableModel):
    """Proof of one authorised access: who, from which institution, when, what
    scope, which report version, under which consent. Visible to the merchant."""

    class Channel(models.TextChoices):
        PORTAL = "portal", _("Analyst portal")
        PDF = "pdf", _("PDF download")
        API = "api", _("Partner API")

    reference = models.CharField(max_length=20, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    institution = models.ForeignKey(FinancialInstitution, on_delete=models.PROTECT, related_name="receipts")
    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="access_receipts")
    consent = models.ForeignKey(ConsentGrant, on_delete=models.PROTECT, related_name="receipts")
    requester_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requester_credential = models.ForeignKey(PartnerCredential, null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts")
    requester_label = models.CharField(max_length=200)
    channel = models.CharField(max_length=6, choices=Channel.choices)
    resource = models.CharField(max_length=60)
    categories = models.JSONField()
    period_start = models.DateField()
    period_end = models.DateField()
    snapshot = models.ForeignKey("passport.PassportSnapshot", null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    calculation_version = models.CharField(max_length=20)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = "RCPT-" + secrets.token_hex(5).upper()
        super().save(*args, **kwargs)

    def category_labels(self):
        return [DataCategory(c).label for c in self.categories if c in DataCategory.values]
