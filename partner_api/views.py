"""Partner API v1. Read-only, narrowly scoped, consent-checked on every request.

Every endpoint: authenticates the institution credential, checks its scope,
checks an active consent for the business/category/period, records an audit
event (allowed or denied) and, when allowed, an access receipt."""

import uuid
from functools import wraps

from django.core.paginator import EmptyPage, Paginator
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core import audit
from core.models import AuditEvent
from core.periods import parse_month
from merchants.models import Business
from passport import services as passport_services
from passport.metrics import DISCLAIMER
from sharing.access import AccessDenied, Authorization, authorize, authorized_businesses, consent_status, issue_receipt
from sharing.models import AccessReceipt, DataCategory, PartnerCredential

from .auth import ApiError, authenticate

API_VERSION = "v1"
MAX_PAGE_SIZE = 100


def _json(payload, status=200, headers=None):
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    response["X-API-Version"] = API_VERSION
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def _error(status, code, message, headers=None):
    return _json({"error": {"code": code, "message": message}}, status, headers)


def endpoint(scope):
    def decorator(view):
        @require_GET
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                credential = authenticate(request, scope)
                return view(request, credential, *args, **kwargs)
            except ApiError as exc:
                return _error(exc.status, exc.code, exc.message, exc.headers)

        return wrapped

    return decorator


def _page_params(request):
    try:
        page = int(request.GET.get("page", "1"))
        size = int(request.GET.get("page_size", "25"))
    except ValueError:
        raise ApiError(400, "invalid_parameter", "page and page_size must be integers.")
    if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
        raise ApiError(400, "invalid_parameter", f"page must be ≥ 1 and page_size between 1 and {MAX_PAGE_SIZE}.")
    return page, size


def _paginate(items, request):
    page_number, size = _page_params(request)
    paginator = Paginator(items, size)
    try:
        page = paginator.page(page_number)
    except EmptyPage:
        page = None
    return (list(page.object_list) if page else []), {"page": page_number, "page_size": size, "total": paginator.count,
                                                        "has_next": bool(page and page.has_next())}


def _business(request, credential, business_id):
    try:
        uuid.UUID(str(business_id))
    except ValueError:
        raise ApiError(400, "invalid_parameter", "business_id must be a UUID.")
    business = Business.objects.filter(public_id=business_id).first()
    if business is None:
        audit.record("bank.access", request=request, actor_label=credential.key_id, outcome=AuditEvent.Outcome.DENIED,
                     institution=credential.institution, reason="unknown_business", resource=request.path)
        # Same answer as "no consent", so ids can't be probed.
        raise ApiError(403, "no_consent", "The business has not authorised this institution.")
    return business


def _period(request):
    try:
        start = parse_month(request.GET["from"]) if request.GET.get("from") else None
        end = parse_month(request.GET["to"]) if request.GET.get("to") else None
    except ValueError as exc:
        raise ApiError(400, "invalid_parameter", f"from/to: {exc}")
    if start and end and start > end:
        raise ApiError(400, "invalid_parameter", "'from' is after 'to'.")
    return start, end


def _scoped(request, credential, business_id, category, resource, section_keys):
    business = _business(request, credential, business_id)
    start, end = _period(request)
    try:
        auth = authorize(institution=credential.institution, business=business, categories=[category],
                         period_start=start, period_end=end, request=request, credential=credential, resource=resource)
    except AccessDenied as exc:
        raise ApiError(403, exc.code, exc.message)
    snapshot = passport_services.generate(business, auth.period_start, auth.period_end, consent=auth.consent, categories=[category])
    receipt = issue_receipt(auth, channel=AccessReceipt.Channel.API, resource=resource, credential=credential, snapshot=snapshot)
    data = snapshot.data
    body = {key: data[key] for key in section_keys if key in data}
    body["meta"] = {
        "api_version": API_VERSION,
        "business_id": str(business.public_id),
        "period": data["period"],
        "history": data["history"] if category == DataCategory.PROFILE else None,
        "includes_demo_data": data["includes_demo_data"],
        "calculation_version": data["calculation_version"],
        "report_version": snapshot.version,
        "consent_reference": auth.consent.reference,
        "receipt_reference": receipt.reference,
        "currency": "ETB",
        "disclaimer": DISCLAIMER,
        "limitations": data["limitations"],
    }
    return _json(body)


@endpoint(PartnerCredential.Scope.CONSENT)
def businesses(request, credential):
    consents = authorized_businesses(credential.institution)
    items, page = _paginate(consents, request)
    audit.record("bank.access", request=request, actor_label=credential.key_id, institution=credential.institution,
                 resource="api:businesses", count=len(items))
    return _json({
        "data": [{"business_id": str(c.business.public_id),
                  "name": c.business.name if DataCategory.PROFILE in c.categories else None,
                  "consent_reference": c.reference, "categories": c.categories,
                  "period": {"start": c.period_start.isoformat(), "end": c.period_end.isoformat()},
                  "expires_at": c.expires_at.isoformat(), "is_demo": c.business.is_demo} for c in items],
        "pagination": page, "meta": {"api_version": API_VERSION, "disclaimer": DISCLAIMER},
    })


@endpoint(PartnerCredential.Scope.PROFILE)
def profile(request, credential, business_id):
    return _scoped(request, credential, business_id, DataCategory.PROFILE, "api:profile", ["profile"])


@endpoint(PartnerCredential.Scope.FINANCIAL)
def financial_summary(request, credential, business_id):
    return _scoped(request, credential, business_id, DataCategory.FINANCIAL_SUMMARY, "api:financial_summary",
                   ["monthly", "totals", "trend", "seasonality", "variability"])


@endpoint(PartnerCredential.Scope.VERIFICATION)
def verification_summary(request, credential, business_id):
    return _scoped(request, credential, business_id, DataCategory.VERIFICATION_SUMMARY, "api:verification_summary", ["evidence"])


@endpoint(PartnerCredential.Scope.QUALITY)
def data_quality(request, credential, business_id):
    return _scoped(request, credential, business_id, DataCategory.DATA_QUALITY, "api:data_quality", ["data_quality"])


@endpoint(PartnerCredential.Scope.CONSENT)
def consent(request, credential, business_id):
    business = _business(request, credential, business_id)
    status = consent_status(credential.institution, business)
    if status is None:
        audit.record("bank.access", request=request, actor_label=credential.key_id, outcome=AuditEvent.Outcome.DENIED,
                     institution=credential.institution, business=business, reason="no_consent", resource="api:consent_status")
        return _error(403, "no_consent", "The business has not authorised this institution.")
    c = status.consent
    audit.record("bank.access", request=request, actor_label=credential.key_id, institution=credential.institution,
                 business=business, consent=c.reference, resource="api:consent_status", status=status.status)
    # Consent metadata only (no business data), but still an access: it gets a receipt.
    receipt = issue_receipt(Authorization(c, credential.institution, business, [], c.period_start, c.period_end),
                            channel=AccessReceipt.Channel.API, resource="consent_status", credential=credential,
                            calculation_version="n/a")
    return _json({"data": {
        "business_id": str(business.public_id), "consent_reference": c.reference, "status": status.status,
        "purpose": c.purpose, "categories": c.categories,
        "period": {"start": c.period_start.isoformat(), "end": c.period_end.isoformat()},
        "granted_at": c.granted_at.isoformat(), "expires_at": c.expires_at.isoformat(),
        "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None, "checked_at": timezone.now().isoformat(),
    }, "meta": {"api_version": API_VERSION, "receipt_reference": receipt.reference}})


@endpoint(PartnerCredential.Scope.RECEIPTS)
def receipts(request, credential):
    qs = AccessReceipt.objects.filter(institution=credential.institution).select_related("business", "consent", "snapshot")
    items, page = _paginate(qs, request)
    return _json({"data": [{
        "receipt_reference": r.reference, "created_at": r.created_at.isoformat(), "business_id": str(r.business.public_id),
        "consent_reference": r.consent.reference, "requester": r.requester_label, "channel": r.channel, "resource": r.resource,
        "categories": r.categories, "period": {"start": r.period_start.isoformat(), "end": r.period_end.isoformat()},
        "report_version": r.snapshot.version if r.snapshot else None, "calculation_version": r.calculation_version,
    } for r in items], "pagination": page, "meta": {"api_version": API_VERSION}})
