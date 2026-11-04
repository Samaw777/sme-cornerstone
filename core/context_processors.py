from django.conf import settings


def workspace(request):
    """Navigation context: which workspace the page belongs to and what the
    signed-in person can switch between."""
    context = {
        "demo_mode": settings.DEMO_MODE,
        "period_presets": [("3m", "3 months"), ("6m", "6 months"), ("12m", "12 months"), ("24m", "24 months")],
    }
    if settings.DEMO_MODE:
        from .demo import DEMO_ACCOUNTS

        context["demo_accounts"] = DEMO_ACCOUNTS
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return context
    from merchants.access import memberships_for
    from sharing.portal import analyst_membership

    path = request.path
    context.update(
        workspace_kind="bank" if path.startswith("/bank") else "console" if path.startswith("/console") else "merchant",
        membership=request.membership or None,
        all_memberships=list(memberships_for(user)),
        analyst=analyst_membership(user),
        is_platform_admin=user.is_platform_admin,
    )
    membership = context["membership"]
    if membership and membership.can_approve:
        from ledger.models import Transaction

        context["pending_count"] = Transaction.objects.filter(business=membership.business, status=Transaction.Status.PENDING).count()
    return context
