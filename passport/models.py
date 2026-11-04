import uuid

from django.conf import settings
from django.db import models

from core.models import ImmutableModel


class CalculationVersion(models.Model):
    """A released version of the passport calculation methods. Every snapshot
    and data-quality finding records the version that produced it."""

    version = models.CharField(max_length=20, unique=True)
    released_at = models.DateTimeField(auto_now_add=True)
    methods = models.JSONField()

    def __str__(self):
        return self.version


class PassportSnapshot(ImmutableModel):
    """One generated passport, frozen. `data` holds exactly the sections that
    were in scope; a bank-facing snapshot never contains categories its consent
    didn't permit."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="passports")
    version = models.PositiveIntegerField()
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    consent = models.ForeignKey("sharing.ConsentGrant", null=True, blank=True, on_delete=models.PROTECT, related_name="snapshots")
    period_start = models.DateField()
    period_end = models.DateField()
    categories = models.JSONField()
    calculation_version = models.ForeignKey(CalculationVersion, on_delete=models.PROTECT, related_name="snapshots")
    data = models.JSONField()
    content_sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ["-generated_at"]
        constraints = [models.UniqueConstraint(fields=["business", "version"], name="snapshot_version_unique")]

    def __str__(self):
        return f"{self.business} passport v{self.version}"

    @property
    def label(self):
        return f"v{self.version} · {self.calculation_version.version}"
