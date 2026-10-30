"""Train the three models on data read from the blood bank API.

Each model is evaluated on the most recent 20-25% of the data (a time-based
split, so the model is tested on a period it has not seen), compared against
a simple baseline, then refitted on all data and saved as a JSON file.
"""

import json
from datetime import timedelta

import numpy as np
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from analytics.models import ModelKind, TrainedModel

from . import features as F
from .algorithms import (
    LogisticRegression,
    RidgeRegression,
    classification_report,
    mean_absolute_error,
    root_mean_squared_error,
    roc_auc,
)

MIN_DEMAND_DAYS = 60
MIN_SAMPLES = 50


class TrainingError(Exception):
    pass


def load_training_data(client):
    """Read everything the models need from the API."""
    return {
        "donors": client.fetch_all("/api/v1/donors/"),
        "donations": client.fetch_all("/api/v1/donations/"),
        "blood_requests": client.fetch_all("/api/v1/blood-requests/"),
    }


def _round_metrics(metrics):
    out = {}
    for key, value in metrics.items():
        if isinstance(value, float):
            out[key] = round(value, 4)
        elif isinstance(value, dict):
            out[key] = _round_metrics(value)
        else:
            out[key] = value
    return out


# -------------------------------------------------------------- demand ---

def train_demand(data, today):
    requests = data["blood_requests"]
    if not requests:
        raise TrainingError("No blood requests returned by the API.")
    start = min(F.to_date(r["request_day"]) for r in requests)
    end = today - timedelta(days=1)  # only complete days
    if (end - start).days < MIN_DEMAND_DAYS:
        raise TrainingError(f"Demand model needs at least {MIN_DEMAND_DAYS} days of requests.")

    X, y, day_index = F.demand_training_set(requests, start, end)
    split_day = np.quantile(day_index, 0.8)
    train, test = day_index <= split_day, day_index > split_day
    if train.sum() < MIN_SAMPLES or test.sum() < 8:
        raise TrainingError("Not enough demand history to train and test.")

    model = RidgeRegression(alpha=1.0).fit(X[train], y[train])
    predicted = np.clip(model.predict(X[test]), 0, None)
    # Baseline: "next week will look like last week".
    baseline = X[test][:, F.DEMAND_FEATURES.index("units_last_7d")]
    metrics = {
        "mae": mean_absolute_error(y[test], predicted),
        "rmse": root_mean_squared_error(y[test], predicted),
        "baseline_mae": mean_absolute_error(y[test], baseline),
        "mean_weekly_units": float(y[test].mean()),
        "horizon_days": F.FORECAST_HORIZON_DAYS,
    }
    metrics["improvement_vs_baseline_pct"] = (
        100 * (metrics["baseline_mae"] - metrics["mae"]) / metrics["baseline_mae"]
        if metrics["baseline_mae"] else 0.0
    )
    final = RidgeRegression(alpha=1.0).fit(X, y)
    return final, F.DEMAND_FEATURES, metrics, int(train.sum()), int(test.sum())


# -------------------------------------------------------- donor return ---

def train_donor_return(data, today):
    donations = data["donations"]
    if not donations:
        raise TrainingError("No donations returned by the API.")
    start = min(F.to_date(d["donation_date"]) for d in donations)
    X, y, cutoffs = F.donor_return_training_set(data["donors"], donations, start, today)
    if len(y) < MIN_SAMPLES or len(set(y.tolist())) < 2:
        raise TrainingError(
            "Donor-return model needs at least a year of donation history with both returning and lapsed donors."
        )
    unique_cutoffs = sorted(set(cutoffs.tolist()))
    split = unique_cutoffs[int(len(unique_cutoffs) * 0.75)] if len(unique_cutoffs) > 1 else unique_cutoffs[0]
    train, test = cutoffs < split, cutoffs >= split
    if train.sum() < MIN_SAMPLES or len(set(y[train].tolist())) < 2:
        train = test = np.ones(len(y), dtype=bool)  # very small data: evaluate in-sample

    model = LogisticRegression(l2=1.0).fit(X[train], y[train])
    probabilities = model.predict_proba(X[test])
    metrics = classification_report(y[test], probabilities)
    # Baseline: rank donors by recency alone (recent donors return more).
    recency = X[test][:, F.DONOR_RETURN_FEATURES.index("log_days_since_last_donation")]
    metrics["baseline_auc_recency_only"] = roc_auc(y[test], -recency)
    metrics["window_days"] = F.RETURN_WINDOW_DAYS
    final = LogisticRegression(l2=1.0).fit(X, y)
    return final, F.DONOR_RETURN_FEATURES, metrics, int(train.sum()), int(test.sum())


# ------------------------------------------------------------ deferral ---

def train_deferral(data, today):
    X, y, dates = F.deferral_training_set(data["donors"], data["donations"])
    if len(y) < MIN_SAMPLES or len(set(y.tolist())) < 2:
        raise TrainingError("Deferral model needs visits with both collected and deferred outcomes.")
    order = np.argsort(dates)
    split_date = dates[order[int(len(order) * 0.8)]]
    train, test = dates < split_date, dates >= split_date
    if train.sum() < MIN_SAMPLES or len(set(y[train].tolist())) < 2 or len(set(y[test].tolist())) < 2:
        train = test = np.ones(len(y), dtype=bool)

    model = LogisticRegression(l2=1.0, balanced=True).fit(X[train], y[train])
    probabilities = model.predict_proba(X[test])
    metrics = classification_report(y[test], probabilities)
    final = LogisticRegression(l2=1.0, balanced=True).fit(X, y)
    return final, F.DEFERRAL_FEATURES, metrics, int(train.sum()), int(test.sum())


TRAINERS = {
    ModelKind.DEMAND: train_demand,
    ModelKind.DONOR_RETURN: train_donor_return,
    ModelKind.DEFERRAL: train_deferral,
}


# ------------------------------------------------------------- saving ---

def save_model(kind, model, feature_names, metrics, data_source, n_train, n_test):
    models_dir = settings.ML_MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)
    with transaction.atomic():
        last = TrainedModel.objects.select_for_update().filter(kind=kind).order_by("-version").first()
        version = (last.version + 1) if last else 1
        file_name = f"{kind.lower()}_v{version}.json"
        metrics = _round_metrics(metrics)
        payload = {
            "kind": kind,
            "version": version,
            "trained_at": timezone.now().isoformat(),
            "data_source": data_source,
            "feature_names": feature_names,
            "metrics": metrics,
            "model": model.to_dict(),
        }
        (models_dir / file_name).write_text(json.dumps(payload, indent=2))
        TrainedModel.objects.filter(kind=kind, is_active=True).update(is_active=False)
        return TrainedModel.objects.create(
            kind=kind, version=version, file_name=file_name, data_source=data_source,
            train_samples=n_train, test_samples=n_test, metrics=metrics, is_active=True,
        )


def train_all(client, kinds=None, log=print):
    """Read data through `client`, train the requested models, save them.

    Returns {kind: TrainedModel or error message}.
    """
    today = timezone.localdate()
    log(f"Reading training data from {client.source_name} ...")
    data = load_training_data(client)
    log(
        f"  {len(data['donors'])} donors, {len(data['donations'])} donations, "
        f"{len(data['blood_requests'])} blood requests"
    )
    results = {}
    for kind in kinds or list(TRAINERS):
        label = ModelKind(kind).label
        try:
            model, names, metrics, n_train, n_test = TRAINERS[kind](data, today)
        except TrainingError as error:
            log(f"  {label}: skipped - {error}")
            results[kind] = str(error)
            continue
        record = save_model(kind, model, names, metrics, client.source_name, n_train, n_test)
        log(f"  {label}: v{record.version} saved ({n_train} train / {n_test} test samples)")
        results[kind] = record
    return results
