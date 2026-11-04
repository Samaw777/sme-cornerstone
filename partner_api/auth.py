"""Service authentication and rate limiting for the partner API.

Credentials are institution-scoped bearer tokens `<key_id>.<secret>`; only a
salted hash of the secret is stored. This is the MVP mechanism — a production
pilot should add mTLS or OAuth 2.0 client credentials as agreed with the
partner (see docs/SECURITY.md)."""

import time

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core import audit
from core.audit import client_ip
from core.models import AuditEvent
from sharing.models import PartnerCredential


class ApiError(Exception):
    def __init__(self, status, code, message, headers=None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def _hit(key, limit):
    window = int(time.time() // 60)
    cache_key = f"rl:{key}:{window}"
    cache.add(cache_key, 0, timeout=70)
    count = cache.incr(cache_key)
    if count > limit:
        raise ApiError(429, "rate_limited", "Too many requests. Try again in a minute.", {"Retry-After": str(60 - int(time.time()) % 60)})


def _fail(request, code, message, credential=None, status=401):
    audit.record("api.auth", request=request, actor_label=credential.key_id if credential else "unknown",
                 outcome=AuditEvent.Outcome.DENIED, institution=credential.institution if credential else None,
                 reason=code, path=request.path)
    _hit(f"authfail:{client_ip(request)}", settings.PARTNER_API_AUTH_FAILURE_LIMIT)
    raise ApiError(status, code, message)


def authenticate(request, scope):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    ip_key = f"authfail:{client_ip(request)}"
    if (cache.get(f"rl:{ip_key}:{int(time.time() // 60)}") or 0) >= settings.PARTNER_API_AUTH_FAILURE_LIMIT:
        raise ApiError(429, "rate_limited", "Too many failed attempts. Try again in a minute.")
    if not header.startswith("Bearer "):
        _fail(request, "missing_credentials", "Send an institution API token as 'Authorization: Bearer <token>'.")
    key_id, _, secret = header[7:].strip().partition(".")
    if not key_id or not secret or len(key_id) > 24 or len(secret) > 200:
        _fail(request, "malformed_credentials", "The API token is malformed.")
    credential = PartnerCredential.objects.select_related("institution").filter(key_id=key_id).first()
    if credential is None or not credential.check_secret(secret):
        _fail(request, "invalid_credentials", "The API token is not valid.")
    if not credential.is_usable():
        _fail(request, "credential_inactive", "This API token has expired or been revoked.", credential)
    if scope not in credential.scopes:
        _fail(request, "scope_not_granted", f"This token lacks the '{scope}' scope.", credential, status=403)
    _hit(f"cred:{credential.key_id}", settings.PARTNER_API_RATE_LIMIT)
    PartnerCredential.objects.filter(pk=credential.pk).update(last_used_at=timezone.now())
    return credential
