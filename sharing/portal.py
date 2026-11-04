"""Bank/MFI analyst portal access: an analyst works for exactly one institution
and sees only businesses that have granted that institution active consent."""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import InstitutionMembership


def analyst_membership(user):
    if not user.is_authenticated:
        return None
    return (InstitutionMembership.objects.filter(user=user, is_active=True, institution__is_active=True)
            .select_related("institution").first())


def analyst_view(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        membership = analyst_membership(request.user)
        if membership is None:
            raise PermissionDenied("This area is for bank and MFI analysts.")
        request.institution = membership.institution
        return view(request, *args, **kwargs)

    return wrapped
