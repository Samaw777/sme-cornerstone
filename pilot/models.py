from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PilotProgram(models.Model):
    """The one bank/MFI pilot this MVP is configured for."""

    name = models.CharField(max_length=120)
    institution = models.ForeignKey("sharing.FinancialInstitution", on_delete=models.PROTECT, related_name="pilots")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    target_merchants = models.PositiveIntegerField(null=True, blank=True)
    evaluation_design = models.TextField(blank=True, help_text=_("How outcomes will be compared with the institution's normal process."))
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class PilotMetricEntry(models.Model):
    """A manually entered evaluation figure, for things the platform can't measure
    itself (e.g. analyst review time). Each entry names its source."""

    class Metric(models.TextChoices):
        REVIEW_MINUTES = "review_minutes", _("Analyst review time per application (minutes)")
        BASELINE_REVIEW_MINUTES = "baseline_review_minutes", _("Baseline review time without passport (minutes)")
        DOCUMENT_REQUESTS = "document_requests", _("Follow-up document requests per application")
        DISCREPANCIES = "discrepancies", _("Discrepancies found by analyst")
        APPLICATIONS_REVIEWED = "applications_reviewed", _("Applications reviewed with a passport")
        OTHER = "other", _("Other (describe in notes)")

    pilot = models.ForeignKey(PilotProgram, on_delete=models.CASCADE, related_name="metrics")
    metric = models.CharField(max_length=30, choices=Metric.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    period_start = models.DateField()
    period_end = models.DateField()
    source = models.CharField(max_length=200, help_text=_("Where this figure came from, e.g. 'MFI review log, week 3'."))
    notes = models.CharField(max_length=300, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entered_at"]


class PilotFeedback(models.Model):
    class Role(models.TextChoices):
        MERCHANT = "merchant", _("Merchant")
        ANALYST = "analyst", _("Bank/MFI analyst")

    pilot = models.ForeignKey(PilotProgram, on_delete=models.CASCADE, related_name="feedback")
    role = models.CharField(max_length=10, choices=Role.choices)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)], help_text=_("1 = not useful, 5 = very useful"))
    comment = models.TextField(blank=True)
    collected_on = models.DateField(default=timezone.localdate)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-collected_on"]


class OperationalIssue(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        RESOLVED = "resolved", _("Resolved")

    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    business = models.ForeignKey("merchants.Business", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    institution = models.ForeignKey("sharing.FinancialInstitution", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-created_at"]


class SupportAccess(models.Model):
    """Time-limited, reasoned access by a platform administrator to one business's
    records. Every grant is audited and shown to the business."""

    admin = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="support_accesses")
    reason = models.CharField(max_length=300)
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-started_at"]

    def is_active(self):
        return self.expires_at > timezone.now()
