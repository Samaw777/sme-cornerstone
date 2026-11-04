from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_platform_admin", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """One sign-in for everyone. What a person can see comes from their
    memberships (business owner/staff, bank/MFI analyst) or the platform
    administrator flag — never from this model alone."""

    username = None
    first_name = None
    last_name = None
    email = models.EmailField(_("email address"), unique=True)
    name = models.CharField(_("full name"), max_length=120, blank=True)
    phone = models.CharField(_("phone"), max_length=30, blank=True)
    # Platform administrators manage organisations and the pilot. They do not
    # see merchant financial records unless they open audited support access.
    is_platform_admin = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        return self.name or self.email.split("@")[0]
