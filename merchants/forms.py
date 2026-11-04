from django import forms
from django.utils.translation import gettext_lazy as _

from core.periods import today

from .models import STAFF_GRANTABLE, Business, Permission


class DateInput(forms.DateInput):
    input_type = "date"


class BusinessForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ["name", "legal_name", "sector", "operating_start_date", "region", "city", "sub_city", "phone",
                  "tin", "trade_license_number"]
        widgets = {
            "operating_start_date": DateInput,
            "name": forms.TextInput(attrs={"placeholder": _("e.g. Abebe Coffee Roasters")}),
            "city": forms.TextInput(attrs={"placeholder": _("e.g. Addis Ababa")}),
            "sub_city": forms.TextInput(attrs={"placeholder": _("e.g. Bole, Woreda 03")}),
            "tin": forms.TextInput(attrs={"inputmode": "numeric", "placeholder": _("Optional")}),
            "trade_license_number": forms.TextInput(attrs={"placeholder": _("Optional")}),
            "legal_name": forms.TextInput(attrs={"placeholder": _("Optional — as on the trade licence")}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "+251 9…"}),
        }
        help_texts = {
            "tin": _("Shown as merchant-provided until it's validated against a document."),
            "trade_license_number": _("Shown as merchant-provided until it's validated against a document."),
        }

    def clean_operating_start_date(self):
        value = self.cleaned_data["operating_start_date"]
        if value > today():
            raise forms.ValidationError(_("The operating start date can't be in the future."))
        if value.year < 1950:
            raise forms.ValidationError(_("Check the year."))
        return value

    def clean_tin(self):
        value = self.cleaned_data["tin"].strip()
        if value and (not value.isdigit() or not 9 <= len(value) <= 13):
            raise forms.ValidationError(_("A TIN is 9 to 13 digits."))
        return value

    def save(self, commit=True):
        business = super().save(commit=False)
        # Any change to a validated identity value returns it to merchant-provided.
        for field, status_field in (("tin", "tin_status"), ("trade_license_number", "trade_license_status")):
            if field in self.changed_data and getattr(business, field):
                setattr(business, status_field, "merchant_provided")
        business.refresh_identity_statuses()
        if commit:
            business.save()
        return business


class StaffForm(forms.Form):
    email = forms.EmailField(label=_("Staff email"))
    name = forms.CharField(label=_("Full name"), max_length=120)
    permissions = forms.MultipleChoiceField(
        label=_("Permissions"),
        choices=[(p.value, p.label) for p in STAFF_GRANTABLE],
        widget=forms.CheckboxSelectMultiple,
        initial=[Permission.RECORD, Permission.DOCUMENTS],
        required=False,
        help_text=_("Staff can never authorise sharing with a bank or MFI."),
    )


class StaffPermissionsForm(forms.Form):
    permissions = forms.MultipleChoiceField(
        choices=[(p.value, p.label) for p in STAFF_GRANTABLE], widget=forms.CheckboxSelectMultiple, required=False
    )
