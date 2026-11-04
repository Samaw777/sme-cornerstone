"""Write paths for financial records. Views call these rather than touching
models directly, so the integrity rules live in one place."""

import hashlib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone

from core import audit

from .models import (
    Annotation, Correction, Document, DocumentBlob, EvidenceLink, SourceEvent, SourceKind, Transaction,
)

ALLOWED_DOCUMENT_TYPES = {
    # content type: (extensions, magic-byte prefixes)
    "application/pdf": ((".pdf",), (b"%PDF-",)),
    "image/jpeg": ((".jpg", ".jpeg"), (b"\xff\xd8\xff",)),
    "image/png": ((".png",), (b"\x89PNG\r\n\x1a\n",)),
}


def _record_fields(data):
    return {
        "direction": data["direction"],
        "category": data["category"],
        "amount": str(data["amount"]),
        "currency": data.get("currency", "ETB"),
        "occurred_on": str(data["occurred_on"]),
        "description": data.get("description", ""),
        "counterparty": data.get("counterparty", ""),
        "reference": data.get("reference", ""),
    }


@db_transaction.atomic
def record_manual(business, user, data):
    """A merchant-entered record. Its first version is preserved as a source event."""
    fields = _record_fields(data)
    event = SourceEvent.objects.create(
        business=business, source_kind=SourceKind.MANUAL, payload=fields, received_by=user
    )
    txn = Transaction.objects.create(
        business=business, source_kind=SourceKind.MANUAL, source_event=event, created_by=user,
        is_demo=data.get("is_demo", False), **{k: data[k] for k in data if k in _MODEL_FIELDS},
    )
    return txn


_MODEL_FIELDS = {"direction", "category", "amount", "currency", "occurred_on", "description", "counterparty", "reference"}


def _require_correctable(original, user):
    if original.status != Transaction.Status.ACTIVE:
        raise ValidationError("Only the current version of a record can be corrected.")
    if original.is_system_sourced:
        # System-sourced events stay as received; the merchant can annotate or dispute them.
        raise ValidationError(
            "Records from a connected source can't be amended or reversed. Add a note or open a dispute instead."
        )


@db_transaction.atomic
def amend(original, user, data, reason):
    """Replace a record with a corrected version. The original stays, marked superseded."""
    original = Transaction.objects.select_for_update().get(pk=original.pk)
    _require_correctable(original, user)
    if not reason.strip():
        raise ValidationError("A reason is required for every correction.")
    replacement = Transaction.objects.create(
        business=original.business,
        source_kind=SourceKind.CORRECTION,
        source_event=original.source_event,
        import_batch=original.import_batch,
        is_demo=original.is_demo,
        created_by=user,
        **{k: data[k] for k in data if k in _MODEL_FIELDS},
    )
    # Documents linked to the original remain evidence for the corrected record.
    for link in original.evidence_links.all():
        EvidenceLink.objects.create(document=link.document, transaction=replacement, linked_by=user)
    replacement.refresh_evidence_class()
    original.status = Transaction.Status.SUPERSEDED
    original.save(update_fields=["status"])
    correction = Correction.objects.create(
        business=original.business, kind=Correction.Kind.AMENDMENT, original=original,
        replacement=replacement, reason=reason.strip(), author=user,
    )
    audit.record("record.amended", user=user, business=original.business,
                 original=str(original.public_id), replacement=str(replacement.public_id), correction=correction.pk)
    return replacement


@db_transaction.atomic
def reverse(original, user, reason):
    original = Transaction.objects.select_for_update().get(pk=original.pk)
    _require_correctable(original, user)
    if not reason.strip():
        raise ValidationError("A reason is required for every correction.")
    original.status = Transaction.Status.REVERSED
    original.save(update_fields=["status"])
    correction = Correction.objects.create(
        business=original.business, kind=Correction.Kind.REVERSAL, original=original, reason=reason.strip(), author=user
    )
    audit.record("record.reversed", user=user, business=original.business, original=str(original.public_id), correction=correction.pk)
    return correction


def annotate(txn, user, kind, text):
    if not text.strip():
        raise ValidationError("Write a note.")
    return Annotation.objects.create(business=txn.business, transaction=txn, kind=kind, text=text.strip(), author=user)


def resolve_dispute(annotation):
    annotation.is_open = False
    annotation.resolved_at = timezone.now()
    annotation.save(update_fields=["is_open", "resolved_at"])


def validate_upload(upload):
    """Basic file checks: size, extension, declared type and the file's own
    signature must all agree. Content is not judged — that needs a reviewer."""
    if upload.size > settings.DOCUMENT_MAX_BYTES:
        raise ValidationError(f"Files can be up to {settings.DOCUMENT_MAX_BYTES // (1024 * 1024)} MB.")
    name = upload.name.lower()
    head = upload.read(16)
    upload.seek(0)
    for content_type, (extensions, signatures) in ALLOWED_DOCUMENT_TYPES.items():
        if name.endswith(extensions) and any(head.startswith(sig) for sig in signatures):
            return content_type
    raise ValidationError("Upload a PDF, JPG or PNG file. The file's contents must match its extension.")


@db_transaction.atomic
def store_document(business, user, upload, kind, title, is_demo=False):
    content_type = validate_upload(upload)
    data = upload.read()
    digest = hashlib.sha256(data).hexdigest()
    existing = Document.objects.filter(business=business, sha256=digest).first()
    if existing:
        raise ValidationError(f"This exact file is already uploaded as “{existing.title}”.")
    document = Document.objects.create(
        business=business, kind=kind, title=title or upload.name[:160], original_name=upload.name[:200],
        content_type=content_type, size=len(data), sha256=digest, uploaded_by=user, is_demo=is_demo,
    )
    DocumentBlob.objects.create(document=document, data=data)
    audit.record("document.uploaded", user=user, business=business, document=str(document.public_id))
    return document


def link_document(document, user, *, transaction=None, invoice=None):
    target = transaction or invoice
    if document.business_id != target.business_id:
        raise ValidationError("Documents can only be linked within the same business.")
    if transaction is not None and transaction.status != Transaction.Status.ACTIVE:
        raise ValidationError("Link evidence to the current version of the record.")
    link, _ = EvidenceLink.objects.get_or_create(document=document, transaction=transaction, invoice=invoice, defaults={"linked_by": user})
    if transaction is not None:
        transaction.refresh_evidence_class()
    return link


def unlink_document(link):
    txn = link.transaction
    link.delete()
    if txn is not None:
        txn.refresh_evidence_class()


def set_document_validation(document, user, status, note=""):
    """Platform-side review. Rejecting a document downgrades linked records."""
    document.validation_status = status
    document.validation_note = note
    document.save(update_fields=["validation_status", "validation_note"])
    for link in document.links.filter(transaction__isnull=False).select_related("transaction"):
        link.transaction.refresh_evidence_class()
    audit.record("document.validation_changed", user=user, business=document.business,
                 document=str(document.public_id), status=status)

