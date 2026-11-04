from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.models import AuditEvent
from merchants.access import merchant_view
from merchants.models import Business, Permission
from passport import pdf, services as passport_services
from pilot.models import SupportAccess

from .access import AccessDenied, authorize, authorized_businesses, issue_receipt
from .forms import ConsentForm, RevokeForm
from .models import CATEGORY_HELP, AccessReceipt, ConsentGrant, DataCategory
from .portal import analyst_view

# ---------- Merchant: consent and access history ----------


@merchant_view(Permission.REPORTS)
def sharing_home(request):
    business = request.business
    consents = ConsentGrant.objects.filter(business=business).select_related("institution", "granted_by", "revoked_by")
    receipts = AccessReceipt.objects.filter(business=business).select_related("institution", "consent", "snapshot")
    denied = AuditEvent.objects.filter(business=business, action="bank.access", outcome=AuditEvent.Outcome.DENIED).select_related("institution")[:20]
    return render(request, "sharing/home.html", {
        "consents": consents,
        "receipts": Paginator(receipts, 25).get_page(request.GET.get("page")),
        "denied": denied,
        "support": SupportAccess.objects.filter(business=business).select_related("admin")[:20],
        "can_share": request.membership.has(Permission.SHARE),
        "now": timezone.now(),
    })


@merchant_view(Permission.SHARE)
@require_http_methods(["GET", "POST"])
def consent_new(request):
    form = ConsentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if request.POST.get("confirm") != "yes":
            # Review step: show the exact scope before anything is granted.
            return render(request, "sharing/consent_review.html", {
                "form": form, "data": data, "category_labels": [DataCategory(c).label for c in data["categories"]],
                "category_help": CATEGORY_HELP, "post": request.POST,
            })
        with db_transaction.atomic():
            consent = ConsentGrant.objects.create(
                business=request.business, institution=data["institution"], purpose=data["purpose"],
                purpose_detail=data["purpose_detail"], categories=data["categories"], period_start=data["period_start"],
                period_end=data["period_end"], expires_at=data["expires_at"], granted_by=request.user,
            )
            audit.record("consent.granted", request=request, business=request.business, institution=consent.institution,
                         consent=consent.reference, categories=consent.categories,
                         period=f"{consent.period_start}..{consent.period_end}", expires=consent.expires_at.isoformat())
        messages.success(request, f"Access granted to {consent.institution.name} until {consent.expires_at:%d %b %Y}. Reference {consent.reference}.")
        return redirect("consent_detail", consent.public_id)
    return render(request, "sharing/consent_form.html", {"form": form, "category_help": CATEGORY_HELP})


def _consent(request, public_id):
    return get_object_or_404(ConsentGrant.objects.select_related("institution", "granted_by", "revoked_by"),
                             business=request.business, public_id=public_id)


@merchant_view(Permission.REPORTS)
def consent_detail(request, public_id):
    consent = _consent(request, public_id)
    return render(request, "sharing/consent_detail.html", {
        "consent": consent, "receipts": consent.receipts.select_related("snapshot")[:50], "revoke_form": RevokeForm(),
        "can_share": request.membership.has(Permission.SHARE), "category_help": CATEGORY_HELP,
    })


@merchant_view(Permission.SHARE)
@require_POST
def consent_revoke(request, public_id):
    consent = _consent(request, public_id)
    form = RevokeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Tick the box to confirm you understand what revoking does.")
        return redirect("consent_detail", consent.public_id)
    if consent.revoked_at is None:
        consent.revoked_at = timezone.now()
        consent.revoked_by = request.user
        consent.revocation_reason = form.cleaned_data["reason"]
        consent.save(update_fields=["revoked_at", "revoked_by", "revocation_reason"])
        audit.record("consent.revoked", request=request, business=request.business, institution=consent.institution,
                     consent=consent.reference)
        messages.success(request, f"Access for {consent.institution.name} revoked. Future requests are blocked from now on.")
    return redirect("consent_detail", consent.public_id)


# ---------- Bank/MFI analyst portal ----------

VIEWABLE = [c for c in DataCategory.values if c != DataCategory.PASSPORT_PDF]


@analyst_view
def bank_home(request):
    consents = authorized_businesses(request.institution)
    return render(request, "sharing/bank_home.html", {"consents": consents, "institution": request.institution})


def _authorize_portal(request, public_id, categories, resource):
    # A business id that the institution has no consent for gets the same denial
    # as any other: the check (and its audit event) happens before anything is shown.
    business = Business.objects.filter(public_id=public_id).first()
    if business is None:
        audit.record("bank.access", request=request, outcome=AuditEvent.Outcome.DENIED, institution=request.institution,
                     reason="unknown_business", resource=resource)
        raise PermissionDenied("The business has not authorised your institution.")
    try:
        return authorize(institution=request.institution, business=business, categories=categories, request=request,
                         user=request.user, resource=resource)
    except AccessDenied as exc:
        raise PermissionDenied(exc.message)


@analyst_view
def bank_business(request, public_id):
    auth = _authorize_portal(request, public_id, [], "portal:passport")
    categories = [c for c in auth.consent.categories if c in VIEWABLE]
    if not categories:
        raise PermissionDenied("The authorisation doesn't include any viewable data.")
    auth.categories = categories
    snapshot = passport_services.generate(auth.business, None, None, consent=auth.consent)
    receipt = issue_receipt(auth, channel=AccessReceipt.Channel.PORTAL, resource="passport_view", user=request.user, snapshot=snapshot)
    return render(request, "sharing/bank_business.html", {
        "consent": auth.consent, "business": auth.business, "snapshot": snapshot, "data": snapshot.data, "receipt": receipt,
        "can_pdf": DataCategory.PASSPORT_PDF in auth.consent.categories,
    })


@analyst_view
def bank_pdf(request, public_id):
    auth = _authorize_portal(request, public_id, [DataCategory.PASSPORT_PDF], "portal:pdf")
    snapshot = passport_services.generate(auth.business, None, None, consent=auth.consent)
    issue_receipt(auth, channel=AccessReceipt.Channel.PDF, resource="passport_pdf", user=request.user, snapshot=snapshot)
    response = HttpResponse(pdf.render(snapshot), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="passport-{auth.consent.reference}-v{snapshot.version}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response


@analyst_view
def bank_receipts(request):
    receipts = AccessReceipt.objects.filter(institution=request.institution).select_related("business", "consent", "snapshot")
    return render(request, "sharing/bank_receipts.html", {
        "receipts": Paginator(receipts, 30).get_page(request.GET.get("page")), "institution": request.institution,
    })
