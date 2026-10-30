# Blood Bank Donor Management System

A Django system for managing blood donors, donations, campaigns, blood stock and
hospital requests, extended with **machine-learning predictions**:

| Model | Question it answers | Algorithm |
|---|---|---|
| Blood demand forecast | How many red-cell units will hospitals request per blood group in the next 7 days, and will stock cover it? | Ridge regression |
| Donor return likelihood | How likely is each donor to give blood again within 180 days? Who should we invite now? | Logistic regression |
| Donation deferral risk | Before the haemoglobin test, how likely is this visit to end in a deferral? | Logistic regression (class-balanced) |

## How the project meets the assignment

1. **Read data from an API.** The project has its own JSON API (`analytics/api.py`).
   Training reads all data through it (`/api/v1/donors/`, `/api/v1/donations/`,
   `/api/v1/blood-requests/`), either over HTTP from a running server or in-process.
2. **Train the model on synthetic data.** `generate_synthetic_data` builds a realistic
   two-year history; `train_models` trains the three models with a time-based
   train/test split and compares each against a simple baseline.
3. **Deploy the model in Django.** Trained models are saved as versioned JSON files
   (`ml_models/`) and registered in the `TrainedModel` table. The `analytics` app loads
   the active versions and serves predictions.
4. **Predict on new incoming data and show it on the dashboard.** New donations, requests
   and screenings arriving through the ingest API or the web forms refresh predictions
   automatically (Django signals). The dashboard shows forecasts, shortage risk, donor
   targeting lists, live screening results, trends and model quality, and refreshes
   itself every 30 seconds.

## Getting started

Requirements: Python 3.12+ (required by Django 6.1) and pip.

```bash
git clone https://github.com/donatien2020/blood_bank_donor.git
cd blood_bank_donor
python3 -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
python3 -m pip install -r requirements.txt
python3 manage.py migrate
python3 manage.py createsuperuser
python3 manage.py setup_demo         # synthetic data + training + sample incoming events
python3 manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in.

`setup_demo` replaces existing donor data. To run the steps one at a time:

```bash
python3 manage.py generate_synthetic_data --clear      # --donors 3000 --days 730 --seed 42
python3 manage.py train_models                          # reads the API in-process
python3 manage.py simulate_incoming --events 20         # new data -> predictions
python3 manage.py refresh_predictions                   # e.g. nightly
```

### Live demo with real HTTP

Run the server in one terminal and these in another:

```bash
python3 manage.py train_models --api-url http://127.0.0.1:8000
python3 manage.py simulate_incoming --events 100 --interval 5 --api-url http://127.0.0.1:8000
```

Keep the dashboard open: incoming screenings, forecasts and donor lists update every 30 seconds.

## API

Authenticate with a logged-in session or the header `X-API-Key: <ANALYTICS_API_KEY>`
(default for local development: `dev-ingest-key`). POST endpoints always need the key.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/donors/` | Donors (paged: `?page=`, `?page_size=` up to 2000) |
| GET | `/api/v1/donations/` | Donations (`?since=YYYY-MM-DD&until=`) |
| GET | `/api/v1/blood-requests/` | Hospital requests (`?since=&until=`) |
| GET | `/api/v1/blood-units/` | Blood units (`?status=AVAILABLE`) |
| POST | `/api/v1/ingest/donors/` | Register a donor |
| POST | `/api/v1/ingest/donations/` | Record a donation; returns the donor's updated return likelihood |
| POST | `/api/v1/ingest/blood-requests/` | Record a request; returns the updated 7-day forecast for that group |
| POST | `/api/v1/ingest/screenings/` | Pre-donation check; returns deferral risk and main factors |
| GET | `/api/v1/predictions/dashboard/` | Everything the dashboard shows |
| GET | `/api/v1/predictions/donors/<id>/` | One donor's prediction |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/screenings/ \
  -H "X-API-Key: dev-ingest-key" -H "Content-Type: application/json" \
  -d '{"national_id": "1199080000000001", "systolic_bp": 150, "diastolic_bp": 95}'
```

## Models in more detail

**Synthetic data** (`analytics/management/commands/generate_synthetic_data.py`) follows all
project rules (age 18-65, weight >= 50 kg, 56 days between donations, shelf life per
component) and contains patterns to learn: donor loyalty, haemoglobin and blood-pressure
driven deferrals, campaigns, weekly and seasonal demand with a slow upward trend, and
first-expiry-first-out issuing.

**Demand forecast.** One sample per blood group per day. Features: units requested in
the last 7, 14 and 28 days, week-over-week change, season, blood group. Target: red-cell
units (whole blood + red cells) requested in the next 7 days. Plasma and platelets are
excluded because they cannot replace red cells. Risk level uses *days of cover*:
stock that won't expire this week divided by forecast daily demand (HIGH < 7 days,
MEDIUM < 14 days). Baseline: "next week = last week".

**Donor return.** Monthly snapshots: a donor's history up to a cutoff date (recency,
number of donations, volume, time since first visit, deferrals, donation rate, age, sex,
campaign share) and whether they donated again in the following 180 days.
Baseline: ranking donors by recency alone.

**Deferral risk.** One sample per visit, using only what is known before the
haemoglobin test (age, sex, weight, blood pressure, BP above the 160/100 limit,
time since last donation, earlier deferrals, last Hb result). The current Hb is
excluded on purpose: it decides deferral by rule. Each prediction lists the features
that raised the risk most.

The algorithms are written with numpy in `analytics/ml/algorithms.py` (no scikit-learn
needed) and models are stored as JSON, not pickle, so loading a model file cannot run code.

## Running the tests

```bash
python3 manage.py test
```

## Production settings

Set these environment variables instead of using the development defaults:
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`, `ANALYTICS_API_KEY`.
