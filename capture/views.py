import datetime as dt
import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from core.periods import today
from ledger import services as ledger_services
from ledger.models import Direction, PaymentMethod, RecurringObligation, SourceKind, Transaction, categories_for
from merchants.access import merchant_view
from merchants.models import Permission

from . import parsing, telegram
from .models import QuickTemplate, TelegramLink, TelegramLinkCode
from .services import log_entry, mark_bill_paid


def _wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def _last_entry(request):
    return (Transaction.objects.filter(business=request.business, created_by=request.user)
            .exclude(source_kind__in=[SourceKind.CORRECTION, SourceKind.CONNECTOR]).order_by("-created_at").first())


@merchant_view(Permission.RECORD)
@require_http_methods(["GET", "POST"])
def quick_add(request):
    business = request.business
    if request.method == "POST":
        template = None
        if request.POST.get("template"):
            template = QuickTemplate.objects.filter(business=business, pk=request.POST["template"]).first()
        day = {"today": today(), "yesterday": today() - dt.timedelta(days=1)}.get(request.POST.get("when"), request.POST.get("occurred_on"))
        photo = request.FILES.get("photo")
        if photo and not request.membership.has(Permission.DOCUMENTS):
            photo = None
        try:
            txn = log_entry(business, request.user, {**request.POST.dict(), "occurred_on": day}, photo=photo, template=template)
            label = (request.POST.get("save_as") or "").strip()[:40]
            if label:
                QuickTemplate.objects.update_or_create(business=business, label=label, defaults={
                    "direction": txn.direction, "category": txn.category, "payment_method": txn.payment_method,
                    "counterparty": txn.counterparty, "description": txn.description, "created_by": request.user})
        except ValidationError as exc:
            if _wants_json(request):
                return JsonResponse({"ok": False, "errors": exc.messages}, status=400)
            for error in exc.messages:
                messages.error(request, error)
            return redirect("quick_add")
        pending = txn.status == Transaction.Status.PENDING
        text = f"{'Saved — awaiting approval' if pending else 'Saved'}: {telegram.describe(txn)}"
        if _wants_json(request):
            return JsonResponse({"ok": True, "message": text, "pending": pending, "evidence": txn.evidence_class})
        messages.success(request, text)
        return redirect("quick_add")
    last = _last_entry(request)
    return render(request, "capture/quick_add.html", {
        "templates": QuickTemplate.objects.filter(business=business)[:12],
        "in_categories": categories_for(Direction.INFLOW), "out_categories": categories_for(Direction.OUTFLOW),
        "default_in": parsing.default_category(business, Direction.INFLOW),
        "default_out": parsing.default_category(business, Direction.OUTFLOW),
        "methods": PaymentMethod.choices, "last": last, "can_photo": request.membership.has(Permission.DOCUMENTS),
        "needs_approval": not request.membership.has(Permission.APPROVE),
        "templates_json": json.dumps([{"id": t.pk, "direction": t.direction, "category": t.category,
                                       "payment_method": t.payment_method, "counterparty": t.counterparty,
                                       "description": t.description, "amount": str(t.default_amount or "")}
                                      for t in QuickTemplate.objects.filter(business=business)[:12]]),
        "last_json": json.dumps({"direction": last.direction, "category": last.category, "payment_method": last.payment_method,
                                 "counterparty": last.counterparty, "description": last.description,
                                 "amount": str(last.amount)} if last else None),
    })


@merchant_view(Permission.RECORD)
@require_POST
def quick_parse(request):
    """Voice or typed sentence -> draft fields for the quick-add form."""
    text = request.POST.get("text", "")[:500]
    draft = parsing.parse_sms(text, request.business) if parsing.looks_like_sms(text) else parsing.parse_text(text, request.business)
    return JsonResponse({"amount": str(draft["amount"] or ""), "direction": draft["direction"], "category": draft["category"],
                         "payment_method": draft["payment_method"], "description": draft["description"],
                         "counterparty": draft["counterparty"], "occurred_on": str(draft["occurred_on"]), "errors": draft["errors"]})


@merchant_view(Permission.RECORD)
@require_POST
def template_delete(request, pk):
    get_object_or_404(QuickTemplate, pk=pk, business=request.business).delete()
    messages.success(request, "Favourite removed.")
    return redirect("quick_add")


# ---------- Paste SMS ----------

def _split_messages(text):
    blocks = [b.strip() for b in text.replace("\r", "").split("\n\n") if b.strip()]
    return blocks[:30]


@merchant_view(Permission.RECORD)
@require_http_methods(["GET", "POST"])
def sms(request):
    business = request.business
    if request.method == "POST" and request.POST.get("step") == "save":
        saved = skipped = 0
        for i in range(int(request.POST.get("count", "0") or 0)):
            if not request.POST.get(f"keep-{i}"):
                continue
            raw = request.POST.get(f"raw-{i}", "")[:2000]
            draft = parsing.parse_sms(raw, business)
            edited = {k: request.POST.get(f"{k}-{i}", "") for k in ("direction", "amount", "category", "payment_method",
                                                                    "occurred_on", "counterparty", "description")}
            edited["reference"] = draft["reference"]
            try:
                log_entry(business, request.user, edited, source_kind=SourceKind.SMS, raw=raw, fingerprint=parsing.sms_fingerprint(draft))
                saved += 1
            except ValidationError as exc:
                skipped += 1
                messages.error(request, f"Message {i + 1} not saved: {'; '.join(exc.messages)}")
        if saved:
            messages.success(request, f"Saved {saved} transaction(s) from SMS." + (" They're awaiting approval." if not request.membership.has(Permission.APPROVE) else ""))
        return redirect("sms")
    drafts = []
    text = ""
    if request.method == "POST":
        text = request.POST.get("text", "")[:20000]
        for raw in _split_messages(text):
            draft = parsing.parse_sms(raw, business)
            fingerprint = parsing.sms_fingerprint(draft)
            draft["duplicate"] = Transaction.objects.filter(business=business, fingerprint=fingerprint).exists()
            drafts.append(draft)
        if not drafts:
            messages.error(request, "Paste at least one message.")
    return render(request, "capture/sms.html", {
        "drafts": drafts, "text": text, "methods": PaymentMethod.choices,
        "in_categories": categories_for(Direction.INFLOW), "out_categories": categories_for(Direction.OUTFLOW),
    })


# ---------- Bills ----------

@merchant_view(Permission.RECORD)
@require_POST
def bill_paid(request, pk):
    obligation = get_object_or_404(RecurringObligation, pk=pk, business=request.business)
    try:
        due = dt.date.fromisoformat(request.POST.get("due_date", ""))
        txn = mark_bill_paid(obligation, due, request.user, request.POST.get("payment_method", ""))
        messages.success(request, f"{obligation.name} marked paid." + (" Awaiting approval." if txn.status == Transaction.Status.PENDING else ""))
    except (ValueError, ValidationError) as exc:
        messages.error(request, exc.messages[0] if isinstance(exc, ValidationError) else "Invalid due date.")
    return redirect(request.POST.get("next") or "dashboard")


# ---------- Approvals ----------

@merchant_view(Permission.APPROVE)
@require_http_methods(["GET", "POST"])
def approvals(request):
    business = request.business
    if request.method == "POST":
        ids = request.POST.getlist("txn")
        approve = request.POST.get("decision") == "approve"
        done = 0
        for txn in Transaction.objects.filter(business=business, status=Transaction.Status.PENDING, public_id__in=ids):
            try:
                ledger_services.decide(txn, request.user, approve, request.POST.get("reason", ""))
                done += 1
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                break
        if done:
            from ledger import quality

            quality.refresh(business)
            messages.success(request, f"{'Approved' if approve else 'Rejected'} {done} entr{'y' if done == 1 else 'ies'}.")
        return redirect("approvals")
    pending = (Transaction.objects.filter(business=business, status=Transaction.Status.PENDING)
               .select_related("created_by").prefetch_related("evidence_links__document").order_by("-created_at"))
    return render(request, "capture/approvals.html", {"pending": pending})


# ---------- Telegram ----------

@merchant_view(Permission.RECORD)
@require_http_methods(["GET", "POST"])
def telegram_settings(request):
    link = TelegramLink.objects.filter(membership=request.membership).first()
    code = None
    if request.method == "POST":
        if "unlink" in request.POST and link:
            link.delete()
            messages.success(request, "Telegram unlinked.")
            return redirect("telegram_settings")
        code = TelegramLinkCode.new(request.membership, timezone.now() + dt.timedelta(minutes=15))
    return render(request, "capture/telegram.html", {
        "link": link, "code": code, "configured": bool(settings.TELEGRAM_BOT_TOKEN),
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
    })


@csrf_exempt
@require_POST
def telegram_webhook(request):
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not (settings.TELEGRAM_BOT_TOKEN and secret and hmac.compare_digest(sent, secret)):
        raise Http404
    try:
        update = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponse(status=400)
    try:
        telegram.handle_update(update)
    except Exception:
        telegram.log.exception("telegram update failed")
    return HttpResponse("ok")


# ---------- Offline support ----------

SERVICE_WORKER = """
const CACHE = "cornerstone-v1";
const SHELL = ["/quick/", "%(css)s", "%(icon)s"];
self.addEventListener("install", e => { self.skipWaiting(); e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => {}))); });
self.addEventListener("activate", e => { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res; })));
  } else if (url.pathname === "/quick/") {
    // Network first so the page is fresh; the cached copy lets you log with no signal.
    e.respondWith(fetch(req).then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res; })
      .catch(() => caches.match(req)));
  }
});
"""


def service_worker(request):
    body = SERVICE_WORKER % {"css": static("css/app.css"), "icon": static("icons/icon-192.png")}
    response = HttpResponse(body, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response

