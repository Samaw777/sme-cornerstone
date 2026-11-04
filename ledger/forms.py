from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from core.periods import today
from merchants.forms import DateInput

from .importing import DATE_FORMATS, FIELDS
from .models import (
    INFLOW_CATEGORIES, OUTFLOW_CATEGORIES, Annotation, Category, Direction, Document, Invoice, PaymentMethod,
    RecurringObligation,
)

AMOUNT_ATTRS = {"inputmode": "decimal", "placeholder": "0.00", "step": "0.01", "min": "0.01"}


class TransactionForm(forms.Form):
    direction = forms.ChoiceField(label=_("Type"), choices=Direction.choices, widget=forms.RadioSelect, initial=Direction.INFLOW)
    occurred_on = forms.DateField(label=_("Date"), widget=DateInput, initial=today)
    amount = forms.DecimalField(label=_("Amount (ETB)"), min_value=Decimal("0.01"), max_digits=16, decimal_places=2,
                                widget=forms.NumberInput(attrs=AMOUNT_ATTRS))
    category = forms.ChoiceField(label=_("Category"), choices=Category.choices)
    counterparty = forms.CharField(label=_("Customer or supplier"), max_length=120, required=False)
    description = forms.CharField(label=_("Description"), max_length=200, required=False)
    reference = forms.CharField(label=_("Receipt or reference number"), max_length=80, required=False)
    payment_method = forms.ChoiceField(label=_("Paid by"), choices=[("", _("Not specified"))] + PaymentMethod.choices, required=False)

    def clean_occurred_on(self):
        value = self.cleaned_data["occurred_on"]
        if value > today():
            raise forms.ValidationError(_("The date can't be in the future."))
        if value.year < 2000:
            raise forms.ValidationError(_("Check the year."))
        return value

    def clean(self):
        data = super().clean()
        direction, category = data.get("direction"), data.get("category")
        allowed = INFLOW_CATEGORIES if direction == Direction.INFLOW else OUTFLOW_CATEGORIES
        if direction and category and category not in allowed:
            self.add_error("category", _("That category doesn't match the type (money in / money out)."))
        data["currency"] = "ETB"
        return data


class CorrectionReasonForm(forms.Form):
    reason = forms.CharField(label=_("Reason for the correction"), max_length=300,
                             widget=forms.TextInput(attrs={"placeholder": _("e.g. Amount typed wrongly; receipt shows 4,500")}))


class AmendForm(TransactionForm, CorrectionReasonForm):
    pass


class AnnotationForm(forms.Form):
    kind = forms.ChoiceField(choices=Annotation.Kind.choices, widget=forms.RadioSelect, initial=Annotation.Kind.NOTE)
    text = forms.CharField(label=_("Note"), max_length=500, widget=forms.Textarea(attrs={"rows": 2}))


class DocumentForm(forms.Form):
    file = forms.FileField(label=_("File (PDF, JPG or PNG, up to 5 MB)"))
    kind = forms.ChoiceField(label=_("Document type"), choices=Document.Kind.choices)
    title = forms.CharField(label=_("Title"), max_length=160, required=False,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. Supplier receipt #1182")}))


class ImportUploadForm(forms.Form):
    file = forms.FileField(label=_("CSV file"))


class MappingForm(forms.Form):
    date_format = forms.ChoiceField(label=_("Date format in the file"), choices=[(k, v[1]) for k, v in DATE_FORMATS.items()])

    LABELS = {
        "date": _("Date *"), "amount": _("Amount (one column; negative = money out)"), "money_in": _("Money in"),
        "money_out": _("Money out"), "direction": _("Direction (in/out, credit/debit)"), "category": _("Category"),
        "description": _("Description"), "counterparty": _("Customer or supplier"), "reference": _("Reference / receipt no."),
        "payment_method": _("Paid by (cash / telebirr / bank)"),
    }

    def __init__(self, *args, headers=(), **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", _("— not in file —"))] + [(h, h) for h in headers]
        for field in FIELDS:
            self.fields[field] = forms.ChoiceField(label=self.LABELS[field], choices=choices, required=False)


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["number", "customer", "issue_date", "due_date", "amount", "notes"]
        widgets = {"issue_date": DateInput, "due_date": DateInput, "amount": forms.NumberInput(attrs=AMOUNT_ATTRS)}

    def clean(self):
        data = super().clean()
        if data.get("issue_date") and data.get("due_date") and data["due_date"] < data["issue_date"]:
            self.add_error("due_date", _("The due date is before the issue date."))
        return data


class PaymentForm(forms.Form):
    amount = forms.DecimalField(label=_("Amount received (ETB)"), min_value=Decimal("0.01"), max_digits=16, decimal_places=2,
                                widget=forms.NumberInput(attrs=AMOUNT_ATTRS))
    paid_on = forms.DateField(label=_("Date received"), widget=DateInput, initial=today)


class ObligationForm(forms.ModelForm):
    class Meta:
        model = RecurringObligation
        fields = ["name", "kind", "counterparty", "amount", "frequency", "start_date", "end_date", "notes"]
        widgets = {"start_date": DateInput, "end_date": DateInput, "amount": forms.NumberInput(attrs=AMOUNT_ATTRS)}

    def clean(self):
        data = super().clean()
        if data.get("end_date") and data.get("start_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", _("The end date is before the start date."))
        return data


class FindingNoteForm(forms.Form):
    note = forms.CharField(label=_("Your explanation"), max_length=500, widget=forms.Textarea(attrs={"rows": 2}))
