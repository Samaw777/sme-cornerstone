import datetime as dt

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.periods import add_months, month_end, month_start, parse_month, today

from .models import CATEGORY_HELP, ConsentGrant, DataCategory, FinancialInstitution

MAX_CONSENT_DAYS = 180


class MonthInput(forms.TextInput):
    input_type = "month"


class ConsentForm(forms.Form):
    institution = forms.ModelChoiceField(label=_("Receiving institution"), queryset=FinancialInstitution.objects.filter(is_active=True),
                                         empty_label=_("Choose a bank or MFI"))
    purpose = forms.ChoiceField(label=_("Purpose of sharing"), choices=ConsentGrant.Purpose.choices)
    purpose_detail = forms.CharField(label=_("Details (optional)"), max_length=300, required=False,
                                     widget=forms.TextInput(attrs={"placeholder": _("e.g. Working-capital application, ref. 2291")}))
    categories = forms.MultipleChoiceField(label=_("Data the institution may see"), widget=forms.CheckboxSelectMultiple,
                                           choices=DataCategory.choices)
    period_from = forms.CharField(label=_("Reporting period from"), widget=MonthInput)
    period_to = forms.CharField(label=_("to"), widget=MonthInput)
    expires_on = forms.DateField(label=_("Access expires on"), widget=forms.DateInput(attrs={"type": "date"}),
                                 help_text=_("Up to 180 days from today. You can revoke earlier at any time."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        end = month_start(today())
        self.fields["period_from"].initial = add_months(end, -11).strftime("%Y-%m")
        self.fields["period_to"].initial = end.strftime("%Y-%m")
        self.fields["expires_on"].initial = today() + dt.timedelta(days=30)
        self.fields["categories"].initial = [DataCategory.PROFILE, DataCategory.FINANCIAL_SUMMARY,
                                             DataCategory.VERIFICATION_SUMMARY, DataCategory.DATA_QUALITY]
        self.category_help = CATEGORY_HELP

    def clean(self):
        data = super().clean()
        try:
            start = parse_month(data.get("period_from", ""))
            end = month_end(parse_month(data.get("period_to", "")))
        except ValueError as exc:
            raise forms.ValidationError(str(exc))
        if start > end:
            raise forms.ValidationError(_("The reporting period starts after it ends."))
        if end > month_end(today()):
            raise forms.ValidationError(_("The reporting period can't extend into future months."))
        if (end.year - start.year) * 12 + end.month - start.month + 1 > 36:
            raise forms.ValidationError(_("Share at most 36 months."))
        expires = data.get("expires_on")
        if expires:
            if expires <= today():
                self.add_error("expires_on", _("Choose a date after today."))
            elif expires > today() + dt.timedelta(days=MAX_CONSENT_DAYS):
                self.add_error("expires_on", _("Consent can last at most 180 days."))
        data["period_start"], data["period_end"] = start, end
        if expires:
            data["expires_at"] = timezone.make_aware(dt.datetime.combine(expires, dt.time(23, 59, 59)))
        return data


class RevokeForm(forms.Form):
    reason = forms.CharField(label=_("Reason (optional, shown in your history)"), max_length=300, required=False)
    understood = forms.BooleanField(label=_("I understand revoking stops future access but can't recall copies already downloaded."))
