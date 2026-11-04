from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.periods import add_months, default_period, label, month_end, month_start, parse_month, today
from ledger.models import ConnectedSource, Invoice
from passport import metrics

from .access import SESSION_KEY, memberships_for, merchant_view
from .forms import BusinessForm, StaffForm, StaffPermissionsForm
from .models import BusinessMembership, Permission

User = get_user_model()


@login_required
@require_http_methods(["GET", "POST"])
def onboarding(request):
    form = BusinessForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with db_transaction.atomic():
            business = form.save(commit=False)
            business.created_by = request.user
            business.save()
            BusinessMembership.objects.create(user=request.user, business=business, role=BusinessMembership.Role.OWNER)
        request.session[SESSION_KEY] = str(business.public_id)
        audit.record("business.created", request=request, business=business)
        messages.success(request, f"{business.name} is set up. Add the people who help you keep records, or skip for now.")
        return redirect("staff")
    return render(request, "merchants/onboarding.html", {"form": form, "first": not memberships_for(request.user).exists()})


@login_required
@require_POST
def switch_business(request, public_id):
    membership = get_object_or_404(memberships_for(request.user), business__public_id=public_id)
    request.session[SESSION_KEY] = str(membership.business.public_id)
    return redirect("dashboard")


@merchant_view(Permission.MANAGE)
@require_http_methods(["GET", "POST"])
def business_profile(request):
    form = BusinessForm(request.POST or None, instance=request.business)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.record("business.updated", request=request, business=request.business, fields=form.changed_data)
        messages.success(request, "Business details saved.")
        return redirect("business_profile")
    return render(request, "merchants/profile.html", {"form": form, "business": request.business})


@merchant_view(Permission.MANAGE)
@require_http_methods(["GET", "POST"])
def staff(request):
    business = request.business
    form = StaffForm(request.POST or None)
    setup_link = None
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        user, created = User.objects.get_or_create(email=email, defaults={"name": form.cleaned_data["name"]})
        if created:
            user.set_unusable_password()
            user.save()
        membership, made = BusinessMembership.objects.get_or_create(
            user=user, business=business,
            defaults={"role": BusinessMembership.Role.STAFF, "permissions": form.cleaned_data["permissions"], "added_by": request.user},
        )
        if not made:
            messages.info(request, f"{email} is already a member of {business.name}.")
        else:
            audit.record("staff.added", request=request, business=business, member=user.pk, permissions=membership.permissions)
            if created:
                # No email is required: the owner passes this one-time link on.
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                setup_link = request.build_absolute_uri(reverse("password_reset_confirm", args=[uid, token]))
            messages.success(request, f"{user.display_name} added as staff.")
            form = StaffForm()
    members = business.memberships.select_related("user").order_by("role", "user__email")
    return render(request, "merchants/staff.html", {"form": form, "members": members, "setup_link": setup_link,
                                                    "perm_form": StaffPermissionsForm()})


@merchant_view(Permission.MANAGE)
@require_POST
def staff_update(request, pk):
    membership = get_object_or_404(BusinessMembership, pk=pk, business=request.business, role=BusinessMembership.Role.STAFF)
    if "remove" in request.POST:
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        audit.record("staff.removed", request=request, business=request.business, member=membership.user_id)
        messages.success(request, f"{membership.user.display_name} no longer has access.")
    else:
        form = StaffPermissionsForm(request.POST)
        if form.is_valid():
            membership.permissions = form.cleaned_data["permissions"]
            membership.is_active = True
            membership.save(update_fields=["permissions", "is_active"])
            audit.record("staff.permissions_changed", request=request, business=request.business,
                         member=membership.user_id, permissions=membership.permissions)
            messages.success(request, "Permissions updated.")
    return redirect("staff")


PRESETS = {"3m": 3, "6m": 6, "12m": 12, "24m": 24}


def period_from_request(request, default_months=12):
    """Read ?preset=6m or ?from=2026-01&to=2026-06; fall back to the last N months."""
    errors = []
    preset = request.GET.get("preset")
    if preset in PRESETS:
        start, end = default_period(PRESETS[preset])
        return start, end, preset, errors
    if request.GET.get("from") or request.GET.get("to"):
        try:
            start = parse_month(request.GET.get("from", ""))
            end = month_end(parse_month(request.GET.get("to", "")))
            if start > end:
                raise ValueError("The start month must be before the end month.")
            if (end.year - start.year) * 12 + end.month - start.month + 1 > 60:
                raise ValueError("Choose a period of five years or less.")
            return start, end, "custom", errors
        except ValueError as exc:
            errors.append(str(exc))
    start, end = default_period(default_months)
    return start, end, f"{default_months}m", errors


@merchant_view(Permission.REPORTS)
def dashboard(request):
    business = request.business
    start, end, preset, errors = period_from_request(request)
    data = metrics.compute(business, start, end)
    unpaid = [i for i in Invoice.objects.filter(business=business, status=Invoice.Status.OPEN) if i.outstanding > 0]
    context = {
        "business": business,
        "data": data,
        "preset": preset,
        "period_errors": errors,
        "start": start,
        "end": end,
        "unpaid_count": len(unpaid),
        "unpaid_total": sum((i.outstanding for i in unpaid), 0),
        "overdue_count": sum(1 for i in unpaid if i.due_date < today()),
        "findings": data.get("data_quality", {}).get("findings", [])[:6],
        "has_sources": ConnectedSource.objects.filter(business=business).exists(),
        "has_records": business.transactions.exists(),
        "prev_month": label(add_months(month_start(today()), -1)),
    }
    return render(request, "merchants/dashboard.html", context)


@login_required
def more(request):
    """The phone 'More' tab: everything that isn't in the tab bar."""
    return render(request, "merchants/more.html")
