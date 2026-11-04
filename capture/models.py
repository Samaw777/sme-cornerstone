import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from ledger.models import MONEY, Category, Direction, PaymentMethod


class QuickTemplate(models.Model):
    """A favourite for one-tap logging ("Diesel", "Daily cash sales")."""

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="quick_templates")
    label = models.CharField(_("name"), max_length=40)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    category = models.CharField(max_length=20, choices=Category.choices)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, blank=True)
    counterparty = models.CharField(max_length=120, blank=True)
    description = models.CharField(max_length=200, blank=True)
    default_amount = models.DecimalField(**MONEY, null=True, blank=True)
    uses = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uses", "label"]
        constraints = [models.UniqueConstraint(fields=["business", "label"], name="template_label_unique")]

    def __str__(self):
        return self.label


class TelegramLink(models.Model):
    """A Telegram chat that may log for one business membership."""

    membership = models.OneToOneField("merchants.BusinessMembership", on_delete=models.CASCADE, related_name="telegram")
    chat_id = models.BigIntegerField(unique=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    linked_at = models.DateTimeField(auto_now_add=True)


class TelegramLinkCode(models.Model):
    membership = models.ForeignKey("merchants.BusinessMembership", on_delete=models.CASCADE, related_name="+")
    code = models.CharField(max_length=12, unique=True)
    expires_at = models.DateTimeField()

    @classmethod
    def new(cls, membership, expires_at):
        cls.objects.filter(membership=membership).delete()
        return cls.objects.create(membership=membership, code=secrets.token_hex(4).upper(), expires_at=expires_at)
