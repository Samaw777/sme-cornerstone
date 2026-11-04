import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Sector(models.TextChoices):
    RETAIL = "retail", _("Retail and shops")
    FOOD = "food", _("Food, cafés and restaurants")
    AGRI = "agriculture", _("Agriculture and agro-processing")
    COFFEE = "coffee", _("Coffee processing and trade")
    MANUFACTURING = "manufacturing", _("Manufacturing and workshops")
    TEXTILES = "textiles", _("Textiles, tailoring and garments")
    CONSTRUCTION = "construction", _("Construction and building materials")
    TRANSPORT = "transport", _("Transport and logistics")
    SERVICES = "services", _("Professional and personal services")
    HOSPITALITY = "hospitality", _("Hospitality and tourism")
    OTHER = "other", _("Other")


class Region(models.TextChoices):
    ADDIS_ABABA = "addis_ababa", _("Addis Ababa")
    DIRE_DAWA = "dire_dawa", _("Dire Dawa")
    AFAR = "afar", _("Afar")
    AMHARA = "amhara", _("Amhara")
    BENISHANGUL = "benishangul_gumuz", _("Benishangul-Gumuz")
    CENTRAL = "central_ethiopia", _("Central Ethiopia")
    GAMBELA = "gambela", _("Gambela")
    HARARI = "harari", _("Harari")
    OROMIA = "oromia", _("Oromia")
    SIDAMA = "sidama", _("Sidama")
    SOMALI = "somali", _("Somali")
    SOUTH = "south_ethiopia", _("South Ethiopia")
    SOUTH_WEST = "south_west_ethiopia", _("South West Ethiopia Peoples'")
    TIGRAY = "tigray", _("Tigray")


class IdentityStatus(models.TextChoices):
    """Who stands behind an identity field. Merchant-provided values are shown as
    such until someone independently checks them."""

    NOT_PROVIDED = "not_provided", _("Not provided")
    MERCHANT_PROVIDED = "merchant_provided", _("Merchant-provided")
    PLATFORM_VALIDATED = "platform_validated", _("Validated by platform against a document")


class Business(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(_("business name"), max_length=160)
    legal_name = models.CharField(_("registered legal name"), max_length=200, blank=True)
    sector = models.CharField(_("sector"), max_length=30, choices=Sector.choices)
    operating_start_date = models.DateField(_("operating since"))
    region = models.CharField(_("region"), max_length=30, choices=Region.choices)
    city = models.CharField(_("city / town"), max_length=80)
    sub_city = models.CharField(_("sub-city / woreda"), max_length=80, blank=True)
    phone = models.CharField(_("business phone"), max_length=30, blank=True)

    # Optional registration information. The status fields record whether a
    # value is merely provided by the merchant or independently validated.
    tin = models.CharField(_("TIN"), max_length=20, blank=True)
    tin_status = models.CharField(max_length=20, choices=IdentityStatus.choices, default=IdentityStatus.NOT_PROVIDED)
    trade_license_number = models.CharField(_("trade licence number"), max_length=40, blank=True)
    trade_license_status = models.CharField(max_length=20, choices=IdentityStatus.choices, default=IdentityStatus.NOT_PROVIDED)
    identity_validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    identity_validated_at = models.DateTimeField(null=True, blank=True)
    identity_validation_note = models.CharField(max_length=300, blank=True)

    # Synthetic demo businesses are flagged so they're never mistaken for real ones.
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "businesses"

    def __str__(self):
        return self.name

    def refresh_identity_statuses(self):
        """New or changed values entered by the merchant are merchant-provided."""
        for field, status_field in (("tin", "tin_status"), ("trade_license_number", "trade_license_status")):
            value = getattr(self, field)
            status = getattr(self, status_field)
            if not value:
                setattr(self, status_field, IdentityStatus.NOT_PROVIDED)
            elif status == IdentityStatus.NOT_PROVIDED:
                setattr(self, status_field, IdentityStatus.MERCHANT_PROVIDED)


class Permission(models.TextChoices):
    RECORD = "record", _("Record transactions")
    IMPORT = "import", _("Import CSV files")
    DOCUMENTS = "documents", _("Upload and link documents")
    RECEIVABLES = "receivables", _("Manage invoices and obligations")
    REPORTS = "reports", _("View dashboard and passport")
    APPROVE = "approve", _("Approve entries made by others")
    # Only for owners; listed so checks read uniformly.
    MANAGE = "manage", _("Manage business, staff and connected sources")
    SHARE = "share", _("Authorise sharing with banks/MFIs")


STAFF_GRANTABLE = [Permission.RECORD, Permission.IMPORT, Permission.DOCUMENTS, Permission.RECEIVABLES, Permission.REPORTS,
                   Permission.APPROVE]


class BusinessMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", _("Owner")
        STAFF = "staff", _("Staff")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="business_memberships")
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices)
    # Staff permissions; owners implicitly hold every permission.
    permissions = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "business"], name="one_membership_per_business")]

    def __str__(self):
        return f"{self.user} · {self.business} ({self.role})"

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    def has(self, permission):
        if not self.is_active:
            return False
        if self.is_owner:
            return True
        # Sharing consent and business management are never delegated to staff.
        return permission in STAFF_GRANTABLE and permission in self.permissions

    @property
    def can_approve(self):
        return self.has(Permission.APPROVE)

    def permission_labels(self):
        if self.is_owner:
            return [_("All permissions")]
        return [Permission(p).label for p in self.permissions if p in Permission.values]
