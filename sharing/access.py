"""Consent enforcement for every bank/MFI request (portal, PDF and API).

`authorize` is the single gate: it checks the institution, an active consent
covering every requested category and the whole requested period, and records
an audit event for allowed and denied attempts. Allowed requests then get an
access receipt via `issue_receipt`."""

from dataclasses import dataclass

from django.utils import timezone

from core import audit
from core.models import AuditEvent
from core.periods import month_end, month_start

from .models import AccessReceipt, ConsentGrant


class AccessDenied(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Authorization:
    consent: ConsentGrant
    institution: object
    business: object
    categories: list
    period_start: object
    period_end: object


DENIAL_MESSAGES = {
    "institution_inactive": "This institution's access is suspended.",
    "no_consent": "The business has not authorised this institution.",
    "consent_inactive": "The business's authorisation has been revoked or has expired.",
    "category_not_permitted": "The business has not authorised this data category.",
    "period_out_of_scope": "The requested period is outside the authorised reporting period.",
}


def authorize(*, institution, business, categories, period_start=None, period_end=None, request=None,
              user=None, credential=None, resource=""):
    now = timezone.now()
    actor = credential.key_id if credential else (user.email if user else "")

    def deny(code):
        audit.record("bank.access", request=request, user=user, actor_label=actor, outcome=AuditEvent.Outcome.DENIED,
                     business=business, institution=institution, reason=code, resource=resource,
                     categories=list(categories))
        raise AccessDenied(code, DENIAL_MESSAGES[code])

    if not institution.is_active:
        deny("institution_inactive")
    consents = list(ConsentGrant.objects.filter(institution=institution, business=business))
    if not consents:
        deny("no_consent")
    active = [c for c in consents if c.is_active(now)]
    if not active:
        deny("consent_inactive")
    with_categories = [c for c in active if set(categories) <= set(c.categories)]
    if not with_categories:
        deny("category_not_permitted")
    chosen = None
    for consent in with_categories:
        start = month_start(period_start) if period_start else consent.period_start
        end = month_end(period_end) if period_end else consent.period_end
        if consent.period_start <= start and end <= consent.period_end and start <= end:
            chosen = (consent, start, end)
            break
    if chosen is None:
        deny("period_out_of_scope")
    consent, start, end = chosen
    audit.record("bank.access", request=request, user=user, actor_label=actor, business=business, institution=institution,
                 consent=consent.reference, resource=resource, categories=list(categories))
    return Authorization(consent, institution, business, list(categories), start, end)


def issue_receipt(auth, *, channel, resource, user=None, credential=None, snapshot=None, calculation_version=""):
    if user is not None:
        label = f"{user.display_name} <{user.email}>"
    else:
        label = f"API credential {credential.label} ({credential.key_id})"
    return AccessReceipt.objects.create(
        institution=auth.institution, business=auth.business, consent=auth.consent,
        requester_user=user, requester_credential=credential, requester_label=label,
        channel=channel, resource=resource, categories=auth.categories,
        period_start=auth.period_start, period_end=auth.period_end, snapshot=snapshot,
        calculation_version=calculation_version or (snapshot.calculation_version.version if snapshot else ""),
    )


def authorized_businesses(institution, now=None):
    """Businesses with at least one active consent for this institution."""
    now = now or timezone.now()
    consents = ConsentGrant.objects.filter(institution=institution, revoked_at__isnull=True, expires_at__gt=now)
    return consents.select_related("business").order_by("business__name")


@dataclass
class ConsentStatus:
    status: str
    consent: ConsentGrant


def consent_status(institution, business):
    consents = list(ConsentGrant.objects.filter(institution=institution, business=business))
    if not consents:
        return None
    active = [c for c in consents if c.is_active()]
    current = active[0] if active else consents[0]
    return ConsentStatus(current.status(), current)
