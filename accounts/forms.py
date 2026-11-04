from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import User


class SignUpForm(UserCreationForm):
    usable_password = None

    class Meta:
        model = User
        fields = ["name", "email"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": _("e.g. Abebe Kebede"), "autofocus": True}),
            "email": forms.EmailInput(attrs={"placeholder": "you@example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = True
        for field in self.fields.values():
            field.help_text = ""

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("An account with this email already exists."))
        return email
