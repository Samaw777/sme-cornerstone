from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core import audit
from merchants.access import merchant_view
from merchants.models import Permission
from merchants.views import period_from_request

from . import metrics, pdf, services
from .models import PassportSnapshot


@merchant_view(Permission.REPORTS)
def passport_home(request):
    start, end, preset, errors = period_from_request(request)
    preview = metrics.compute(request.business, start, end)
    snapshots = PassportSnapshot.objects.filter(business=request.business).select_related("consent__institution", "calculation_version")[:20]
    return render(request, "passport/home.html", {
        "data": preview, "preview": True, "preset": preset, "period_errors": errors, "start": start, "end": end,
        "snapshots": snapshots, "can_share": request.membership.has(Permission.SHARE),
    })


@merchant_view(Permission.REPORTS)
@require_POST
def passport_generate(request):
    start, end, _, errors = period_from_request(request)
    snapshot = services.generate(request.business, start, end, user=request.user)
    messages.success(request, f"Passport v{snapshot.version} generated and saved.")
    return redirect("passport_snapshot", snapshot.public_id)


def _snapshot(request, public_id):
    return get_object_or_404(PassportSnapshot.objects.select_related("calculation_version", "consent__institution"),
                             business=request.business, public_id=public_id)


@merchant_view(Permission.REPORTS)
def passport_snapshot(request, public_id):
    snapshot = _snapshot(request, public_id)
    return render(request, "passport/snapshot.html", {"snapshot": snapshot, "data": snapshot.data,
                                                      "receipts": snapshot.receipts.select_related("institution")[:20]})


@merchant_view(Permission.REPORTS)
def passport_pdf(request, public_id):
    snapshot = _snapshot(request, public_id)
    audit.record("passport.pdf_downloaded", request=request, business=request.business, snapshot=str(snapshot.public_id))
    response = HttpResponse(pdf.render(snapshot), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="passport-v{snapshot.version}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response
