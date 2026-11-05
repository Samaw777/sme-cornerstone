from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static

from merchants.access import current_membership
from sharing.portal import analyst_membership


def _icon(path, size, purpose="any"):
    return {"src": static(path), "sizes": f"{size}x{size}", "type": "image/png", "purpose": purpose}


def manifest(request):
    """Makes the site installable as a home-screen app that opens on the home page."""
    data = {
        "name": "SME Cornerstone",
        "short_name": "Cornerstone",
        "description": "Financial records and a shareable Business Financial Passport for small businesses.",
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f5f6f8",
        "theme_color": "#163a6b",
        "icons": [
            _icon("icons/icon-192.png", 192),
            _icon("icons/icon-512.png", 512),
            _icon("icons/icon-maskable-512.png", 512, "maskable"),
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json")


def home(request):
    """Send each person to their workspace."""
    if not request.user.is_authenticated:
        return redirect("login")
    if current_membership(request):
        return redirect("dashboard")
    if analyst_membership(request.user):
        return redirect("bank_home")
    if request.user.is_platform_admin:
        return redirect("console_home")
    return redirect("onboarding")


def glossary(request):
    return render(request, "core/glossary.html")


def permission_denied(request, exception=None):
    return render(request, "403.html", {"reason": str(exception or "")}, status=403)


def not_found(request, exception=None):
    return render(request, "404.html", status=404)
