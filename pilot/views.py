import datetime as dt
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.models import AuditEvent
from ledger import services as ledger_services
from ledger.models import Document, Transaction
from merchants.models import Business, IdentityStatus
from sharing.models import FinancialInstitution, InstitutionMembership, PartnerCredential

from . import metrics
from .forms import (
    AnalystForm, CredentialForm, FeedbackForm, IdentityValidationForm, InstitutionForm, IssueForm, MetricEntryForm,
    PilotProgramForm, SupportAccessForm,
)
from .models import OperationalIssue, PilotProgram, SupportAccess

User = get_user_model()


def admin_view(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_platform_admin:
            raise PermissionDenied("This area is for platform administrators.")
        return view(request, *args, **kwargs)

    return wrapped


def _setup_link(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return request.build_absolute_uri(reverse("password_reset_confirm", args=[uid, default_token_generator.make_token(user)]))


@admin_view
def console_home(request):
    pilot = PilotProgram.objects.filter(is_active=True).select_related("institution").first()
    return render(request, "pilot/home.html", {
        "pilot": pilot,
        "counts": {"businesses": Business.objects.count(), "institutions": FinancialInstitution.objects.count(),
                   "users": User.objects.count(), "issues": OperationalIssue.objects.filter(status="open").count()},
        "recent": AuditEvent.objects.select_related("business", "institution")[:15],
    })


@admin_view
def console_businesses(request):
    businesses = Business.objects.annotate(member_count=Count("memberships", distinct=True), consent_count=Count("consents", distinct=True))
    return render(request, "pilot/businesses.html", {"businesses": businesses})


@admin_view
@require_http_methods(["GET", "POST"])
def console_business(request, public_id):
    business = get_object_or_404(Business, public_id=public_id)
    support = SupportAccess.objects.filter(admin=request.user, business=business, expires_at__gt=timezone.now()).first()
    support_form, identity_form = SupportAccessForm(), IdentityValidationForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "support":
            support_form = SupportAccessForm(request.POST)
            if support_form.is_valid():
                support = SupportAccess.objects.create(
                    admin=request.user, business=business, reason=support_form.cleaned_data["reason"],
                    expires_at=timezone.now() + dt.timedelta(minutes=settings.SUPPORT_ACCESS_MINUTES))
                audit.record("support.access_started", request=request, business=business, reason=support.reason,
                             minutes=settings.SUPPORT_ACCESS_MINUTES)
                messages.success(request, f"Support access open for {settings.SUPPORT_ACCESS_MINUTES} minutes. The business can see this.")
                return redirect("console_business", business.public_id)
        elif action == "identity":
            identity_form = IdentityValidationForm(request.POST)
            if identity_form.is_valid():
                field = identity_form.cleaned_data["field"]
                if not getattr(business, field):
                    messages.error(request, "The business hasn't provided this value.")
                else:
                    setattr(business, "tin_status" if field == "tin" else "trade_license_status", IdentityStatus.PLATFORM_VALIDATED)
                    business.identity_validated_by = request.user
                    business.identity_validated_at = timezone.now()
                    business.identity_validation_note = identity_form.cleaned_data["note"]
                    business.save()
                    audit.record("identity.validated", request=request, business=business, field=field)
                    messages.success(request, "Marked as validated by the platform.")
                    return redirect("console_business", business.public_id)
        elif action == "document" and support:
            document = get_object_or_404(Document, business=business, public_id=request.POST.get("document"))
            status = request.POST.get("status")
            if status in Document.Validation.values:
                ledger_services.set_document_validation(document, request.user, status, request.POST.get("note", "")[:300])
                messages.success(request, "Document review status updated.")
            return redirect("console_business", business.public_id)
    context = {"business": business, "support": support, "support_form": support_form, "identity_form": identity_form,
               "members": business.memberships.select_related("user"),
               "accesses": SupportAccess.objects.filter(business=business).select_related("admin")[:10]}
    if support:
        context["transactions"] = Transaction.objects.filter(business=business)[:50]
        context["documents"] = Document.objects.filter(business=business)[:50]
        context["validation_choices"] = Document.Validation.choices
    return render(request, "pilot/business.html", context)


@admin_view
@require_http_methods(["GET", "POST"])
def console_institutions(request):
    form = InstitutionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        institution = form.save()
        audit.record("institution.created", request=request, institution=institution)
        messages.success(request, f"{institution.name} added.")
        return redirect("console_institution", institution.public_id)
    institutions = FinancialInstitution.objects.annotate(analyst_count=Count("memberships", distinct=True), consent_count=Count("consents", distinct=True))
    return render(request, "pilot/institutions.html", {"form": form, "institutions": institutions})


@admin_view
@require_http_methods(["GET", "POST"])
def console_institution(request, public_id):
    institution = get_object_or_404(FinancialInstitution, public_id=public_id)
    analyst_form, credential_form = AnalystForm(), CredentialForm()
    new_token = setup_link = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "analyst":
            analyst_form = AnalystForm(request.POST)
            if analyst_form.is_valid():
                user, created = User.objects.get_or_create(email=analyst_form.cleaned_data["email"].lower(),
                                                           defaults={"name": analyst_form.cleaned_data["name"]})
                if created:
                    user.set_unusable_password()
                    user.save()
                    setup_link = _setup_link(request, user)
                if InstitutionMembership.objects.filter(user=user).exclude(institution=institution).exists():
                    messages.error(request, "That person already works for another institution.")
                else:
                    InstitutionMembership.objects.get_or_create(user=user, institution=institution)
                    audit.record("analyst.added", request=request, institution=institution, analyst=user.pk)
                    messages.success(request, f"{user.display_name} can now use the analyst portal.")
        elif action == "credential":
            credential_form = CredentialForm(request.POST)
            if credential_form.is_valid():
                credential, new_token = PartnerCredential.issue(institution, credential_form.cleaned_data["label"],
                                                                credential_form.cleaned_data["scopes"], request.user,
                                                                days=credential_form.cleaned_data["days"])
                audit.record("credential.issued", request=request, institution=institution, key_id=credential.key_id,
                             scopes=credential.scopes)
        elif action == "revoke":
            credential = get_object_or_404(PartnerCredential, pk=request.POST.get("credential"), institution=institution)
            if credential.revoked_at is None:
                credential.revoked_at = timezone.now()
                credential.save(update_fields=["revoked_at"])
                audit.record("credential.revoked", request=request, institution=institution, key_id=credential.key_id)
                messages.success(request, f"Credential {credential.key_id} revoked.")
        elif action == "toggle":
            institution.is_active = not institution.is_active
            institution.save(update_fields=["is_active"])
            audit.record("institution.suspended" if not institution.is_active else "institution.reactivated",
                         request=request, institution=institution)
    return render(request, "pilot/institution.html", {
        "institution": institution, "analyst_form": analyst_form, "credential_form": credential_form, "new_token": new_token,
        "setup_link": setup_link, "analysts": institution.memberships.select_related("user"),
        "credentials": institution.credentials.all(), "now": timezone.now(),
    })


@admin_view
@require_http_methods(["GET", "POST"])
def console_pilot(request):
    pilot = PilotProgram.objects.filter(is_active=True).first()
    program_form = PilotProgramForm(instance=pilot, prefix="p")
    metric_form, feedback_form = MetricEntryForm(prefix="m"), FeedbackForm(prefix="f")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "program":
            program_form = PilotProgramForm(request.POST, instance=pilot, prefix="p")
            if program_form.is_valid():
                pilot = program_form.save()
                audit.record("pilot.configured", request=request, institution=pilot.institution, pilot=pilot.pk)
                messages.success(request, "Pilot configuration saved.")
                return redirect("console_pilot")
        elif pilot and action == "metric":
            metric_form = MetricEntryForm(request.POST, prefix="m")
            if metric_form.is_valid():
                entry = metric_form.save(commit=False)
                entry.pilot, entry.entered_by = pilot, request.user
                entry.save()
                messages.success(request, "Measurement recorded.")
                return redirect("console_pilot")
        elif pilot and action == "feedback":
            feedback_form = FeedbackForm(request.POST, prefix="f")
            if feedback_form.is_valid():
                entry = feedback_form.save(commit=False)
                entry.pilot, entry.entered_by = pilot, request.user
                entry.save()
                messages.success(request, "Feedback recorded.")
                return redirect("console_pilot")
    include_demo = request.GET.get("demo") == "1"
    return render(request, "pilot/pilot.html", {
        "pilot": pilot, "program_form": program_form, "metric_form": metric_form, "feedback_form": feedback_form,
        "m": metrics.compute(pilot, include_demo=include_demo),
        "entries": pilot.metrics.select_related("entered_by")[:30] if pilot else [],
        "feedback": pilot.feedback.select_related("entered_by")[:30] if pilot else [],
    })


@admin_view
@require_http_methods(["GET", "POST"])
def console_issues(request):
    form = IssueForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        issue = form.save(commit=False)
        issue.created_by = request.user
        issue.save()
        messages.success(request, "Issue logged.")
        return redirect("console_issues")
    return render(request, "pilot/issues.html", {"form": form, "issues": OperationalIssue.objects.select_related("business", "institution")})


@admin_view
@require_POST
def console_issue_resolve(request, pk):
    issue = get_object_or_404(OperationalIssue, pk=pk)
    issue.status, issue.resolved_at = OperationalIssue.Status.RESOLVED, timezone.now()
    issue.save(update_fields=["status", "resolved_at"])
    return redirect("console_issues")


@admin_view
def console_audit(request):
    events = AuditEvent.objects.select_related("business", "institution", "actor_user")
    outcome = request.GET.get("outcome")
    if outcome in AuditEvent.Outcome.values:
        events = events.filter(outcome=outcome)
    return render(request, "pilot/audit.html", {"events": events[:200], "outcome": outcome})
