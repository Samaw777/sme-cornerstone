from django import forms
from django.utils.translation import gettext_lazy as _

from merchants.forms import DateInput
from sharing.models import FinancialInstitution, PartnerCredential

from .models import OperationalIssue, PilotFeedback, PilotMetricEntry, PilotProgram


class PilotProgramForm(forms.ModelForm):
    class Meta:
        model = PilotProgram
        fields = ["name", "institution", "start_date", "end_date", "target_merchants", "evaluation_design", "is_active"]
        widgets = {"start_date": DateInput, "end_date": DateInput, "evaluation_design": forms.Textarea(attrs={"rows": 3})}


class MetricEntryForm(forms.ModelForm):
    class Meta:
        model = PilotMetricEntry
        fields = ["metric", "value", "period_start", "period_end", "source", "notes"]
        widgets = {"period_start": DateInput, "period_end": DateInput}

    def clean(self):
        data = super().clean()
        if data.get("period_start") and data.get("period_end") and data["period_end"] < data["period_start"]:
            self.add_error("period_end", _("The end is before the start."))
        if data.get("value") is not None and data["value"] < 0:
            self.add_error("value", _("Use a value of zero or more."))
        return data


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = PilotFeedback
        fields = ["role", "rating", "comment", "collected_on"]
        widgets = {"collected_on": DateInput, "comment": forms.Textarea(attrs={"rows": 2})}


class IssueForm(forms.ModelForm):
    class Meta:
        model = OperationalIssue
        fields = ["title", "description", "business", "institution"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class InstitutionForm(forms.ModelForm):
    class Meta:
        model = FinancialInstitution
        fields = ["name", "kind", "contact_email", "is_pilot_partner"]


class AnalystForm(forms.Form):
    email = forms.EmailField()
    name = forms.CharField(max_length=120)


class CredentialForm(forms.Form):
    label = forms.CharField(max_length=80, initial="Pilot integration")
    scopes = forms.MultipleChoiceField(choices=PartnerCredential.Scope.choices, widget=forms.CheckboxSelectMultiple,
                                       initial=[s.value for s in PartnerCredential.Scope])
    days = forms.IntegerField(label=_("Valid for (days)"), min_value=1, max_value=365, initial=90)


class SupportAccessForm(forms.Form):
    reason = forms.CharField(max_length=300, label=_("Reason (shown to the business)"),
                             widget=forms.TextInput(attrs={"placeholder": _("e.g. Merchant ticket #42: import failing")}))


class IdentityValidationForm(forms.Form):
    FIELD_CHOICES = [("tin", _("TIN")), ("trade_license_number", _("Trade licence number"))]
    field = forms.ChoiceField(choices=FIELD_CHOICES)
    note = forms.CharField(max_length=300, label=_("What it was checked against"),
                           widget=forms.TextInput(attrs={"placeholder": _("e.g. Trade licence copy, document reviewed in person")}))
