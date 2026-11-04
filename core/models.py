from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Evidence(models.TextChoices):
    """The four evidence classes used everywhere: UI, passport, PDF and API.

    The first three classify recorded transactions. DERIVED only ever labels a
    calculated metric and is never counted as transaction evidence."""

    SYSTEM_VERIFIED = "system_verified", _("System-verified")
    EVIDENCE_SUPPORTED = "evidence_supported", _("Evidence-supported")
    SELF_REPORTED = "self_reported", _("Self-reported")
    DERIVED = "derived", _("Derived metric")


TRANSACTION_EVIDENCE_CLASSES = [Evidence.SYSTEM_VERIFIED, Evidence.EVIDENCE_SUPPORTED, Evidence.SELF_REPORTED]

EVIDENCE_EXPLANATIONS = {
    Evidence.SYSTEM_VERIFIED: _(
        "Imported from an authorised external source with its source identifier and original event preserved."
    ),
    Evidence.EVIDENCE_SUPPORTED: _(
        "Entered or imported by the business and backed by a linked document. The document's validation status is shown separately."
    ),
    Evidence.SELF_REPORTED: _("Entered or imported by the business without independent verification."),
    Evidence.DERIVED: _("Calculated by the platform from identified records using a documented, versioned method."),
}


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError(f"{self.model.__name__} records are append-only.")

    def delete(self):
        raise TypeError(f"{self.model.__name__} records are append-only.")


class ImmutableModel(models.Model):
    """Append-only records: original source events, corrections, audit events,
    access receipts and passport snapshots. Once saved they can't be edited or
    deleted through the ORM."""

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.pop("_allow_update", False):
            raise TypeError(f"{type(self).__name__} records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError(f"{type(self).__name__} records are append-only.")


class AuditEvent(ImmutableModel):
    """Security-relevant activity: sign-ins to sensitive areas, consent changes,
    every bank/MFI access attempt (allowed or denied), credential changes and
    platform support access. `detail` holds identifiers and reasons only — never
    financial payloads, document contents or secrets."""

    class Outcome(models.TextChoices):
        SUCCESS = "success", _("Allowed")
        DENIED = "denied", _("Denied")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=80, db_index=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.SUCCESS)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=200, blank=True)
    business = models.ForeignKey("merchants.Business", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    institution = models.ForeignKey("sharing.FinancialInstitution", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.outcome}"
