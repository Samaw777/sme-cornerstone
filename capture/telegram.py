"""Telegram bot: staff log entries by chatting ("expense 3200 diesel cash"),
forwarding a bank/telebirr SMS, or sending a receipt photo with a caption.

Setup (see docs/LOGGING.md): create a bot with @BotFather, set
TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET, then run
`python manage.py telegram_setup https://your-site`."""

import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.utils import timezone

from core.periods import today
from ledger.models import Category, Direction, PaymentMethod, SourceKind, Transaction
from merchants.models import Permission

from . import parsing
from .models import TelegramLink, TelegramLinkCode
from .services import log_entry

log = logging.getLogger("cornerstone.telegram")
API = "https://api.telegram.org"
MAX_PHOTO_BYTES = 4 * 1024 * 1024

HELP = (
    "Log a transaction by sending a message, for example:\n"
    "• sale 4500 freight Adama telebirr\n"
    "• expense 3200 diesel cash\n"
    "• ወጪ 2,300 ነዳጅ ትናንት\n"
    "• a receipt photo with a caption like “expense 2500 tyres”\n"
    "• a forwarded bank or telebirr SMS\n\n"
    "/today shows today's totals. Entries by staff wait for an approver."
)


def call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    request = urllib.request.Request(f"{API}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}", data=data)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def reply(chat_id, text):
    try:
        call("sendMessage", chat_id=chat_id, text=text)
    except Exception:  # never fail the webhook because a reply couldn't be sent
        log.warning("telegram reply failed chat=%s", chat_id)


def download_photo(file_id):
    info = call("getFile", file_id=file_id)["result"]
    if info.get("file_size", 0) > MAX_PHOTO_BYTES:
        raise ValidationError("That photo is too large; send it compressed (not as a file).")
    url = f"{API}/file/bot{settings.TELEGRAM_BOT_TOKEN}/{info['file_path']}"
    with urllib.request.urlopen(url, timeout=15) as response:
        data = response.read(MAX_PHOTO_BYTES + 1)
    return SimpleUploadedFile("telegram-receipt.jpg", data, content_type="image/jpeg")


def describe(txn):
    sign = "+" if txn.direction == Direction.INFLOW else "−"
    method = f" · {PaymentMethod(txn.payment_method).label}" if txn.payment_method else ""
    return f"{sign}ETB {txn.amount:,.2f} · {Category(txn.category).label}{method} · {txn.occurred_on:%d %b}"


def handle_update(update):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = message["chat"]["id"]
    text = (message.get("text") or message.get("caption") or "").strip()
    command = text.split()[0].lower() if text.startswith("/") else ""

    if command == "/link":
        code = text.split()[1].upper() if len(text.split()) > 1 else ""
        link_code = TelegramLinkCode.objects.filter(code=code, expires_at__gt=timezone.now()).select_related("membership__business").first()
        if not link_code:
            reply(chat_id, "That code isn't valid or has expired. Get a new one in the app under Telegram.")
            return
        TelegramLink.objects.filter(chat_id=chat_id).delete()
        TelegramLink.objects.update_or_create(membership=link_code.membership, defaults={
            "chat_id": chat_id, "telegram_username": (message.get("from") or {}).get("username", "")[:64]})
        link_code.delete()
        reply(chat_id, f"Linked to {link_code.membership.business.name}. ✅\n\n{HELP}")
        return

    link = TelegramLink.objects.filter(chat_id=chat_id, membership__is_active=True).select_related("membership__business", "membership__user").first()
    if command in ("/start", "/help") or link is None:
        reply(chat_id, HELP if link else "Welcome to SME Cornerstone. To start, open the app → Telegram, get a link code, and send /link CODE here.")
        return
    membership, business, user = link.membership, link.membership.business, link.membership.user
    if not membership.has(Permission.RECORD):
        reply(chat_id, "Your role in this business doesn't include recording transactions.")
        return

    if command == "/today":
        todays = Transaction.objects.filter(business=business, occurred_on=today())
        counted = todays.filter(status=Transaction.Status.ACTIVE)
        money_in = counted.filter(direction=Direction.INFLOW).aggregate(t=Sum("amount"))["t"] or 0
        money_out = counted.filter(direction=Direction.OUTFLOW).aggregate(t=Sum("amount"))["t"] or 0
        pending = todays.filter(status=Transaction.Status.PENDING).count()
        reply(chat_id, f"Today at {business.name}:\nMoney in ETB {money_in:,.2f}\nMoney out ETB {money_out:,.2f}"
                       + (f"\n{pending} entr{'y' if pending == 1 else 'ies'} awaiting approval" if pending else ""))
        return

    if not text:
        reply(chat_id, "Add a caption with the amount, e.g. “expense 2500 tyres”.")
        return
    is_sms = parsing.looks_like_sms(text)
    draft = parsing.parse_sms(text, business) if is_sms else parsing.parse_text(text, business)
    if draft["errors"] and not draft.get("amount"):
        reply(chat_id, draft["errors"][0] + "\n\n" + HELP)
        return
    photo = None
    note = ""
    if message.get("photo"):
        if membership.has(Permission.DOCUMENTS):
            try:
                photo = download_photo(message["photo"][-1]["file_id"])
            except ValidationError as exc:
                note = f"\n(Photo not attached: {exc.messages[0]})"
            except Exception:
                note = "\n(Photo not attached: couldn't download it.)"
        else:
            note = "\n(Photo not attached: your role doesn't include documents.)"
    try:
        txn = log_entry(business, user, draft, source_kind=SourceKind.TELEGRAM, raw=text,
                        fingerprint=parsing.sms_fingerprint(draft) if is_sms else None, photo=photo)
    except ValidationError as exc:
        reply(chat_id, "Not recorded: " + "; ".join(exc.messages))
        return
    status = "⏳ Awaiting approval" if txn.status == Transaction.Status.PENDING else "✅ Recorded"
    attached = "\n📎 Receipt attached" if photo else ""
    reply(chat_id, f"{status}: {describe(txn)}{attached}{note}\nWrong? Ask an approver to correct or reject it.")
