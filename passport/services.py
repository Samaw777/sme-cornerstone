from django.db import transaction as db_transaction
from django.db.models import Max

from core import audit

from . import metrics
from .models import CalculationVersion, PassportSnapshot


def calculation_version():
    version, _ = CalculationVersion.objects.get_or_create(
        version=metrics.CALCULATION_VERSION,
        defaults={"methods": [{"metric": m, "method": d} for m, d in metrics.METHODS]},
    )
    return version


def sharing_block(consent):
    if consent is None:
        return None
    return {
        "recipient": consent.institution.name,
        "purpose": consent.get_purpose_display(),
        "purpose_detail": consent.purpose_detail or None,
        "consent_reference": consent.reference,
        "categories": consent.categories,
        "consent_expires_at": consent.expires_at.isoformat(),
        "granted_at": consent.granted_at.isoformat(),
    }


@db_transaction.atomic
def generate(business, start, end, *, user=None, consent=None, categories=None):
    """Compute and freeze a passport. For a consent, scope defaults to the
    consent's; an identical earlier snapshot under the same consent is reused so
    the report version stays stable while nothing changes."""
    if consent is not None:
        # Callers pass a narrower scope only after sharing.access.authorize() checked it.
        start = start or consent.period_start
        end = end or consent.period_end
        categories = categories or consent.categories
    # Anything generated under a consent goes to an institution: aggregates and metrics only.
    data = metrics.compute(business, start, end, categories, sharing=sharing_block(consent),
                           audience="merchant" if consent is None else "institution")
    digest = metrics.content_hash(data)
    if consent is not None:
        same = PassportSnapshot.objects.filter(business=business, consent=consent, content_sha256=digest).first()
        if same:
            return same
    # Lock the business row so concurrent generations get distinct version numbers.
    type(business).objects.select_for_update().filter(pk=business.pk).first()
    next_version = (PassportSnapshot.objects.filter(business=business).aggregate(v=Max("version"))["v"] or 0) + 1
    snapshot = PassportSnapshot.objects.create(
        business=business, version=next_version, generated_by=user, consent=consent,
        period_start=data["period"]["start"], period_end=data["period"]["end"], categories=data["sections"],
        calculation_version=calculation_version(), data=data, content_sha256=digest,
    )
    audit.record("passport.generated", user=user, actor_label="" if user else "system", business=business,
                 snapshot=str(snapshot.public_id), version=next_version, consent=consent.reference if consent else None)
    return snapshot
