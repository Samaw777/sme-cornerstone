from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core import audit

from .forms import SignUpForm


@require_http_methods(["GET", "POST"])
def signup(request):
    """Self sign-up is for business owners. Staff are added by an owner; bank
    analysts and administrators are created by the platform."""
    if request.user.is_authenticated:
        return redirect("home")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        audit.record("account.signup", request=request, user=user)
        return redirect("onboarding")
    return render(request, "registration/signup.html", {"form": form})
