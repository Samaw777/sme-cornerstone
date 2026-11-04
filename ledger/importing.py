"""CSV import: upload -> map columns -> preview with validation -> commit.

Duplicate protection
--------------------
Every imported row gets a fingerprint, unique per business across all
transactions (including superseded/reversed ones):

* rows with a reference column: sha256(reference, date, amount, direction)
* otherwise: sha256(date, amount, direction, description, counterparty, n)
  where n counts identical rows earlier in the same file (0, 1, 2, ...).

Re-uploading the same file, or an overlapping export, therefore produces the
same fingerprints and those rows are skipped as duplicates. Identical rows
inside one file are flagged as repeats and only imported if the person
confirms they are genuinely separate records.

CSV rows are self-reported: an upload does not make anything system-verified.
"""

import csv
import datetime as dt
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone

from core import audit
from core.periods import today

from .models import (
    INFLOW_CATEGORIES, OUTFLOW_CATEGORIES, Category, Direction, ImportBatch, SourceEvent, SourceKind, Transaction,
)

MAX_ROWS = 5000
FIELDS = ["date", "amount", "money_in", "money_out", "direction", "category", "description", "counterparty", "reference",
          "payment_method"]
DATE_FORMATS = {
    "iso": ("%Y-%m-%d", "YYYY-MM-DD"),
    "dmy": ("%d/%m/%Y", "DD/MM/YYYY"),
    "dmy_dash": ("%d-%m-%Y", "DD-MM-YYYY"),
    "mdy": ("%m/%d/%Y", "MM/DD/YYYY"),
}
INFLOW_WORDS = {"in", "inflow", "credit", "cr", "income", "receipt", "sale", "sales", "deposit", "money in"}
OUTFLOW_WORDS = {"out", "outflow", "debit", "dr", "expense", "payment", "withdrawal", "purchase", "money out"}
HEADER_GUESSES = {
    "date": ["date", "transaction date", "txn date", "value date", "day"],
    "amount": ["amount", "amount (etb)", "value", "total"],
    "money_in": ["money in", "credit", "in", "inflow", "received", "deposit"],
    "money_out": ["money out", "debit", "out", "outflow", "paid", "withdrawal"],
    "direction": ["direction", "type", "in/out", "dr/cr"],
    "category": ["category", "account", "class"],
    "description": ["description", "details", "narration", "memo", "note", "particulars"],
    "counterparty": ["counterparty", "customer", "supplier", "payee", "party", "name"],
    "reference": ["reference", "ref", "receipt no", "invoice no", "transaction id", "id"],
    "payment_method": ["payment method", "method", "payment", "paid by", "channel"],
}
PAYMENT_ALIASES = {"cash": "cash", "telebirr": "telebirr", "tele birr": "telebirr", "bank": "bank", "bank transfer": "bank",
                   "transfer": "bank", "cbe": "bank", "mobile money": "telebirr"}


def read_upload(upload):
    if upload.size > settings.CSV_IMPORT_MAX_BYTES:
        raise ValidationError(f"CSV files can be up to {settings.CSV_IMPORT_MAX_BYTES // 1024} KB.")
    if not upload.name.lower().endswith((".csv", ".txt")):
        raise ValidationError("Upload a .csv file.")
    raw = upload.read()
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError("The file isn't readable text. Save it as CSV (UTF-8) and try again.")
    headers, rows = parse(text)
    if not headers:
        raise ValidationError("The file is empty.")
    if not rows:
        raise ValidationError("The file has a header row but no data rows.")
    return text, hashlib.sha256(raw).hexdigest()


def parse(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    lines = [row for row in reader if any(cell.strip() for cell in row)]
    if not lines:
        return [], []
    headers = [h.strip() for h in lines[0]]
    rows = lines[1 : MAX_ROWS + 1]
    return headers, rows


def guess_mapping(headers):
    lowered = {h.lower().strip(): h for h in headers}
    mapping = {}
    for field, guesses in HEADER_GUESSES.items():
        for guess in guesses:
            if guess in lowered and lowered[guess] not in mapping.values():
                mapping[field] = lowered[guess]
                break
    if "amount" in mapping and ("money_in" in mapping or "money_out" in mapping):
        mapping.pop("money_in", None)
        mapping.pop("money_out", None)
    return mapping


def guess_date_format(values):
    for key, (fmt, _) in DATE_FORMATS.items():
        parsed = 0
        for value in values[:20]:
            try:
                dt.datetime.strptime(value.strip(), fmt)
                parsed += 1
            except ValueError:
                pass
        if values and parsed == min(len(values), 20):
            return key
    return "iso"


AMOUNT_JUNK = re.compile(r"(etb|birr|br\.?|\s|,)", re.IGNORECASE)


def parse_amount(value):
    """'12,500.00' / 'ETB 12500' / '(450)' / '-450' -> (Decimal, negative?)."""
    text = AMOUNT_JUNK.sub("", value or "")
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("()-+")
    if not text:
        return None, False
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"“{value}” isn't a number.")
    if amount.as_tuple().exponent < -2:
        raise ValueError(f"“{value}” has more than two decimal places.")
    return amount, negative


def match_category(value, direction):
    allowed = INFLOW_CATEGORIES if direction == Direction.INFLOW else OUTFLOW_CATEGORIES
    wanted = (value or "").strip().lower()
    if wanted:
        for category in Category:
            if category in allowed and wanted in (category.value, str(category.label).lower()):
                return category.value, None
    default = Category.SALES if direction == Direction.INFLOW else Category.OTHER_EXPENSE
    note = f"Category “{value}” not recognised — using “{default.label}”." if wanted else None
    return default.value, note


def _norm(text):
    return " ".join((text or "").lower().split())


def fingerprint(row, occurrence):
    amount = f"{row['amount']:.2f}"  # "1200" and "1,200.00" are the same amount
    if row["reference"]:
        key = ["ref", _norm(row["reference"]), str(row["date"]), amount, row["direction"]]
    else:
        key = ["row", str(row["date"]), amount, row["direction"], _norm(row["description"]),
               _norm(row["counterparty"]), str(occurrence)]
    return hashlib.sha256("|".join(key).encode()).hexdigest()


def validate_mapping(mapping, headers):
    errors = []
    if not mapping.get("date"):
        errors.append("Choose the column that holds the date.")
    has_amount = bool(mapping.get("amount"))
    has_split = bool(mapping.get("money_in") or mapping.get("money_out"))
    if not has_amount and not has_split:
        errors.append("Choose an amount column, or separate money-in / money-out columns.")
    if has_amount and has_split:
        errors.append("Use either one amount column or money-in / money-out columns, not both.")
    for field, column in mapping.items():
        if field in FIELDS and column and column not in headers:
            errors.append(f"Column “{column}” isn't in the file.")
    if mapping.get("date_format") not in DATE_FORMATS:
        errors.append("Choose the date format used in the file.")
    return errors


def preview(batch):
    """Validate every row against the mapping and existing records. Nothing is saved."""
    headers, rows = parse(batch.raw_text)
    mapping = batch.mapping
    index = {h: i for i, h in enumerate(headers)}
    fmt = DATE_FORMATS[mapping["date_format"]][0]
    existing = set(
        Transaction.objects.filter(business=batch.business, fingerprint__isnull=False).values_list("fingerprint", flat=True)
    )
    similar = {
        (t["occurred_on"], t["amount"], t["direction"])
        for t in Transaction.objects.active().filter(business=batch.business).exclude(source_kind=SourceKind.CSV)
        .values("occurred_on", "amount", "direction")
    }
    seen = {}
    results = []
    latest = today()

    def cell(field, raw):
        column = mapping.get(field)
        return raw[index[column]].strip() if column and index[column] < len(raw) else ""

    for line_number, raw in enumerate(rows, start=2):
        errors, warnings = [], []
        row = {"line": line_number, "raw": dict(zip(headers, raw)), "description": cell("description", raw)[:200],
               "counterparty": cell("counterparty", raw)[:120], "reference": cell("reference", raw)[:80]}

        date_text = cell("date", raw)
        try:
            row["date"] = dt.datetime.strptime(date_text, fmt).date()
            if row["date"] > latest:
                errors.append("Date is in the future.")
            elif row["date"].year < 2000:
                errors.append("Date is before 2000.")
        except ValueError:
            row["date"] = None
            errors.append(f"“{date_text}” isn't a {DATE_FORMATS[mapping['date_format']][1]} date." if date_text else "Date is missing.")

        row["direction"] = None
        row["amount"] = None
        try:
            if mapping.get("amount"):
                amount, negative = parse_amount(cell("amount", raw))
                direction_text = _norm(cell("direction", raw))
                if direction_text in INFLOW_WORDS:
                    row["direction"] = Direction.INFLOW
                elif direction_text in OUTFLOW_WORDS:
                    row["direction"] = Direction.OUTFLOW
                elif mapping.get("direction") and direction_text:
                    errors.append(f"“{cell('direction', raw)}” isn't a recognised direction (use in/out, credit/debit).")
                elif amount is not None:
                    row["direction"] = Direction.OUTFLOW if negative else Direction.INFLOW
                row["amount"] = amount
            else:
                money_in, _ = parse_amount(cell("money_in", raw))
                money_out, _ = parse_amount(cell("money_out", raw))
                if money_in and money_out:
                    errors.append("Both money-in and money-out have a value.")
                elif money_in:
                    row["amount"], row["direction"] = money_in, Direction.INFLOW
                elif money_out:
                    row["amount"], row["direction"] = money_out, Direction.OUTFLOW
        except ValueError as exc:
            errors.append(str(exc))
        if row["amount"] is None and not any("number" in e or "decimal" in e for e in errors):
            errors.append("Amount is missing.")
        elif row["amount"] is not None and row["amount"] <= 0:
            errors.append("Amount must be greater than zero.")
            row["amount"] = None
        elif row["amount"] is not None and row["amount"] >= Decimal("1e13"):
            errors.append("Amount is too large.")

        row["payment_method"] = PAYMENT_ALIASES.get(_norm(cell("payment_method", raw)), "")
        if cell("payment_method", raw) and not row["payment_method"]:
            warnings.append(f"Payment method “{cell('payment_method', raw)}” not recognised — left blank.")
        row["category"] = None
        if row["direction"]:
            row["category"], note = match_category(cell("category", raw), row["direction"])
            if note:
                warnings.append(note)

        row["errors"], row["warnings"] = errors, warnings
        if errors:
            row["status"] = "error"
        else:
            base = fingerprint(row, 0)
            occurrence = seen.get(base, 0)
            seen[base] = occurrence + 1
            row["fingerprint"] = fingerprint(row, occurrence) if not row["reference"] else base
            if row["fingerprint"] in existing:
                row["status"] = "duplicate"
            elif occurrence and not row["reference"]:
                row["status"] = "repeat"
                warnings.append("Identical to an earlier row in this file.")
            elif occurrence and row["reference"]:
                row["status"] = "duplicate"
                warnings.append("Same reference as an earlier row in this file.")
            else:
                row["status"] = "ok"
            if (row["date"], row["amount"], row["direction"]) in similar:
                warnings.append("A record with the same date, amount and direction already exists from another source.")
        results.append(row)
    return results


def summarise(results):
    counts = {"ok": 0, "duplicate": 0, "repeat": 0, "error": 0}
    for row in results:
        counts[row["status"]] += 1
    counts["total"] = len(results)
    return counts


@db_transaction.atomic
def commit(batch, user, include_repeats=False, is_demo=False):
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != ImportBatch.Status.MAPPED:
        raise ValidationError("This import has already been completed or discarded.")
    results = preview(batch)
    imported = duplicates = 0
    from .services import requires_approval

    status = Transaction.Status.PENDING if requires_approval(batch.business, user) else Transaction.Status.ACTIVE
    for row in results:
        if row["status"] == "error":
            continue
        if row["status"] == "duplicate" or (row["status"] == "repeat" and not include_repeats):
            duplicates += 1
            continue
        try:
            with db_transaction.atomic():
                event = SourceEvent.objects.create(
                    business=batch.business, source_kind=SourceKind.CSV, import_batch=batch,
                    payload={"line": row["line"], "row": row["raw"]}, received_by=user,
                )
                Transaction.objects.create(
                    business=batch.business, source_kind=SourceKind.CSV, source_event=event, import_batch=batch,
                    direction=row["direction"], category=row["category"], amount=row["amount"],
                    occurred_on=row["date"], description=row["description"], counterparty=row["counterparty"],
                    reference=row["reference"], payment_method=row["payment_method"], fingerprint=row["fingerprint"], created_by=user, is_demo=is_demo, status=status,
                )
            imported += 1
        except IntegrityError:
            # Another import committed the same record a moment ago.
            duplicates += 1
    summary = summarise(results)
    batch.status = ImportBatch.Status.COMMITTED
    batch.committed_at = timezone.now()
    batch.rows_total = summary["total"]
    batch.rows_imported = imported
    batch.rows_duplicate = duplicates
    batch.rows_error = summary["error"]
    batch.save()
    audit.record("import.committed", user=user, business=batch.business, batch=batch.pk,
                 imported=imported, duplicates=duplicates, errors=summary["error"])
    return batch
