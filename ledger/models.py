import hashlib
import json
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils.translation import gettext_lazy as _

from core.models import Evidence, ImmutableModel

MONEY = {"max_digits": 16, "decimal_places": 2}


class Currency(models.TextChoices):
    ETB = "ETB", _("Ethiopian birr (ETB)")


class Direction(models.TextChoices):
    INFLOW = "in", _("Money in")
    OUTFLOW = "out", _("Money out")


class Category(models.TextChoices):
    """Revenue and operating-expense categories feed the revenue/expense lines.
    Financing, owner and asset movements are cash movement but not revenue or
    expense, so cash movement and the revenue-minus-expense line differ."""

    SALES = "sales", _("Sales")
    SERVICE_INCOME = "service_income", _("Service income")
    OTHER_INCOME = "other_income", _("Other operating income")
    LOAN_RECEIVED = "loan_received", _("Loan or credit received")
    OWNER_CAPITAL = "owner_capital", _("Owner capital added")
    STOCK = "stock", _("Stock and raw materials")
    RENT = "rent", _("Rent")
    WAGES = "wages", _("Wages and staff costs")
    UTILITIES = "utilities", _("Electricity, water and utilities")
    TRANSPORT = "transport", _("Transport and fuel")
    TELECOM = "telecom", _("Phone, airtime and internet")
    TAXES_FEES = "taxes_fees", _("Taxes, licences and fees")
    MAINTENANCE = "maintenance", _("Repairs and maintenance")
    OTHER_EXPENSE = "other_expense", _("Other operating expense")
    LOAN_REPAYMENT = "loan_repayment", _("Loan repayment")
    OWNER_DRAWINGS = "owner_drawings", _("Owner drawings")
    EQUIPMENT = "equipment", _("Equipment purchase")


REVENUE_CATEGORIES = {Category.SALES, Category.SERVICE_INCOME, Category.OTHER_INCOME}
NON_OPERATING_INFLOWS = {Category.LOAN_RECEIVED, Category.OWNER_CAPITAL}
OPERATING_EXPENSE_CATEGORIES = {
    Category.STOCK, Category.RENT, Category.WAGES, Category.UTILITIES, Category.TRANSPORT,
    Category.TELECOM, Category.TAXES_FEES, Category.MAINTENANCE, Category.OTHER_EXPENSE,
}
NON_OPERATING_OUTFLOWS = {Category.LOAN_REPAYMENT, Category.OWNER_DRAWINGS, Category.EQUIPMENT}
INFLOW_CATEGORIES = REVENUE_CATEGORIES | NON_OPERATING_INFLOWS
OUTFLOW_CATEGORIES = OPERATING_EXPENSE_CATEGORIES | NON_OPERATING_OUTFLOWS


def categories_for(direction):
    allowed = INFLOW_CATEGORIES if direction == Direction.INFLOW else OUTFLOW_CATEGORIES
    return [(c.value, c.label) for c in Category if c in allowed]


class SourceKind(models.TextChoices):
    MANUAL = "manual", _("Manual entry")
    CSV = "csv", _("CSV import")
    CONNECTOR = "connector", _("Connected source")
    CORRECTION = "correction", _("Correction")
    SMS = "sms", _("Pasted SMS")
    TELEGRAM = "telegram", _("Telegram bot")
    BILL = "bill", _("Bill marked paid")


class PaymentMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    TELEBIRR = "telebirr", _("telebirr")
    BANK = "bank", _("Bank transfer")
    OTHER = "other", _("Other")


def sha256_json(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class ConnectedSource(models.Model):
    """An external account feeding system-verified transactions through a
    connector (see ledger/connectors). Demo connectors are always flagged."""

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="connected_sources")
    connector_key = models.CharField(max_length=40)
    display_name = models.CharField(max_length=120)
    account_mask = models.CharField(max_length=40)
    external_account_id = models.CharField(max_length=80)
    is_demo = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    connected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    connected_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "connector_key", "external_account_id"], name="one_link_per_account")
        ]

    def __str__(self):
        return f"{self.display_name} {self.account_mask}"


class StatementBalance(models.Model):
    """Opening/closing balances reported by a connected source for one month,
    used for reconciliation against the system-verified transactions."""

    source = models.ForeignKey(ConnectedSource, on_delete=models.CASCADE, related_name="balances")
    month = models.DateField()
    opening = models.DecimalField(**MONEY)
    closing = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.ETB)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "month"], name="one_balance_per_month")]


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", _("Uploaded — awaiting column mapping")
        MAPPED = "mapped", _("Mapped — awaiting review")
        COMMITTED = "committed", _("Imported")
        DISCARDED = "discarded", _("Discarded")

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="imports")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=200)
    file_sha256 = models.CharField(max_length=64)
    raw_text = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADED)
    mapping = models.JSONField(default=dict, blank=True)
    rows_total = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_duplicate = models.PositiveIntegerField(default=0)
    rows_error = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.filename


class SourceEvent(ImmutableModel):
    """The original record as it arrived: a CSV row exactly as uploaded, a
    connector event exactly as received, or the first version of a manual entry.
    Never edited; corrections create new transactions that point back here."""

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="source_events")
    source_kind = models.CharField(max_length=12, choices=SourceKind.choices)
    connected_source = models.ForeignKey(ConnectedSource, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    import_batch = models.ForeignKey(ImportBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    external_id = models.CharField(max_length=120, blank=True)
    payload = models.JSONField()
    payload_sha256 = models.CharField(max_length=64)
    received_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connected_source", "external_id"],
                condition=Q(connected_source__isnull=False),
                name="connector_event_once",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.payload_sha256:
            self.payload_sha256 = sha256_json(self.payload)
        super().save(*args, **kwargs)


class TransactionQuerySet(models.QuerySet):
    def active(self):
        """Records that count. Superseded and reversed originals are kept for the
        audit trail but never added to totals, so corrections can't double count."""
        return self.filter(status=Transaction.Status.ACTIVE)

    def total(self):
        return self.aggregate(total=Sum("amount"))["total"] or Decimal("0")


class Transaction(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUPERSEDED = "superseded", _("Superseded by a correction")
        REVERSED = "reversed", _("Reversed")
        # Entered by someone without approval rights: not counted until approved.
        PENDING = "pending", _("Awaiting approval")
        REJECTED = "rejected", _("Rejected by an approver")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="transactions")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    category = models.CharField(max_length=20, choices=Category.choices)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.ETB)
    occurred_on = models.DateField(db_index=True)
    description = models.CharField(max_length=200, blank=True)
    counterparty = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, blank=True)

    source_kind = models.CharField(max_length=12, choices=SourceKind.choices)
    source_event = models.ForeignKey(SourceEvent, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions")
    import_batch = models.ForeignKey(ImportBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions")
    connected_source = models.ForeignKey(ConnectedSource, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions")
    is_demo = models.BooleanField(default=False)

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    evidence_class = models.CharField(max_length=20, choices=Evidence.choices, default=Evidence.SELF_REPORTED)
    # Identifies the same underlying record across re-imports; see ledger.importing.
    fingerprint = models.CharField(max_length=64, null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TransactionQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "fingerprint"], condition=Q(fingerprint__isnull=False), name="no_duplicate_imports"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="amount_positive"),
        ]
        indexes = [models.Index(fields=["business", "status", "occurred_on"])]

    def __str__(self):
        return f"{self.occurred_on} {self.get_direction_display()} {self.currency} {self.amount}"

    MUTABLE_FIELDS = {"status", "evidence_class"}

    def save(self, *args, **kwargs):
        # Recorded figures are never edited in place: only status (via a
        # Correction) and the derived evidence class may change after creation.
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if not update_fields or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise TypeError("Transactions are corrected with an amendment or reversal, not edited.")
        super().save(*args, **kwargs)

    @property
    def signed_amount(self):
        return self.amount if self.direction == Direction.INFLOW else -self.amount

    @property
    def is_system_sourced(self):
        return self.source_kind == SourceKind.CONNECTOR

    def compute_evidence_class(self):
        if self.source_kind == SourceKind.CONNECTOR and self.source_event_id and self.source_event.external_id:
            return Evidence.SYSTEM_VERIFIED
        if self.evidence_links.exclude(document__validation_status=Document.Validation.REJECTED).exists():
            return Evidence.EVIDENCE_SUPPORTED
        return Evidence.SELF_REPORTED

    def refresh_evidence_class(self):
        new = self.compute_evidence_class()
        if new != self.evidence_class:
            self.evidence_class = new
            self.save(update_fields=["evidence_class"])

    def lineage(self):
        """The chain of corrections from the original record to this one."""
        chain = [self]
        current = self
        while True:
            correction = Correction.objects.filter(replacement=current).select_related("original").first()
            if not correction:
                break
            current = correction.original
            chain.append(current)
        return list(reversed(chain))


class Correction(ImmutableModel):
    """An amendment (original replaced by a new record) or a reversal (original
    withdrawn). The original stays in the database with its source event."""

    class Kind(models.TextChoices):
        AMENDMENT = "amendment", _("Amendment")
        REVERSAL = "reversal", _("Reversal")

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="corrections")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    original = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="corrections")
    replacement = models.OneToOneField(Transaction, null=True, blank=True, on_delete=models.PROTECT, related_name="corrects")
    reason = models.CharField(max_length=300)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Annotation(models.Model):
    """A merchant note or dispute on a record. The record itself is unchanged."""

    class Kind(models.TextChoices):
        NOTE = "note", _("Note")
        DISPUTE = "dispute", _("Dispute")

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="annotations")
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="annotations")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    text = models.CharField(max_length=500)
    is_open = models.BooleanField(default=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class Document(models.Model):
    class Kind(models.TextChoices):
        RECEIPT = "receipt", _("Receipt")
        INVOICE = "invoice", _("Invoice")
        STATEMENT = "statement", _("Bank or wallet statement")
        CONTRACT = "contract", _("Contract or lease")
        OTHER = "other", _("Other")

    class Validation(models.TextChoices):
        CHECKS_PASSED = "checks_passed", _("File checks passed — content not independently validated")
        REVIEWED = "reviewed", _("Reviewed by platform staff")
        REJECTED = "rejected", _("Rejected")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    title = models.CharField(max_length=160)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    validation_status = models.CharField(max_length=15, choices=Validation.choices, default=Validation.CHECKS_PASSED)
    validation_note = models.CharField(max_length=300, blank=True)
    is_demo = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [models.UniqueConstraint(fields=["business", "sha256"], name="document_once_per_business")]

    def __str__(self):
        return self.title


class DocumentBlob(models.Model):
    """File bytes, kept apart from Document so listings never load them and so
    they live inside the database's backups and access controls."""

    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="blob")
    data = models.BinaryField()


class Invoice(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        PAID = "paid", _("Paid")
        VOID = "void", _("Void")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="invoices")
    number = models.CharField(_("invoice number"), max_length=40)
    customer = models.CharField(_("customer"), max_length=120)
    issue_date = models.DateField(_("issue date"))
    due_date = models.DateField(_("due date"))
    amount = models.DecimalField(_("amount"), **MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.ETB)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.OPEN)
    notes = models.CharField(max_length=300, blank=True)
    is_demo = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="invoice_number_unique")]

    def __str__(self):
        return self.number

    @property
    def amount_paid(self):
        return self.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    @property
    def outstanding(self):
        if self.status == self.Status.VOID:
            return Decimal("0")
        return max(self.amount - self.amount_paid, Decimal("0"))


class InvoicePayment(models.Model):
    """A payment against an invoice. It does not create a transaction (record the
    cash receipt in Transactions, optionally linked here) so revenue isn't counted twice."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    paid_on = models.DateField()
    transaction = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoice_payments")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)


class RecurringObligation(models.Model):
    """An obligation the merchant has chosen to record. The platform does not
    discover liabilities that exist outside it."""

    class Kind(models.TextChoices):
        RENT = "rent", _("Rent or lease")
        LOAN = "loan", _("Loan repayment")
        SALARY = "salary", _("Salaries")
        UTILITY = "utility", _("Utilities")
        SUPPLIER = "supplier", _("Supplier credit")
        TAX = "tax", _("Tax instalment")
        OTHER = "other", _("Other")

    class Frequency(models.TextChoices):
        MONTHLY = "monthly", _("Monthly")
        QUARTERLY = "quarterly", _("Quarterly")
        ANNUAL = "annual", _("Annually")

    FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="obligations")
    name = models.CharField(_("name"), max_length=120)
    counterparty = models.CharField(_("paid to"), max_length=120)
    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices)
    amount = models.DecimalField(_("amount per payment"), **MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.ETB)
    frequency = models.CharField(_("frequency"), max_length=10, choices=Frequency.choices)
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    is_demo = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def monthly_equivalent(self):
        return (self.amount / self.FREQUENCY_MONTHS[self.frequency]).quantize(Decimal("0.01"))

    def is_current(self, on):
        return self.start_date <= on and (self.end_date is None or self.end_date >= on)


class EvidenceLink(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="links")
    transaction = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence_links")
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence_links")
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(transaction__isnull=False) & Q(invoice__isnull=True)) | (Q(transaction__isnull=True) & Q(invoice__isnull=False)),
                name="link_has_one_target",
            ),
            models.UniqueConstraint(fields=["document", "transaction"], condition=Q(transaction__isnull=False), name="link_doc_txn_once"),
            models.UniqueConstraint(fields=["document", "invoice"], condition=Q(invoice__isnull=False), name="link_doc_inv_once"),
        ]


class DataQualityFinding(models.Model):
    """A neutral observation about the records (gaps, unusual changes, possible
    duplicates, reconciliation differences). It is not a finding of wrongdoing."""

    class Code(models.TextChoices):
        MISSING_MONTH = "missing_month", _("Month with no records")
        UNUSUAL_CHANGE = "unusual_change", _("Unusual change")
        POSSIBLE_DUPLICATE = "possible_duplicate", _("Possible duplicate")
        RECONCILIATION_GAP = "reconciliation_gap", _("Reconciliation difference")
        MISSING_DOCUMENT = "missing_document", _("Large amounts without documents")
        OPEN_DISPUTE = "open_dispute", _("Disputed record")
        FUTURE_DATED = "future_dated", _("Future-dated record")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ACKNOWLEDGED = "acknowledged", _("Acknowledged with a note")
        RESOLVED = "resolved", _("No longer detected")

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="findings")
    code = models.CharField(max_length=20, choices=Code.choices)
    key = models.CharField(max_length=80)
    month = models.DateField(null=True, blank=True)
    title = models.CharField(max_length=200)
    detail = models.CharField(max_length=500)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    merchant_note = models.CharField(max_length=500, blank=True)
    first_detected_at = models.DateTimeField(auto_now_add=True)
    last_detected_at = models.DateTimeField(auto_now=True)
    calculation_version = models.CharField(max_length=20)

    class Meta:
        ordering = ["-month", "code"]
        constraints = [models.UniqueConstraint(fields=["business", "code", "key"], name="finding_once")]

    def __str__(self):
        return self.title


class ApprovalDecision(ImmutableModel):
    """An approver's decision on an entry made by someone without approval rights."""

    class Decision(models.TextChoices):
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    business = models.ForeignKey("merchants.Business", on_delete=models.CASCADE, related_name="approval_decisions")
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="approval_decisions")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    reason = models.CharField(max_length=300, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-decided_at"]


class ObligationPayment(models.Model):
    """Links one due instalment of a recurring obligation to the transaction that
    paid it, so a bill can't be marked paid twice."""

    obligation = models.ForeignKey(RecurringObligation, on_delete=models.CASCADE, related_name="payments")
    due_date = models.DateField()
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="obligation_payments")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["obligation", "due_date"], name="one_payment_per_instalment")]
