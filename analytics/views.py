from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import FormView

from donors.models import Donor

from . import services
from .models import ScreeningCheck


class ScreeningForm(forms.Form):
    national_id = forms.CharField(max_length=16, label="Donor national ID")
    systolic_bp = forms.IntegerField(min_value=60, max_value=250, label="Systolic BP (mmHg)")
    diastolic_bp = forms.IntegerField(min_value=30, max_value=150, label="Diastolic BP (mmHg)")
    weight_kg = forms.DecimalField(max_digits=4, decimal_places=1, required=False,
                                   label="Weight today (kg)", help_text="Leave empty to use the registered weight.")

    def clean_national_id(self):
        value = self.cleaned_data["national_id"].strip()
        donor = Donor.objects.filter(national_id=value).first()
        if donor is None:
            raise forms.ValidationError("No donor is registered with this national ID.")
        self.donor = donor
        return value


class ScreeningView(LoginRequiredMixin, FormView):
    """Pre-donation check: predicts how likely this visit ends in deferral."""

    template_name = "adminlte/analytics/screening.html"
    form_class = ScreeningForm

    def get_initial(self):
        initial = super().get_initial()
        donor_id = self.request.GET.get("donor")
        donor = Donor.objects.filter(pk=donor_id).first() if donor_id and donor_id.isdigit() else None
        if donor:
            initial["national_id"] = donor.national_id
        return initial

    def form_valid(self, form):
        try:
            screening = services.screen_donor(
                form.donor,
                systolic_bp=form.cleaned_data["systolic_bp"],
                diastolic_bp=form.cleaned_data["diastolic_bp"],
                weight_kg=form.cleaned_data["weight_kg"],
                source="web form",
                user=self.request.user,
            )
        except services.ModelNotTrained as error:
            messages.error(self.request, str(error))
            return self.form_invalid(form)
        return self.render_to_response(self.get_context_data(
            form=self.form_class(), result=screening,
            eligible=form.donor.is_eligible_on(screening.screening_date),
        ))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["recent"] = ScreeningCheck.objects.select_related("donor")[:15]
        return context
