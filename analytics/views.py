import json

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView, TemplateView

from donors.models import BloodGroup, Donor

from . import services
from .ml import features as F
from .models import DemandForecast, ModelKind, RiskLevel, ScreeningCheck, TrainedModel


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


# ---------------------------------------------------------------------------
# Analytics & Predictions pages (sidebar menu in base.html)
# ---------------------------------------------------------------------------

RETURN_BANDS = {
    "high": ("High (60% or more)", Q(return_probability__gte=0.6)),
    "medium": ("Medium (30-60%)", Q(return_probability__gte=0.3, return_probability__lt=0.6)),
    "low": ("Low (under 30%)", Q(return_probability__lt=0.3)),
}


def _staff_only(view):
    return login_required(user_passes_test(lambda u: u.is_staff)(view))


class DemandForecastView(LoginRequiredMixin, TemplateView):
    """Red-cell demand forecast for the next 7 days, per blood group."""

    template_name = "adminlte/analytics/forecasts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        forecasts = list(DemandForecast.objects.select_related("model"))
        if not forecasts:
            forecasts = list(services.refresh_demand_forecasts().values())
        rows = [services.forecast_dict(f) for f in sorted(forecasts, key=lambda f: F.BLOOD_GROUPS.index(f.blood_group))]
        for row in rows:
            row["usable_units"] = row["available_units"] - row["expiring_units"]
        context.update(
            forecasts=rows,
            chart_data={
                "labels": [r["blood_group"] for r in rows],
                "predicted": [r["predicted_units"] for r in rows],
                "available": [r["available_units"] for r in rows],
                "usable": [r["usable_units"] for r in rows],
            },
            totals={
                "predicted": round(sum(r["predicted_units"] for r in rows), 1),
                "available": sum(r["available_units"] for r in rows),
                "expiring": sum(r["expiring_units"] for r in rows),
                "high": sum(1 for r in rows if r["risk_level"] == RiskLevel.HIGH),
            },
            model=forecasts[0].model if forecasts else None,
            updated_at=forecasts[0].updated_at if forecasts else None,
            horizon_days=F.FORECAST_HORIZON_DAYS,
        )
        return context


class DonorPredictionListView(LoginRequiredMixin, ListView):
    """Every scored donor with their chance of donating again within 180 days."""

    template_name = "adminlte/analytics/donor_predictions.html"
    context_object_name = "predictions"
    paginate_by = 25

    def get_queryset(self):
        qs = services.donor_prediction_queryset()
        params = self.request.GET
        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(donor__first_name__icontains=search) | Q(donor__last_name__icontains=search)
                | Q(donor__national_id__icontains=search) | Q(donor__phone__icontains=search)
            )
        if params.get("blood_group") in BloodGroup.values:
            qs = qs.filter(donor__blood_group=params["blood_group"])
        if params.get("band") in RETURN_BANDS:
            qs = qs.filter(RETURN_BANDS[params["band"]][1])
        if params.get("district"):
            qs = qs.filter(donor__district__icontains=params["district"].strip())
        if params.get("eligible") == "1":
            qs = qs.filter(services.eligible_donor_filter(timezone.localdate()))
        order = "return_probability" if params.get("order") == "asc" else "-return_probability"
        return qs.order_by(order, "donor__last_name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_predictions = services.donor_prediction_queryset()
        context.update(
            blood_groups=BloodGroup.values,
            bands=[(key, label) for key, (label, _) in RETURN_BANDS.items()],
            summary={
                "scored": all_predictions.count(),
                "high": all_predictions.filter(RETURN_BANDS["high"][1]).count(),
                "low": all_predictions.filter(RETURN_BANDS["low"][1]).count(),
                "eligible_now": all_predictions.filter(services.eligible_donor_filter(timezone.localdate())).count(),
            },
            query=self._query_without_page(),
            model=TrainedModel.objects.filter(kind=ModelKind.DONOR_RETURN, is_active=True).first(),
        )
        return context

    def _query_without_page(self):
        query = self.request.GET.copy()
        query.pop("page", None)
        return query.urlencode()


class ScreeningCheckListView(LoginRequiredMixin, ListView):
    """History of every deferral-risk screening (web form, API, simulator)."""

    template_name = "adminlte/analytics/screening_list.html"
    context_object_name = "screenings"
    paginate_by = 25

    def get_queryset(self):
        qs = ScreeningCheck.objects.select_related("donor", "created_by", "model")
        params = self.request.GET
        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(donor__first_name__icontains=search) | Q(donor__last_name__icontains=search)
                | Q(donor__national_id__icontains=search)
            )
        if params.get("risk") in RiskLevel.values:
            qs = qs.filter(risk_level=params["risk"])
        if params.get("source"):
            qs = qs.filter(source=params["source"])
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_checks = ScreeningCheck.objects.all()
        query = self.request.GET.copy()
        query.pop("page", None)
        context.update(
            risk_levels=RiskLevel.choices,
            sources=list(all_checks.order_by("source").values_list("source", flat=True).distinct()),
            summary={level: all_checks.filter(risk_level=level).count() for level in RiskLevel.values},
            total=all_checks.count(),
            query=query.urlencode(),
        )
        return context


class TrainedModelListView(LoginRequiredMixin, ListView):
    """All trained versions of the three models; the active one serves predictions."""

    template_name = "adminlte/analytics/model_list.html"
    context_object_name = "models"

    def get_queryset(self):
        return TrainedModel.objects.order_by("kind", "-version")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cards"] = services.model_cards()
        context["groups"] = [
            (kind.label, [m for m in context["models"] if m.kind == kind.value]) for kind in ModelKind
        ]
        return context


class TrainedModelDetailView(LoginRequiredMixin, DetailView):
    """One model version: its metrics and what each feature contributes."""

    model = TrainedModel
    template_name = "adminlte/analytics/model_detail.html"
    context_object_name = "trained_model"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        record = self.object
        payload = None
        path = settings.ML_MODELS_DIR / record.file_name
        if path.exists():
            payload = json.loads(path.read_text())
        weights = []
        if payload:
            coef = payload["model"]["coef"]
            biggest = max((abs(c) for c in coef), default=1) or 1
            for name, c in zip(payload["feature_names"], coef):
                weights.append({
                    "name": name,
                    "label": FEATURE_DESCRIPTIONS.get(name, name.replace("_", " ")),
                    "weight": round(c, 3),
                    "width": round(100 * abs(c) / biggest),
                })
            weights.sort(key=lambda w: abs(w["weight"]), reverse=True)
        metrics = {k: v for k, v in record.metrics.items() if not isinstance(v, dict)}
        context.update(
            file_missing=payload is None,
            weights=weights,
            metrics=metrics,
            confusion=record.metrics.get("confusion"),
            is_classifier=record.kind != ModelKind.DEMAND,
            other_versions=TrainedModel.objects.filter(kind=record.kind).exclude(pk=record.pk).order_by("-version")[:10],
        )
        return context


FEATURE_DESCRIPTIONS = {
    # demand forecast
    "units_last_7d": "Units requested in the last 7 days",
    "units_per_week_last_14d": "Weekly average, last 14 days",
    "units_per_week_last_28d": "Weekly average, last 28 days",
    "week_over_week_change": "Change versus the week before",
    "month_sin": "Season (sine of day of year)",
    "month_cos": "Season (cosine of day of year)",
    # donor return
    "log_days_since_last_donation": "Days since last donation (log)",
    "collected_donations": "Number of donations",
    "total_volume_litres": "Total volume donated (litres)",
    "log_days_since_first_visit": "Time since first visit (log)",
    "deferrals": "Number of deferrals",
    "donations_per_year": "Donations per year",
    "campaign_share": "Share of donations made at campaigns",
    "eligible_now": "Eligible to donate today",
    # deferral risk
    "age_years": "Age",
    "is_male": "Male",
    "weight_kg": "Body weight (kg)",
    "systolic_bp": "Systolic blood pressure",
    "diastolic_bp": "Diastolic blood pressure",
    "bp_over_limit": "Blood pressure above 160/100",
    "first_time_donor": "First-time donor",
    "days_since_last_collected": "Days since last donation",
    "previous_deferrals": "Earlier deferrals",
    "previous_collected": "Earlier donations",
    "last_hemoglobin": "Last haemoglobin result",
}
for _group in F.BLOOD_GROUPS:
    FEATURE_DESCRIPTIONS[f"group_{_group}"] = f"Blood group {_group}"


@_staff_only
@require_POST
def retrain_models(request):
    """Read the data through the API again and train a new version of each model."""
    from .client import LocalApiClient
    from .ml.training import train_all

    results = train_all(LocalApiClient(), log=lambda *args: None)
    trained = [r for r in results.values() if isinstance(r, TrainedModel)]
    for kind, result in results.items():
        if not isinstance(result, TrainedModel):
            messages.warning(request, f"{ModelKind(kind).label} was not trained: {result}")
    if trained:
        services.refresh_demand_forecasts()
        services.refresh_all_donor_predictions()
        names = ", ".join(str(r) for r in trained)
        messages.success(request, f"Trained and deployed: {names}. All predictions were refreshed.")
    return redirect("analytics_models")


@_staff_only
@require_POST
def activate_model(request, pk):
    """Serve predictions from an earlier (or later) version of a model."""
    record = get_object_or_404(TrainedModel, pk=pk)
    if not (settings.ML_MODELS_DIR / record.file_name).exists():
        messages.error(request, f"The model file {record.file_name} is missing; retrain instead.")
        return redirect("analytics_model_detail", pk=pk)
    with transaction.atomic():
        TrainedModel.objects.filter(kind=record.kind).update(is_active=False)
        TrainedModel.objects.filter(pk=pk).update(is_active=True)
    if record.kind == ModelKind.DEMAND:
        services.refresh_demand_forecasts()
    elif record.kind == ModelKind.DONOR_RETURN:
        services.refresh_all_donor_predictions()
    messages.success(request, f"{record} is now the active version.")
    return redirect("analytics_model_detail", pk=pk)


@_staff_only
@require_POST
def refresh_predictions(request):
    forecasts = services.refresh_demand_forecasts()
    scored = services.refresh_all_donor_predictions()
    if not forecasts and not scored:
        messages.warning(request, "No trained models yet. Use 'Retrain models' on the Trained Models page.")
    else:
        messages.success(request, f"Updated {len(forecasts)} blood-group forecasts and {scored} donor predictions.")
    return redirect(request.POST.get("next") or "analytics_forecasts")
