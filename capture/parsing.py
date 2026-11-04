"""Turn short messages into draft transactions.

Two inputs share this module:
* free text from Telegram or voice: "sale 4500 freight Adama telebirr", "ወጪ 2,300 ነዳጅ"
* pasted bank / telebirr SMS notifications

SMS formats differ between banks and change over time, so parsing is a
heuristic that fills an editable draft; nothing is saved without the person
confirming it. Pasted SMS are self-reported: they aren't an authorised feed."""

import datetime as dt
import hashlib
import re
from decimal import Decimal, InvalidOperation

from django.db.models import Count

from core.periods import today
from ledger.models import INFLOW_CATEGORIES, OUTFLOW_CATEGORIES, Category, Direction, PaymentMethod, Transaction

INFLOW_WORDS = {"sale", "sales", "sold", "income", "received", "receive", "got", "in", "deposit", "revenue",
                "ሽያጭ", "ገቢ", "ተቀበልኩ", "ሸጥኩ"}
OUTFLOW_WORDS = {"expense", "expenses", "spent", "spend", "paid", "pay", "bought", "buy", "out", "cost", "purchase",
                 "ወጪ", "ከፈልኩ", "ገዛሁ", "ክፍያ"}
PAYMENT_WORDS = {
    PaymentMethod.CASH: {"cash", "ካሽ", "ጥሬ"},
    PaymentMethod.TELEBIRR: {"telebirr", "tele", "ቴሌብር"},
    PaymentMethod.BANK: {"bank", "transfer", "cbe", "awash", "dashen", "abyssinia", "boa", "ባንክ"},
}
YESTERDAY_WORDS = {"yesterday", "ትናንት", "ትላንት"}
# Keyword -> category. Checked in order; the first match whose direction fits wins.
CATEGORY_WORDS = [
    (Category.TRANSPORT, {"diesel", "fuel", "benzene", "benzine", "petrol", "gas", "taxi", "bajaj", "ነዳጅ", "ናፍጣ"}),
    (Category.RENT, {"rent", "ኪራይ"}),
    (Category.WAGES, {"salary", "salaries", "wage", "wages", "labour", "labor", "driver", "ደመወዝ", "ሰራተኛ"}),
    (Category.UTILITIES, {"electricity", "power", "water", "utility", "መብራት", "ውሃ"}),
    (Category.TELECOM, {"airtime", "data", "internet", "wifi", "phone", "ካርድ"}),
    (Category.MAINTENANCE, {"repair", "repairs", "maintenance", "tyre", "tyres", "tire", "tires", "service", "garage", "ጥገና"}),
    (Category.TAXES_FEES, {"tax", "vat", "licence", "license", "fee", "fees", "ግብር", "ታክስ"}),
    (Category.STOCK, {"stock", "goods", "supplies", "supplier", "raw", "materials", "inventory", "ዕቃ", "እቃ"}),
    (Category.EQUIPMENT, {"equipment", "machine", "laptop", "truck", "vehicle"}),
    (Category.LOAN_REPAYMENT, {"loan", "repayment", "instalment", "installment", "ብድር"}),
    (Category.LOAN_RECEIVED, {"loan", "credit", "ብድር"}),
    (Category.OWNER_CAPITAL, {"capital", "owner", "investment", "ካፒታል"}),
    (Category.OWNER_DRAWINGS, {"drawings", "personal", "withdrawal"}),
    (Category.SERVICE_INCOME, {"freight", "delivery", "trip", "service", "commission", "fare", "ትራንስፖርት"}),
    (Category.SALES, {"sale", "sales", "sold", "shop", "ሽያጭ"}),
]
NUMBER = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"


def _amount(text):
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _words(text):
    return [w.strip(".,:;!?()").lower() for w in text.split()]


def default_category(business, direction):
    """The business's most used category for this direction, or a sensible default."""
    allowed = INFLOW_CATEGORIES if direction == Direction.INFLOW else OUTFLOW_CATEGORIES
    common = (Transaction.objects.filter(business=business, direction=direction, category__in=[c.value for c in allowed])
              .values("category").annotate(n=Count("id")).order_by("-n").first())
    if common:
        return common["category"]
    return Category.SALES if direction == Direction.INFLOW else Category.OTHER_EXPENSE


def guess_category(words, direction, business=None):
    allowed = INFLOW_CATEGORIES if direction == Direction.INFLOW else OUTFLOW_CATEGORIES
    found = set(words)
    for category, keys in CATEGORY_WORDS:
        if category in allowed and found & keys:
            return category.value
    return default_category(business, direction) if business else None


def guess_payment(words, text=""):
    found = set(words)
    for method, keys in PAYMENT_WORDS.items():
        if found & keys or any(k in text.lower() for k in keys if len(k) > 4):
            return method.value
    return ""


def parse_text(text, business=None):
    """Free text such as "expense 3200 diesel cash" or "ሽያጭ 4,500 ትናንት"."""
    text = (text or "").strip()
    words = _words(text)
    result = {"raw": text, "errors": []}
    match = re.search(NUMBER + r"\s*(k|K|ሺ)?", text)
    amount = _amount(match.group(1)) if match else None
    if amount is not None and match.group(2):
        amount *= 1000
    if not amount or amount <= 0:
        result["errors"].append("Couldn't find an amount — include a number, e.g. “sale 4500”.")
    result["amount"] = amount
    if set(words) & OUTFLOW_WORDS:
        direction = Direction.OUTFLOW
    elif set(words) & INFLOW_WORDS:
        direction = Direction.INFLOW
    else:
        # No direction word: a recognised expense keyword means money out; otherwise a sale.
        direction = Direction.OUTFLOW if guess_category(words, Direction.OUTFLOW) else Direction.INFLOW
    result["direction"] = direction.value
    result["category"] = guess_category(words, direction, business) or (
        Category.SALES.value if direction == Direction.INFLOW else Category.OTHER_EXPENSE.value)
    result["payment_method"] = guess_payment(words, text)
    result["occurred_on"] = today() - dt.timedelta(days=1) if set(words) & YESTERDAY_WORDS else today()
    skip = INFLOW_WORDS | OUTFLOW_WORDS | YESTERDAY_WORDS | {"etb", "birr", "br", "ብር"} | set().union(*PAYMENT_WORDS.values())
    rest = [w for w in text.split() if w.strip(".,:;").lower() not in skip and not re.fullmatch(NUMBER + r"(k|K|ሺ)?", w.strip(".,"))]
    result["description"] = " ".join(rest)[:200]
    result["counterparty"] = ""
    result["reference"] = ""
    return result


SMS_IN = re.compile(r"\b(credited|received|deposited|has been added|transferred to your|ተቀብለዋል|ገቢ ተደርጓል)", re.I)
SMS_OUT = re.compile(r"\b(debited|you have paid|have paid|paid|sent|transferred|withdrawn|withdrawal|purchase|ከፍለዋል|ልከዋል|ተቀንሷል)", re.I)
SMS_AMOUNT = re.compile(r"(?:ETB|Birr|Br\.?)\s*" + NUMBER + r"|" + NUMBER + r"\s*(?:ETB|Birr|Br\b|ብር)", re.I)
SMS_REF = re.compile(r"(?:ref(?:erence)?(?:\s*no\.?)?|transaction\s*(?:id|no\.?|number)|txn\s*id|trx\s*id|tid)\s*(?:is)?\s*[:#.]?\s*([A-Z0-9]{6,})"
                     r"|\b(FT[A-Z0-9]{8,})\b", re.I)
SMS_PARTY = re.compile(r"\b(?:from|to)\s+([A-Z][\w.'&-]*(?:\s+[A-Z][\w.'&-]*){0,4})")
SMS_DATES = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})"), lambda m: dt.date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})"), lambda m: dt.date(int(m[3]), int(m[2]), int(m[1]))),
    (re.compile(r"\b(\d{1,2})[ -]([A-Za-z]{3})[a-z]*[ -,]+(\d{4})"),
     lambda m: dt.datetime.strptime(f"{m[1]} {m[2].title()} {m[3]}", "%d %b %Y").date()),
]


def looks_like_sms(text):
    return bool(SMS_AMOUNT.search(text or "")) and bool(SMS_IN.search(text or "") or SMS_OUT.search(text or ""))


def parse_sms(text, business=None):
    text = " ".join((text or "").split())
    result = {"raw": text, "errors": [], "counterparty": "", "reference": "", "description": ""}
    amounts = []
    for m in SMS_AMOUNT.finditer(text):
        before = text[max(0, m.start() - 30):m.start()].lower()
        if any(w in before for w in ("balance", "bal.", "bal ", "fee", "charge", "vat", "ቀሪ")):
            continue
        value = _amount(m.group(1) or m.group(2))
        if value:
            amounts.append(value)
    result["amount"] = amounts[0] if amounts else None
    if not result["amount"]:
        result["errors"].append("Couldn't find the transaction amount in this message.")
    first_in = SMS_IN.search(text)
    first_out = SMS_OUT.search(text)
    if first_in and (not first_out or first_in.start() < first_out.start()):
        direction = Direction.INFLOW
    elif first_out:
        direction = Direction.OUTFLOW
    else:
        direction = Direction.INFLOW
        result["errors"].append("Couldn't tell whether money came in or went out — check the type.")
    result["direction"] = direction.value
    day = None
    for pattern, build in SMS_DATES:
        m = pattern.search(text)
        if m:
            try:
                day = build(m)
                break
            except ValueError:
                continue
    result["occurred_on"] = day if day and day <= today() else today()
    ref = SMS_REF.search(text)
    if ref:
        result["reference"] = (ref.group(1) or ref.group(2)).upper()[:80]
    party = SMS_PARTY.search(text)
    if party:
        name = re.split(r"\.\s|\.$|\s(?:Transaction|Ref|Your|Account|Acc)\b", party.group(1))[0]
        result["counterparty"] = name.strip(" .,")[:120]
    words = _words(text)
    result["payment_method"] = PaymentMethod.TELEBIRR.value if "telebirr" in text.lower() or "ቴሌብር" in text else PaymentMethod.BANK.value
    result["category"] = guess_category(words, direction, business) or (
        Category.SALES.value if direction == Direction.INFLOW else Category.OTHER_EXPENSE.value)
    result["description"] = ("Received " if direction == Direction.INFLOW else "Paid ") + (
        f"via {PaymentMethod(result['payment_method']).label}")
    return result


def sms_fingerprint(parsed):
    """Same SMS pasted twice, or pasted and also sent to the bot, is one record."""
    if parsed.get("reference"):
        key = "sms-ref|" + parsed["reference"]
    else:
        key = "sms-text|" + " ".join(parsed["raw"].lower().split())
    return hashlib.sha256(key.encode()).hexdigest()
