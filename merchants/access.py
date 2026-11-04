"""Tenant isolation for the merchant workspace.

Every merchant view resolves the business through the signed-in user's active
membership, then filters records by that business. Object lookups always include
`business=` so an id from another tenant simply 404s."""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import BusinessMembership

SESSION_KEY = "current_business"


def memberships_for(user):
    return (
        BusinessMembership.objects.filter(user=user, is_active=True)
        .select_related("business")
        .order_by("business__name")
    )


def current_membership(request):
    if not request.user.is_authenticated:
        return None
    memberships = list(memberships_for(request.user))
    if not memberships:
        return None
    wanted = request.session.get(SESSION_KEY)
    for membership in memberships:
        if str(membership.business.public_id) == wanted:
            return membership
    return memberships[0]


def merchant_view(permission):
    """Require a signed-in member of the current business holding `permission`.
    Sets request.membership and request.business for the view."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            membership = getattr(request, "membership", None) or current_membership(request)
            if membership is None:
                return redirect("onboarding")
            if not membership.has(permission):
                raise PermissionDenied("Your role in this business doesn't include this action.")
            request.membership = membership
            request.business = membership.business
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
