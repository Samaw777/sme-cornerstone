import logging

from .models import AuditEvent

log = logging.getLogger("cornerstone.audit")

# Keys that must never reach the audit table or logs, even by accident.
_FORBIDDEN_KEYS = {"secret", "password", "token", "authorization", "payload", "file", "content"}


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR")) or None


def record(action, *, request=None, user=None, actor_label="", outcome=AuditEvent.Outcome.SUCCESS,
           business=None, institution=None, **detail):
    """Write one audit event. Pass identifiers and reasons as keyword details."""
    clean = {k: v for k, v in detail.items() if k.lower() not in _FORBIDDEN_KEYS}
    if user is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        user = request.user
    event = AuditEvent.objects.create(
        action=action,
        outcome=outcome,
        actor_user=user,
        actor_label=actor_label or (user.email if user else ""),
        business=business,
        institution=institution,
        ip_address=client_ip(request),
        detail=clean,
    )
    log.info("audit action=%s outcome=%s event=%s", action, outcome, event.pk)
    return event
