"""Deployed models: load trained models and turn new data into predictions."""

import json
import threading
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from donors.models import BloodUnit, Donation, DonationOutcome, Donor
from inventory.models import BloodRequest, BloodRequestStatus

from . import serializers
from .ml import features as F
from .ml.algorithms import LogisticRegression, RidgeRegression
from .models import (
    DemandForecast,
    DonorPrediction,
    ModelKind,
    RiskLevel,
    ScreeningCheck,
    TrainedModel,
)

ALGORITHMS = {
    "ridge_regression": RidgeRegression,
    "logistic_regression": LogisticRegression,
}

def _factor_label(name, value):
    """Readable description of a feature value, e.g. 'Systolic BP 152'."""
    if name == "is_male":
        return "Male donor" if value else "Female donor"
    if name == "bp_over_limit":
        return "Blood pressure above donation limit" if value else "Blood pressure within limit"
    if name == "first_time_donor":
        return "First-time donor" if value else "Returning donor"
    labels = {
        "age_years": "Age {:.0f}",
        "weight_kg": "Weight {:.1f} kg",
        "systolic_bp": "Systolic BP {:.0f}",
        "diastolic_bp": "Diastolic BP {:.0f}",
        "days_since_last_collected": "{:.0f} days since last donation",
        "previous_deferrals": "{:.0f} earlier deferral(s)",
        "previous_collected": "{:.0f} earlier donation(s)",
        "last_hemoglobin": "Last Hb {:.1f} g/dL",
    }
    return labels.get(name, name + " {}").format(value)


# A factor is listed only if it raises the log-odds of deferral by at least this much.
MIN_FACTOR_IMPACT = 0.2

INVITE_LIMIT = 10


class ModelNotTrained(Exception):
    pass


# ------------------------------------------------------- model loading ---

_cache = {}
_cache_lock = threading.Lock()


def get_active_model(kind):
    """Return (TrainedModel, estimator) for the active version, or (None, None)."""
    record = TrainedModel.objects.filter(kind=kind, is_active=True).order_by("-version").first()
    if record is None:
        return None, None
    with _cache_lock:
        estimator = _cache.get(record.file_name)
        if estimator is None:
            path = settings.ML_MODELS_DIR / record.file_name
            if not path.exists():
                return None, None
            payload = json.loads(path.read_text())
            estimator = ALGORITHMS[payload["model"]["algorithm"]].from_dict(payload["model"])
            _cache[record.file_name] = estimator
    return record, estimator


# ----------------------------------------------------- demand forecasts ---

def _risk_for(predicted, available, expiring):
    """Grade by days of cover: how long the usable stock lasts at forecast demand.

    HIGH   - less than 7 days of cover (the forecast week cannot be covered)
    MEDIUM - less than 14 days of cover
    Units that expire inside the forecast week are not counted as cover.
    """
    usable = max(available - expiring, 0)
    if predicted <= 0:
        return RiskLevel.LOW
    days_of_cover = usable / (predicted / F.FORECAST_HORIZON_DAYS)
    if days_of_cover < F.FORECAST_HORIZON_DAYS:
        return RiskLevel.HIGH
    if days_of_cover < 2 * F.FORECAST_HORIZON_DAYS:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def refresh_demand_forecasts():
    """Forecast red-cell units requested per blood group over the next 7 days."""
    record, model = get_active_model(ModelKind.DEMAND)
    if model is None:
        return {}
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    start = yesterday - timedelta(days=F.DEMAND_HISTORY_DAYS - 1)
    recent = BloodRequest.objects.filter(request_date__date__gte=start - timedelta(days=1))
    series = F.daily_demand([serializers.blood_request_record(r) for r in recent], start, yesterday)

    red_cells = BloodUnit.objects.available().filter(component__in=F.RED_CELL_COMPONENTS)
    available = dict(red_cells.values_list("blood_group").annotate(n=Count("pk")))
    expiring = dict(
        red_cells.filter(expiry_date__lt=today + timedelta(days=F.FORECAST_HORIZON_DAYS))
        .values_list("blood_group").annotate(n=Count("pk"))
    )
    results = {}
    for group in F.BLOOD_GROUPS:
        x = F.demand_features(series[group], F.DEMAND_HISTORY_DAYS - 1, group, today)
        predicted = max(0.0, float(model.predict([x])[0]))
        have, soon = available.get(group, 0), expiring.get(group, 0)
        forecast, _ = DemandForecast.objects.update_or_create(
            blood_group=group,
            defaults={
                "horizon_days": F.FORECAST_HORIZON_DAYS,
                "predicted_units": round(predicted, 1),
                "available_units": have,
                "expiring_units": soon,
                "shortfall_units": round(max(0.0, predicted - have), 1),
                "risk_level": _risk_for(predicted, have, soon),
                "model": record,
            },
        )
        results[group] = forecast
    return results


def forecast_dict(forecast):
    daily = forecast.predicted_units / forecast.horizon_days if forecast.predicted_units else 0
    return {
        "days_of_cover": round(forecast.usable_units / daily, 1) if daily else None,
        "blood_group": forecast.blood_group,
        "predicted_units": forecast.predicted_units,
        "available_units": forecast.available_units,
        "expiring_units": forecast.expiring_units,
        "shortfall_units": forecast.shortfall_units,
        "risk_level": forecast.risk_level,
        "horizon_days": forecast.horizon_days,
        "updated_at": forecast.updated_at.isoformat(),
    }


# ------------------------------------------------- donor return model ---

def _donor_records_with_history(donors):
    donor_ids = [d.pk for d in donors]
    history = F.group_donations_by_donor(
        serializers.donation_record(d) for d in Donation.objects.filter(donor_id__in=donor_ids)
    )
    return [(d, serializers.donor_record(d), history.get(d.pk, [])) for d in donors]


def refresh_donor_prediction(donor):
    record, model = get_active_model(ModelKind.DONOR_RETURN)
    if model is None:
        return None
    (_, donor_rec, history), = _donor_records_with_history([donor])
    x = F.donor_return_features(donor_rec, history, timezone.localdate())
    if x is None:
        DonorPrediction.objects.filter(donor=donor).delete()
        return None
    prediction, _ = DonorPrediction.objects.update_or_create(
        donor=donor,
        defaults={"return_probability": round(float(model.predict_proba([x])[0]), 4), "model": record},
    )
    return prediction


def refresh_all_donor_predictions():
    record, model = get_active_model(ModelKind.DONOR_RETURN)
    if model is None:
        return 0
    today = timezone.localdate()
    rows, X = [], []
    for donor, donor_rec, history in _donor_records_with_history(list(Donor.objects.all())):
        x = F.donor_return_features(donor_rec, history, today)
        if x is not None:
            rows.append(donor)
            X.append(x)
    probabilities = model.predict_proba(X) if X else []
    DonorPrediction.objects.all().delete()
    DonorPrediction.objects.bulk_create(
        DonorPrediction(donor=d, return_probability=round(float(p), 4), model=record)
        for d, p in zip(rows, probabilities)
    )
    return len(rows)


# --------------------------------------------------- deferral screening ---

def _deferral_level(probability):
    if probability >= 0.7:
        return RiskLevel.HIGH
    if probability >= 0.4:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def screen_donor(donor, systolic_bp, diastolic_bp, weight_kg=None, screening_date=None, source="api", user=None):
    """Score an incoming pre-donation screening and store it."""
    record, model = get_active_model(ModelKind.DEFERRAL)
    if model is None:
        raise ModelNotTrained("The deferral model has not been trained yet. Run: python manage.py train_models")
    visit_date = screening_date or timezone.localdate()
    weight = float(weight_kg) if weight_kg not in (None, "") else float(donor.weight_kg)
    previous = sorted(
        (serializers.donation_record(d) for d in donor.donations.filter(donation_date__lt=visit_date)),
        key=lambda r: (r["donation_date"], r["id"]),
    )
    x = F.deferral_features(serializers.donor_record(donor), visit_date, systolic_bp, diastolic_bp, weight, previous)
    probability = float(model.predict_proba([x])[0])
    contributions = model.contributions(x)
    factors = sorted(
        (
            {"feature": name, "label": _factor_label(name, value), "value": round(value, 2),
             "impact": round(float(c), 3)}
            for name, value, c in zip(F.DEFERRAL_FEATURES, x, contributions)
        ),
        key=lambda f: f["impact"], reverse=True,
    )
    return ScreeningCheck.objects.create(
        donor=donor, screening_date=visit_date, systolic_bp=systolic_bp, diastolic_bp=diastolic_bp,
        weight_kg=weight, deferral_risk=round(probability, 4), risk_level=_deferral_level(probability),
        top_factors=[f for f in factors if f["impact"] >= MIN_FACTOR_IMPACT][:3], source=source, model=record,
        created_by=user,
    )


def screening_dict(screening):
    donor = screening.donor
    next_date = donor.next_eligible_date
    return {
        "eligible_on_screening_date": donor.is_eligible_on(screening.screening_date),
        "next_eligible_date": next_date.isoformat() if next_date else None,
        "id": screening.pk,
        "donor_id": donor.pk,
        "donor_name": f"{donor.first_name} {donor.last_name}",
        "blood_group": donor.blood_group,
        "screening_date": screening.screening_date.isoformat(),
        "systolic_bp": screening.systolic_bp,
        "diastolic_bp": screening.diastolic_bp,
        "weight_kg": float(screening.weight_kg),
        "deferral_risk": screening.deferral_risk,
        "risk_level": screening.risk_level,
        "top_factors": screening.top_factors,
        "source": screening.source,
        "created_at": timezone.localtime(screening.created_at).isoformat(),
    }


# ------------------------------------------------------- dashboard data ---

def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February in a non-leap year
        return day.replace(year=day.year - years, day=28)


def _eligible_donor_filter(today):
    """Active donors aged 18-65 with no collected donation in the last 56 days."""
    oldest = _years_before(today, 66) + timedelta(days=1)
    youngest = _years_before(today, 18)
    return Q(
        donor__is_active=True,
        donor__weight_kg__gte=50,
        donor__date_of_birth__gte=oldest,
        donor__date_of_birth__lte=youngest,
    ) & (Q(last_collected__isnull=True) | Q(last_collected__lte=today - timedelta(days=F.DONATION_INTERVAL_DAYS)))


def _donor_row(prediction, priority=None):
    donor = prediction.donor
    return {
        "donor_id": donor.pk,
        "name": f"{donor.first_name} {donor.last_name}",
        "blood_group": donor.blood_group,
        "phone": donor.phone,
        "district": donor.district,
        "return_probability": prediction.return_probability,
        "last_donation": prediction.last_collected.isoformat() if prediction.last_collected else None,
        "donations": prediction.collected_count,
        "priority": round(priority, 3) if priority is not None else None,
    }


def donor_targets(forecasts):
    """Donors to invite now, boosted when their blood group is short."""
    today = timezone.localdate()
    boost = {RiskLevel.HIGH: 1.5, RiskLevel.MEDIUM: 1.25, RiskLevel.LOW: 1.0}
    group_boost = {g: boost[f["risk_level"]] for g, f in forecasts.items()}
    qs = DonorPrediction.objects.select_related("donor").annotate(
        last_collected=Max("donor__donations__donation_date", filter=Q(donor__donations__outcome=DonationOutcome.COLLECTED)),
        collected_count=Count("donor__donations", filter=Q(donor__donations__outcome=DonationOutcome.COLLECTED)),
    )
    candidates = list(qs.filter(_eligible_donor_filter(today)).order_by("-return_probability")[:300])
    ranked = sorted(
        candidates,
        key=lambda p: p.return_probability * group_boost.get(p.donor.blood_group, 1.0),
        reverse=True,
    )[:INVITE_LIMIT]
    invite = [_donor_row(p, p.return_probability * group_boost.get(p.donor.blood_group, 1.0)) for p in ranked]
    lapsing = [
        _donor_row(p)
        for p in qs.filter(donor__is_active=True, collected_count__gte=3, return_probability__lt=0.3)
        .order_by("return_probability")[:INVITE_LIMIT]
    ]
    return invite, lapsing


def trends(days=90):
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    collected = dict(
        Donation.objects.filter(outcome=DonationOutcome.COLLECTED, donation_date__gte=start)
        .values_list("donation_date").annotate(n=Count("pk"))
    )
    deferred = dict(
        Donation.objects.filter(outcome=DonationOutcome.DEFERRED, donation_date__gte=start)
        .values_list("donation_date").annotate(n=Count("pk"))
    )
    requested = dict(
        BloodRequest.objects.filter(request_date__date__gte=start)
        .annotate(day=TruncDate("request_date"))
        .values_list("day").annotate(n=Sum("quantity_units"))
    )
    labels, c, d, r = [], [], [], []
    for i in range(days):
        day = start + timedelta(days=i)
        labels.append(day.isoformat())
        c.append(collected.get(day, 0))
        d.append(deferred.get(day, 0))
        r.append(requested.get(day, 0) or 0)
    return {"labels": labels, "collected": c, "deferred": d, "requested_units": r}


def model_cards():
    cards = []
    for kind in ModelKind:
        record = TrainedModel.objects.filter(kind=kind, is_active=True).order_by("-version").first()
        if record is None:
            cards.append({"kind": kind.value, "name": kind.label, "trained": False})
            continue
        m = record.metrics
        if kind == ModelKind.DEMAND:
            headline = [
                ("MAE (units / week)", m.get("mae")),
                ("Naive baseline MAE", m.get("baseline_mae")),
                ("Better than baseline", f"{m.get('improvement_vs_baseline_pct', 0):.0f}%"),
            ]
        elif kind == ModelKind.DONOR_RETURN:
            headline = [
                ("ROC AUC", m.get("auc")),
                ("Baseline AUC (recency)", m.get("baseline_auc_recency_only")),
                ("Accuracy", m.get("accuracy")),
            ]
        else:
            headline = [("ROC AUC", m.get("auc")), ("Recall", m.get("recall")), ("Precision", m.get("precision"))]
        cards.append({
            "kind": kind.value, "name": kind.label, "trained": True, "version": record.version,
            "trained_at": timezone.localtime(record.trained_at).strftime("%Y-%m-%d %H:%M"),
            "data_source": record.data_source, "train_samples": record.train_samples,
            "test_samples": record.test_samples,
            "headline": [{"label": label, "value": value} for label, value in headline],
        })
    return cards


def dashboard_payload():
    today = timezone.localdate()
    forecasts = {f.blood_group: forecast_dict(f) for f in DemandForecast.objects.all()}
    if not forecasts:
        forecasts = {g: forecast_dict(f) for g, f in refresh_demand_forecasts().items()}
    invite, lapsing = donor_targets(forecasts)
    last30 = Donation.objects.filter(donation_date__gt=today - timedelta(days=30))
    visits = last30.count()
    deferred = last30.filter(outcome=DonationOutcome.DEFERRED).count()
    expected_returns = DonorPrediction.objects.aggregate(total=Sum("return_probability"))["total"] or 0
    screenings = ScreeningCheck.objects.select_related("donor")[:10]
    return {
        "generated_at": timezone.localtime().isoformat(),
        "kpis": {
            "active_donors": Donor.objects.filter(is_active=True).count(),
            "donations_this_month": Donation.objects.filter(
                donation_date__gte=today.replace(day=1), outcome=DonationOutcome.COLLECTED
            ).count(),
            "available_units": BloodUnit.objects.available().count(),
            "pending_requests": BloodRequest.objects.filter(
                status__in=[BloodRequestStatus.PENDING, BloodRequestStatus.PARTIAL]
            ).count(),
            "forecast_units_7d": round(sum(f["predicted_units"] for f in forecasts.values()), 1),
            "groups_at_risk": sum(1 for f in forecasts.values() if f["risk_level"] == RiskLevel.HIGH),
            "expected_returning_donors": round(expected_returns),
            "deferral_rate_30d": round(100 * deferred / visits, 1) if visits else 0.0,
        },
        "forecasts": [forecasts[g] for g in F.BLOOD_GROUPS if g in forecasts],
        "donors_to_invite": invite,
        "lapsing_donors": lapsing,
        "recent_screenings": [screening_dict(s) for s in screenings],
        "trends": trends(),
        "models": model_cards(),
    }
