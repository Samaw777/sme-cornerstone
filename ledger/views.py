from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.models import TRANSACTION_EVIDENCE_CLASSES
from core.periods import month_end, parse_month, today
from merchants.access import merchant_view
from merchants.models import Permission
from pilot.models import SupportAccess

from . import importing, quality, services
from .connectors import registry
from .forms import (
    AmendForm, AnnotationForm, CorrectionReasonForm, DocumentForm, FindingNoteForm, ImportUploadForm, InvoiceForm,
    MappingForm, ObligationForm, PaymentForm, TransactionForm,
)
from .models import (
    Annotation, Category, ConnectedSource, Correction, DataQualityFinding, Direction, Document, EvidenceLink, ImportBatch, Invoice,
    InvoicePayment, PaymentMethod, RecurringObligation, SourceKind, Transaction, categories_for,
)

# ---------- Transactions ----------


@merchant_view(Permission.REPORTS)
def transaction_list(request):
    business = request.business
    qs = Transaction.objects.filter(business=business).select_related("connected_source")
    filters = {k: request.GET.get(k, "") for k in ("from", "to", "source", "evidence", "direction", "category", "status", "q", "payment")}
    errors = []
    try:
        if filters["from"]:
            qs = qs.filter(occurred_on__gte=parse_month(filters["from"]))
        if filters["to"]:
            qs = qs.filter(occurred_on__lte=month_end(parse_month(filters["to"])))
    except ValueError as exc:
        errors.append(str(exc))
    if filters["source"] in SourceKind.values:
        qs = qs.filter(source_kind=filters["source"])
    if filters["evidence"] in [c.value for c in TRANSACTION_EVIDENCE_CLASSES]:
        qs = qs.filter(evidence_class=filters["evidence"])
    if filters["direction"] in Direction.values:
        qs = qs.filter(direction=filters["direction"])
    if filters["category"] in Category.values:
        qs = qs.filter(category=filters["category"])
    if filters["payment"] in PaymentMethod.values:
        qs = qs.filter(payment_method=filters["payment"])
    if filters["status"] == "all":
        pass
    elif filters["status"] in Transaction.Status.values:
        qs = qs.filter(status=filters["status"])
    else:
        # Current records, plus entries still waiting for approval (marked, not counted).
        qs = qs.filter(status__in=[Transaction.Status.ACTIVE, Transaction.Status.PENDING])
    if filters["q"]:
        qs = qs.filter(description__icontains=filters["q"]) | qs.filter(counterparty__icontains=filters["q"])
    active = qs.filter(status=Transaction.Status.ACTIVE)
    totals = {
        "in": active.filter(direction=Direction.INFLOW).total(),
        "out": active.filter(direction=Direction.OUTFLOW).total(),
        "count": qs.count(),
    }
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "ledger/transactions.html", {
        "page": page, "filters": filters, "errors": errors, "totals": totals, "query": query.urlencode(),
        "sources": SourceKind.choices, "evidence_classes": TRANSACTION_EVIDENCE_CLASSES,
        "categories": Category.choices, "statuses": Transaction.Status.choices, "methods": PaymentMethod.choices,
    })


@merchant_view(Permission.RECORD)
@require_http_methods(["GET", "POST"])
def transaction_new(request):
    form = TransactionForm(request.POST or None, initial={"direction": request.GET.get("direction", Direction.INFLOW)})
    if request.method == "POST" and form.is_valid():
        txn = services.record_manual(request.business, request.user, form.cleaned_data)
        messages.success(request, "Recorded. Attach a receipt to make it evidence-supported.")
        if "another" in request.POST:
            return redirect("transaction_new")
        return redirect("transaction_detail", txn.public_id)
    return render(request, "ledger/transaction_form.html", {
        "form": form, "in_categories": categories_for(Direction.INFLOW), "out_categories": categories_for(Direction.OUTFLOW),
    })


def _txn(request, public_id):
    return get_object_or_404(Transaction.objects.select_related("source_event", "connected_source", "import_batch"),
                             business=request.business, public_id=public_id)


@merchant_view(Permission.REPORTS)
def transaction_detail(request, public_id):
    txn = _txn(request, public_id)
    can_approve = request.membership.has(Permission.APPROVE)
    can_correct = can_approve and request.membership.has(Permission.RECORD) and txn.status == Transaction.Status.ACTIVE and not txn.is_system_sourced
    initial = {f: getattr(txn, f) for f in ("direction", "occurred_on", "amount", "category", "counterparty", "description",
                                            "reference", "payment_method")}
    documents = Document.objects.filter(business=request.business).exclude(links__transaction=txn)[:50]
    return render(request, "ledger/transaction_detail.html", {
        "txn": txn,
        "lineage": txn.lineage(),
        "corrections": txn.corrections.select_related("author", "replacement"),
        "corrected_from": Correction.objects.filter(replacement=txn).select_related("original", "author").first(),
        "links": txn.evidence_links.select_related("document"),
        "annotations": txn.annotations.select_related("author"),
        "amend_form": AmendForm(initial=initial),
        "reverse_form": CorrectionReasonForm(),
        "note_form": AnnotationForm(),
        "can_correct": can_correct,
        "can_approve": can_approve,
        "decisions": txn.approval_decisions.select_related("decided_by"),
        "can_link": request.membership.has(Permission.DOCUMENTS) and txn.status in (Transaction.Status.ACTIVE, Transaction.Status.PENDING),
        "documents": documents,
        "doc_form": DocumentForm(),
    })


@merchant_view(Permission.RECORD)
@require_POST
def transaction_amend(request, public_id):
    txn = _txn(request, public_id)
    form = AmendForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("transaction_detail", txn.public_id)
    try:
        replacement = services.amend(txn, request.user, form.cleaned_data, form.cleaned_data["reason"])
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("transaction_detail", txn.public_id)
    messages.success(request, "Correction saved. The original is kept and linked below.")
    return redirect("transaction_detail", replacement.public_id)


@merchant_view(Permission.RECORD)
@require_POST
def transaction_reverse(request, public_id):
    txn = _txn(request, public_id)
    form = CorrectionReasonForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("A reason is required for every correction.")
        services.reverse(txn, request.user, form.cleaned_data["reason"])
        messages.success(request, "Record reversed. It stays in the history but no longer counts in totals.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("transaction_detail", txn.public_id)


@merchant_view(Permission.RECORD)
@require_POST
def transaction_annotate(request, public_id):
    txn = _txn(request, public_id)
    form = AnnotationForm(request.POST)
    if form.is_valid():
        services.annotate(txn, request.user, form.cleaned_data["kind"], form.cleaned_data["text"])
        if form.cleaned_data["kind"] == Annotation.Kind.DISPUTE:
            quality.refresh(request.business)
        messages.success(request, "Note added. The record itself is unchanged.")
    return redirect("transaction_detail", txn.public_id)


@merchant_view(Permission.RECORD)
@require_POST
def annotation_resolve(request, pk):
    annotation = get_object_or_404(Annotation, pk=pk, business=request.business, kind=Annotation.Kind.DISPUTE)
    services.resolve_dispute(annotation)
    quality.refresh(request.business)
    messages.success(request, "Dispute marked resolved.")
    return redirect("transaction_detail", annotation.transaction.public_id)


@merchant_view(Permission.DOCUMENTS)
@require_POST
def transaction_link(request, public_id):
    txn = _txn(request, public_id)
    try:
        if request.FILES.get("file"):
            form = DocumentForm(request.POST, request.FILES)
            if not form.is_valid():
                raise ValidationError("Choose a file and document type.")
            document = services.store_document(request.business, request.user, form.cleaned_data["file"],
                                               form.cleaned_data["kind"], form.cleaned_data["title"])
        else:
            document = get_object_or_404(Document, business=request.business, public_id=request.POST.get("document"))
        services.link_document(document, request.user, transaction=txn)
        messages.success(request, f"“{document.title}” linked. This record is now evidence-supported.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("transaction_detail", txn.public_id)


@merchant_view(Permission.DOCUMENTS)
@require_POST
def link_remove(request, pk):
    link = get_object_or_404(EvidenceLink, pk=pk, document__business=request.business)
    target = link.transaction.public_id if link.transaction_id else None
    services.unlink_document(link)
    messages.success(request, "Document unlinked.")
    return redirect("transaction_detail", target) if target else redirect("receivables")


# ---------- CSV import ----------


@merchant_view(Permission.IMPORT)
@require_http_methods(["GET", "POST"])
def import_list(request):
    form = ImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            text, digest = importing.read_upload(form.cleaned_data["file"])
        except ValidationError as exc:
            form.add_error("file", exc.messages[0])
        else:
            headers, _ = importing.parse(text)
            batch = ImportBatch.objects.create(
                business=request.business, uploaded_by=request.user, filename=form.cleaned_data["file"].name[:200],
                file_sha256=digest, raw_text=text, mapping=importing.guess_mapping(headers),
            )
            return redirect("import_map", batch.pk)
    batches = ImportBatch.objects.filter(business=request.business)[:30]
    return render(request, "ledger/imports.html", {"form": form, "batches": batches})


def _batch(request, pk):
    return get_object_or_404(ImportBatch, pk=pk, business=request.business)


@merchant_view(Permission.IMPORT)
@require_http_methods(["GET", "POST"])
def import_map(request, pk):
    batch = _batch(request, pk)
    if batch.status in (ImportBatch.Status.COMMITTED, ImportBatch.Status.DISCARDED):
        return redirect("import_preview", batch.pk)
    headers, rows = importing.parse(batch.raw_text)
    initial = dict(batch.mapping)
    if "date_format" not in initial:
        column = initial.get("date")
        values = [r[headers.index(column)] for r in rows if column and headers.index(column) < len(r)] if column else []
        initial["date_format"] = importing.guess_date_format(values)
    form = MappingForm(request.POST or None, headers=headers, initial=initial)
    mapping_errors = []
    if request.method == "POST" and form.is_valid():
        mapping = {k: v for k, v in form.cleaned_data.items() if v}
        mapping_errors = importing.validate_mapping(mapping, headers)
        if not mapping_errors:
            batch.mapping = mapping
            batch.status = ImportBatch.Status.MAPPED
            batch.save(update_fields=["mapping", "status"])
            return redirect("import_preview", batch.pk)
    return render(request, "ledger/import_map.html", {"batch": batch, "form": form, "headers": headers,
                                                      "sample": rows[:5], "mapping_errors": mapping_errors})


@merchant_view(Permission.IMPORT)
def import_preview(request, pk):
    batch = _batch(request, pk)
    if batch.status == ImportBatch.Status.UPLOADED:
        return redirect("import_map", batch.pk)
    results = importing.preview(batch) if batch.status == ImportBatch.Status.MAPPED else []
    same_file = ImportBatch.objects.filter(business=request.business, file_sha256=batch.file_sha256,
                                           status=ImportBatch.Status.COMMITTED).exclude(pk=batch.pk).first()
    show = request.GET.get("show", "all")
    visible = [r for r in results if show == "all" or r["status"] == show]
    return render(request, "ledger/import_preview.html", {
        "batch": batch, "results": visible[:500], "summary": importing.summarise(results), "show": show,
        "same_file": same_file, "truncated": len(visible) > 500, "category_labels": dict(Category.choices),
    })


@merchant_view(Permission.IMPORT)
@require_POST
def import_commit(request, pk):
    batch = _batch(request, pk)
    try:
        batch = importing.commit(batch, request.user, include_repeats=bool(request.POST.get("include_repeats")))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("import_preview", batch.pk)
    quality.refresh(request.business)
    messages.success(request, f"Imported {batch.rows_imported} record(s). Skipped {batch.rows_duplicate} duplicate(s) "
                              f"and {batch.rows_error} row(s) with errors.")
    return redirect("import_preview", batch.pk)


@merchant_view(Permission.IMPORT)
@require_POST
def import_discard(request, pk):
    batch = _batch(request, pk)
    if batch.status in (ImportBatch.Status.UPLOADED, ImportBatch.Status.MAPPED):
        batch.status = ImportBatch.Status.DISCARDED
        batch.save(update_fields=["status"])
        messages.success(request, "Import discarded. Nothing was saved.")
    return redirect("import_list")


@merchant_view(Permission.IMPORT)
def import_template(request):
    content = ("date,type,amount,category,customer_or_supplier,description,reference,payment_method\n"
               f"{today():%Y-%m-%d},in,12500.00,Sales,Walk-in customers,Daily shop sales,R-0001,cash\n"
               f"{today():%Y-%m-%d},out,4300.00,Stock and raw materials,Merkato wholesaler,Green coffee 20kg,S-0457,telebirr\n")
    response = HttpResponse(content, content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="cornerstone-import-template.csv"'
    return response


# ---------- Connected sources ----------


@merchant_view(Permission.REPORTS)
def sources(request):
    return render(request, "ledger/sources.html", {
        "sources": ConnectedSource.objects.filter(business=request.business),
        "can_manage": request.membership.has(Permission.MANAGE),
        "connectors": registry.CONNECTORS.values(),
    })


@merchant_view(Permission.MANAGE)
@require_POST
def source_connect(request):
    source = registry.connect(request.business, request.user, "mock_bank")
    added = registry.sync(source, request.user)
    quality.refresh(request.business)
    messages.success(request, f"Demo source connected and synced: {added} new record(s). These are labelled as demo data.")
    return redirect("sources")


@merchant_view(Permission.MANAGE)
@require_POST
def source_sync(request, pk):
    source = get_object_or_404(ConnectedSource, pk=pk, business=request.business)
    added = registry.sync(source, request.user)
    quality.refresh(request.business)
    messages.success(request, f"Synced: {added} new record(s). Events already received were skipped.")
    return redirect("sources")


# ---------- Documents ----------


@merchant_view(Permission.REPORTS)
@require_http_methods(["GET", "POST"])
def documents(request):
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not request.membership.has(Permission.DOCUMENTS):
            raise PermissionDenied("Your role doesn't include uploading documents.")
        if form.is_valid():
            try:
                services.store_document(request.business, request.user, form.cleaned_data["file"],
                                        form.cleaned_data["kind"], form.cleaned_data["title"])
                messages.success(request, "Uploaded. Link it to a record or invoice to use it as evidence.")
                return redirect("documents")
            except ValidationError as exc:
                form.add_error("file", exc.messages[0])
    docs = Document.objects.filter(business=request.business).prefetch_related("links__transaction", "links__invoice")
    return render(request, "ledger/documents.html", {"form": form, "documents": docs,
                                                     "can_upload": request.membership.has(Permission.DOCUMENTS)})


def _can_open_document(request, document):
    """Members of the owning business, or a platform admin with active support
    access to it. Banks/MFIs never receive documents in this MVP."""
    from merchants.access import memberships_for

    if memberships_for(request.user).filter(business=document.business).exists():
        return True
    if request.user.is_platform_admin:
        return SupportAccess.objects.filter(admin=request.user, business=document.business, expires_at__gt=timezone.now()).exists()
    return False


def document_download(request, public_id):
    if not request.user.is_authenticated:
        raise Http404
    document = get_object_or_404(Document.objects.select_related("blob", "business"), public_id=public_id)
    if not _can_open_document(request, document):
        audit.record("document.denied", request=request, outcome="denied", business=document.business,
                     document=str(document.public_id))
        raise Http404
    audit.record("document.opened", request=request, business=document.business, document=str(document.public_id))
    response = HttpResponse(bytes(document.blob.data), content_type=document.content_type)
    disposition = "inline" if request.GET.get("view") else "attachment"
    safe_name = "".join(c for c in document.original_name if c.isalnum() or c in "._- ")[:100] or "document"
    response["Content-Disposition"] = f'{disposition}; filename="{safe_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response


# ---------- Receivables and obligations ----------


@merchant_view(Permission.REPORTS)
@require_http_methods(["GET", "POST"])
def receivables(request):
    business = request.business
    can_edit = request.membership.has(Permission.RECEIVABLES)
    invoice_form = InvoiceForm(prefix="inv")
    obligation_form = ObligationForm(prefix="obl")
    if request.method == "POST" and can_edit:
        if "inv-number" in request.POST:
            invoice_form = InvoiceForm(request.POST, prefix="inv")
            if invoice_form.is_valid():
                if Invoice.objects.filter(business=business, number=invoice_form.cleaned_data["number"]).exists():
                    invoice_form.add_error("number", "An invoice with this number already exists.")
                else:
                    invoice = invoice_form.save(commit=False)
                    invoice.business, invoice.created_by = business, request.user
                    invoice.save()
                    messages.success(request, f"Invoice {invoice.number} recorded.")
                    return redirect("receivables")
        else:
            obligation_form = ObligationForm(request.POST, prefix="obl")
            if obligation_form.is_valid():
                obligation = obligation_form.save(commit=False)
                obligation.business, obligation.created_by = business, request.user
                obligation.save()
                messages.success(request, f"Obligation “{obligation.name}” recorded.")
                return redirect("receivables")
    invoices = Invoice.objects.filter(business=business).prefetch_related("payments")
    on = today()
    open_invoices = [i for i in invoices if i.status == Invoice.Status.OPEN]
    obligations = RecurringObligation.objects.filter(business=business)
    current = [o for o in obligations if o.is_current(on)]
    return render(request, "ledger/receivables.html", {
        "invoices": invoices, "invoice_form": invoice_form, "obligation_form": obligation_form,
        "obligations": obligations, "can_edit": can_edit, "today": on, "payment_form": PaymentForm(),
        "outstanding": sum((i.outstanding for i in open_invoices), 0),
        "overdue": sum((i.outstanding for i in open_invoices if i.due_date < on), 0),
        "monthly_obligations": sum((o.monthly_equivalent for o in current), 0),
    })


@merchant_view(Permission.RECEIVABLES)
@require_POST
def invoice_payment(request, public_id):
    invoice = get_object_or_404(Invoice, business=request.business, public_id=public_id)
    form = PaymentForm(request.POST)
    if form.is_valid() and invoice.status == Invoice.Status.OPEN:
        amount = min(form.cleaned_data["amount"], invoice.outstanding)
        InvoicePayment.objects.create(invoice=invoice, amount=amount, paid_on=form.cleaned_data["paid_on"], recorded_by=request.user)
        if invoice.outstanding <= 0:
            invoice.status = Invoice.Status.PAID
            invoice.save(update_fields=["status"])
        messages.success(request, f"Payment recorded on {invoice.number}. Record the cash receipt in Transactions if you haven't.")
    return redirect("receivables")


@merchant_view(Permission.RECEIVABLES)
@require_POST
def invoice_void(request, public_id):
    invoice = get_object_or_404(Invoice, business=request.business, public_id=public_id)
    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status"])
    messages.success(request, f"Invoice {invoice.number} voided.")
    return redirect("receivables")


@merchant_view(Permission.RECEIVABLES)
@require_POST
def obligation_end(request, pk):
    obligation = get_object_or_404(RecurringObligation, pk=pk, business=request.business)
    obligation.end_date = today()
    obligation.save(update_fields=["end_date"])
    messages.success(request, f"“{obligation.name}” marked as ended today.")
    return redirect("receivables")


# ---------- Data quality ----------


@merchant_view(Permission.REPORTS)
def findings(request):
    current = quality.refresh(request.business)
    resolved = DataQualityFinding.objects.filter(business=request.business, status=DataQualityFinding.Status.RESOLVED)[:20]
    return render(request, "ledger/findings.html", {"findings": current, "resolved": resolved, "note_form": FindingNoteForm(),
                                                    "can_note": request.membership.has(Permission.RECORD)})


@merchant_view(Permission.RECORD)
@require_POST
def finding_note(request, pk):
    finding = get_object_or_404(DataQualityFinding, pk=pk, business=request.business)
    form = FindingNoteForm(request.POST)
    if form.is_valid():
        finding.merchant_note = form.cleaned_data["note"]
        finding.status = DataQualityFinding.Status.ACKNOWLEDGED
        finding.save(update_fields=["merchant_note", "status", "last_detected_at"])
        messages.success(request, "Explanation saved. It appears next to this indicator in your passport.")
    return redirect("findings")
