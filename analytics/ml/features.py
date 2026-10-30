"""Feature engineering for the three models.

Every function works on the plain dict records produced by
analytics.serializers (the same shape the API returns), so the exact same
code builds features for training and for live predictions.
"""

import math
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

# Donor must wait this many days between collected donations.
DONATION_INTERVAL_DAYS = 56
# The donor-return model predicts a new donation within this window.
RETURN_WINDOW_DAYS = 180
# The demand model forecasts total units requested over this many days.
FORECAST_HORIZON_DAYS = 7
# Days of history the demand model needs before it can make a forecast.
DEMAND_HISTORY_DAYS = 28
# The demand forecast covers red-cell products. Plasma (kept frozen for a year)
# and platelets cannot replace red cells, so mixing them into one stock
# number would hide real shortages.
RED_CELL_COMPONENTS = ("WB", "RBC")


def to_date(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def age_on(date_of_birth, on_date):
    dob = to_date(date_of_birth)
    return on_date.year - dob.year - ((on_date.month, on_date.day) < (dob.month, dob.day))


# ------------------------------------------------------- demand forecast ---

DEMAND_FEATURES = [
    "units_last_7d",
    "units_per_week_last_14d",
    "units_per_week_last_28d",
    "week_over_week_change",
    "month_sin",
    "month_cos",
] + [f"group_{g}" for g in BLOOD_GROUPS]


def daily_demand(request_records, start, end):
    """Red-cell units requested per blood group per day (start..end inclusive).

    Returns {blood_group: numpy array}, where index 0 is `start`.
    """
    n_days = (end - start).days + 1
    series = {g: np.zeros(n_days) for g in BLOOD_GROUPS}
    for record in request_records:
        if record.get("status") == "CANCELLED" or record.get("component") not in RED_CELL_COMPONENTS:
            continue
        day = to_date(record["request_day"])
        if start <= day <= end and record["blood_group"] in series:
            series[record["blood_group"]][(day - start).days] += record["quantity_units"]
    return series


def demand_features(series, last_index, blood_group, forecast_start):
    """Features for forecasting the 7 days after series[last_index].

    series must hold at least DEMAND_HISTORY_DAYS days up to last_index.
    forecast_start is the calendar date of the first forecast day.
    """
    t = last_index
    last7 = series[t - 6 : t + 1].sum()
    prev7 = series[t - 13 : t - 6].sum()
    last14 = series[t - 13 : t + 1].sum() / 2
    last28 = series[t - 27 : t + 1].sum() / 4
    middle = forecast_start + timedelta(days=FORECAST_HORIZON_DAYS // 2)
    angle = 2 * math.pi * (middle.timetuple().tm_yday / 365.25)
    one_hot = [1.0 if g == blood_group else 0.0 for g in BLOOD_GROUPS]
    return [last7, last14, last28, last7 - prev7, math.sin(angle), math.cos(angle)] + one_hot


def demand_training_set(request_records, start, end):
    """One sample per blood group per day that has 28 days before and 7 after."""
    series = daily_demand(request_records, start, end)
    X, y, days = [], [], []
    n_days = (end - start).days + 1
    for t in range(DEMAND_HISTORY_DAYS - 1, n_days - FORECAST_HORIZON_DAYS):
        forecast_start = start + timedelta(days=t + 1)
        for group in BLOOD_GROUPS:
            s = series[group]
            X.append(demand_features(s, t, group, forecast_start))
            y.append(s[t + 1 : t + 1 + FORECAST_HORIZON_DAYS].sum())
            days.append(t)
    return np.array(X), np.array(y), np.array(days)


# --------------------------------------------------------- donor return ---

DONOR_RETURN_FEATURES = [
    "log_days_since_last_donation",
    "collected_donations",
    "total_volume_litres",
    "log_days_since_first_visit",
    "deferrals",
    "donations_per_year",
    "age_years",
    "is_male",
    "campaign_share",
    "eligible_now",
]


def group_donations_by_donor(donation_records):
    by_donor = defaultdict(list)
    for record in donation_records:
        by_donor[record["donor_id"]].append(record)
    for records in by_donor.values():
        records.sort(key=lambda r: (r["donation_date"], r["id"]))
    return by_donor


def donor_return_features(donor, donations, cutoff):
    """Features describing a donor's history up to `cutoff` (inclusive).

    Returns None when the donor has no collected donation before cutoff:
    the model only scores people who have already donated.
    """
    history = [d for d in donations if to_date(d["donation_date"]) <= cutoff]
    collected = [d for d in history if d["outcome"] == "COLLECTED"]
    if not collected:
        return None
    last = to_date(collected[-1]["donation_date"])
    first = to_date(history[0]["donation_date"])
    days_since_last = (cutoff - last).days
    tenure_days = max((cutoff - first).days, 1)
    deferrals = len(history) - len(collected)
    from_campaigns = sum(1 for d in collected if d.get("campaign_id"))
    return [
        math.log1p(days_since_last),
        len(collected),
        sum(d["volume_ml"] for d in collected) / 1000,
        math.log1p(tenure_days),
        deferrals,
        len(collected) / max(tenure_days / 365.25, 0.25),
        age_on(donor["date_of_birth"], cutoff),
        1.0 if donor["gender"] == "M" else 0.0,
        from_campaigns / len(collected),
        1.0 if days_since_last >= DONATION_INTERVAL_DAYS else 0.0,
    ]


def donor_return_training_set(donor_records, donation_records, start, end, step_days=30):
    """Snapshots every `step_days`: features at a cutoff date, and a label
    saying whether the donor gave blood again in the next 180 days."""
    donors = {d["id"]: d for d in donor_records}
    by_donor = group_donations_by_donor(donation_records)
    X, y, cutoffs = [], [], []
    cutoff = start + timedelta(days=RETURN_WINDOW_DAYS)
    while cutoff + timedelta(days=RETURN_WINDOW_DAYS) <= end:
        window_end = cutoff + timedelta(days=RETURN_WINDOW_DAYS)
        for donor_id, donations in by_donor.items():
            donor = donors.get(donor_id)
            if donor is None:
                continue
            features = donor_return_features(donor, donations, cutoff)
            if features is None:
                continue
            returned = any(
                d["outcome"] == "COLLECTED" and cutoff < to_date(d["donation_date"]) <= window_end
                for d in donations
            )
            X.append(features)
            y.append(1 if returned else 0)
            cutoffs.append(cutoff)
        cutoff += timedelta(days=step_days)
    return np.array(X), np.array(y), np.array(cutoffs)


# --------------------------------------------------------- deferral risk ---

DEFERRAL_FEATURES = [
    "age_years",
    "is_male",
    "weight_kg",
    "systolic_bp",
    "diastolic_bp",
    "bp_over_limit",
    "first_time_donor",
    "days_since_last_collected",
    "previous_deferrals",
    "previous_collected",
    "last_hemoglobin",
]

# Common donation limits: blood pressure above these usually means deferral.
SYSTOLIC_LIMIT = 160
DIASTOLIC_LIMIT = 100
DEFAULT_SYSTOLIC = 120
DEFAULT_DIASTOLIC = 78
DEFAULT_HEMOGLOBIN = 13.8  # used when a donor has no earlier result


def deferral_features(donor, visit_date, systolic_bp, diastolic_bp, weight_kg, previous_donations):
    """Features known *before* the haemoglobin test of a new visit.

    Haemoglobin of the current visit is deliberately left out: it decides
    deferral by rule, so the model would learn nothing useful from it.
    previous_donations must only contain visits before visit_date.
    """
    collected = [d for d in previous_donations if d["outcome"] == "COLLECTED"]
    deferrals = len(previous_donations) - len(collected)
    if collected:
        days_since = min((visit_date - to_date(collected[-1]["donation_date"])).days, 730)
    else:
        days_since = 730
    last_hb = (
        float(previous_donations[-1]["hemoglobin_g_dl"]) if previous_donations else DEFAULT_HEMOGLOBIN
    )
    systolic = float(systolic_bp if systolic_bp is not None else DEFAULT_SYSTOLIC)
    diastolic = float(diastolic_bp if diastolic_bp is not None else DEFAULT_DIASTOLIC)
    return [
        age_on(donor["date_of_birth"], visit_date),
        1.0 if donor["gender"] == "M" else 0.0,
        float(weight_kg),
        systolic,
        diastolic,
        # A linear model cannot learn a sharp cut-off from the raw numbers,
        # so the clinical limit is given to it as its own feature.
        1.0 if systolic > SYSTOLIC_LIMIT or diastolic > DIASTOLIC_LIMIT else 0.0,
        0.0 if collected else 1.0,
        days_since,
        deferrals,
        len(collected),
        last_hb,
    ]


def deferral_training_set(donor_records, donation_records):
    """One sample per recorded visit; label 1 when the visit was deferred."""
    donors = {d["id"]: d for d in donor_records}
    X, y, dates = [], [], []
    for donor_id, donations in group_donations_by_donor(donation_records).items():
        donor = donors.get(donor_id)
        if donor is None:
            continue
        for i, visit in enumerate(donations):
            visit_date = to_date(visit["donation_date"])
            X.append(
                deferral_features(
                    donor, visit_date, visit["systolic_bp"], visit["diastolic_bp"],
                    donor["weight_kg"], donations[:i],
                )
            )
            y.append(1 if visit["outcome"] == "DEFERRED" else 0)
            dates.append(visit_date)
    return np.array(X), np.array(y), np.array(dates)
