import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from analytics import services
from analytics.client import LocalApiClient
from analytics.ml.algorithms import LogisticRegression, RidgeRegression, roc_auc
from analytics.ml.training import train_all
from analytics.models import DemandForecast, DonorPrediction, ModelKind, ScreeningCheck, TrainedModel
from core.models import Institution
from donors.models import BloodUnit, Donation, Donor

API_KEY = "test-key"


class AlgorithmTests(TestCase):
    def test_logistic_regression_learns_and_round_trips(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(2000, 3))
        y = (rng.random(2000) < 1 / (1 + np.exp(-(2 * X[:, 0] - X[:, 1])))).astype(int)
        model = LogisticRegression().fit(X, y)
        self.assertGreater(roc_auc(y, model.predict_proba(X)), 0.8)
        self.assertGreater(model.coef[0], 1.5)
        restored = LogisticRegression.from_dict(json.loads(json.dumps(model.to_dict())))
        np.testing.assert_allclose(restored.predict_proba(X), model.predict_proba(X))

    def test_ridge_regression_recovers_coefficients(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(1000, 2))
        y = 3 * X[:, 0] - 2 * X[:, 1] + 5
        model = RidgeRegression(alpha=0.01).fit(X, y)
        np.testing.assert_allclose(model.predict(X), y, atol=0.05)

    def test_auc_handles_ties(self):
        self.assertEqual(roc_auc([0, 1], [0.5, 0.5]), 0.5)
        self.assertEqual(roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)


class PipelineTestCase(TestCase):
    """Generates a small synthetic history and trains the models once."""

    @classmethod
    def setUpClass(cls):
        cls.models_dir = Path(tempfile.mkdtemp())
        cls.settings_override = override_settings(ML_MODELS_DIR=cls.models_dir, ANALYTICS_API_KEY=API_KEY)
        cls.settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.settings_override.disable()
        shutil.rmtree(cls.models_dir, ignore_errors=True)
        services._cache.clear()

    @classmethod
    def setUpTestData(cls):
        call_command("generate_synthetic_data", donors=500, days=450, seed=7, clear=True, stdout=open("/dev/null", "w"))
        cls.results = train_all(LocalApiClient(API_KEY), log=lambda *a: None)
        services.refresh_demand_forecasts()
        services.refresh_all_donor_predictions()


class TrainingTests(PipelineTestCase):
    def test_all_three_models_trained_and_saved(self):
        for kind in ModelKind:
            record = self.results[kind]
            self.assertIsInstance(record, TrainedModel, f"{kind} failed: {record}")
            self.assertTrue((self.models_dir / record.file_name).exists())
            self.assertEqual(record.data_source, "in-process /api/v1")

    def test_models_beat_their_baselines(self):
        demand = self.results[ModelKind.DEMAND].metrics
        self.assertLess(demand["mae"], demand["baseline_mae"])
        self.assertGreater(self.results[ModelKind.DONOR_RETURN].metrics["auc"], 0.6)
        self.assertGreater(self.results[ModelKind.DEFERRAL].metrics["auc"], 0.6)

    def test_predictions_are_stored(self):
        self.assertEqual(DemandForecast.objects.count(), 8)
        self.assertGreater(DonorPrediction.objects.count(), 100)
        p = DonorPrediction.objects.first().return_probability
        self.assertTrue(0 <= p <= 1)

    def test_retraining_creates_new_active_version(self):
        train_all(LocalApiClient(API_KEY), kinds=[ModelKind.DEMAND], log=lambda *a: None)
        versions = TrainedModel.objects.filter(kind=ModelKind.DEMAND)
        self.assertEqual(versions.count(), 2)
        self.assertEqual(versions.filter(is_active=True).get().version, 2)


class ApiTests(PipelineTestCase):
    def get(self, url, key=API_KEY):
        return self.client.get(url, HTTP_X_API_KEY=key) if key else self.client.get(url)

    def post(self, url, payload, key=API_KEY):
        return self.client.post(url, json.dumps(payload), content_type="application/json", HTTP_X_API_KEY=key)

    def test_read_api_requires_authentication(self):
        self.assertEqual(self.get(reverse("api_donors"), key=None).status_code, 401)
        self.assertEqual(self.get(reverse("api_donors"), key="wrong").status_code, 401)

    def test_pagination(self):
        page = self.get(reverse("api_donors") + "?page_size=50").json()
        self.assertEqual(page["count"], Donor.objects.count())
        self.assertEqual(len(page["results"]), 50)
        self.assertIn("page=2", page["next"])

    def test_ingest_requires_key(self):
        self.assertEqual(self.post(reverse("api_ingest_screening"), {}, key="wrong").status_code, 401)

    def test_ingest_screening_returns_deferral_risk(self):
        donor = Donor.objects.first()
        response = self.post(reverse("api_ingest_screening"),
                             {"national_id": donor.national_id, "systolic_bp": 172, "diastolic_bp": 106})
        self.assertEqual(response.status_code, 201)
        screening = response.json()["screening"]
        self.assertEqual(screening["risk_level"], "HIGH")
        self.assertIn("Blood pressure above donation limit", [f["label"] for f in screening["top_factors"]])
        self.assertEqual(ScreeningCheck.objects.count(), 1)

    def test_ingest_donation_returns_prediction_and_creates_unit(self):
        donor = next(d for d in Donor.objects.filter(is_active=True) if d.is_eligible_on())
        response = self.post(reverse("api_ingest_donation"), {
            "donor_id": donor.pk, "outcome": "COLLECTED", "hemoglobin_g_dl": 14.3,
            "systolic_bp": 120, "diastolic_bp": 80,
        })
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertIsNotNone(body["blood_unit"])
        self.assertTrue(0 <= body["predictions"]["donor_return_probability"] <= 1)

    def test_ingest_donation_validation_errors_are_400(self):
        donor = Donor.objects.first()
        response = self.post(reverse("api_ingest_donation"), {
            "donor_id": donor.pk, "outcome": "COLLECTED", "hemoglobin_g_dl": 10.1,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("errors", response.json())

    def test_ingest_blood_request_updates_forecast(self):
        hospital = Institution.objects.filter(institution_category="HOSPITAL").first()
        bank = Institution.objects.filter(institution_category="BLOOD_BANK").first()
        response = self.post(reverse("api_ingest_blood_request"), {
            "requestor_id": hospital.pk, "receiver_id": bank.pk, "patient_reference": "PT-9", "blood_group": "A+", "component": "RBC",
            "quantity_units": 2, "required_by": timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 201, response.content)
        forecast = response.json()["predictions"]["demand_forecast"]
        self.assertEqual(forecast["blood_group"], "A+")

    def test_new_data_from_web_forms_refreshes_predictions(self):
        """Signals: a donation saved anywhere updates that donor's prediction."""
        donor = next(d for d in Donor.objects.filter(is_active=True, prediction__isnull=False) if d.is_eligible_on())
        before = donor.prediction.updated_at
        with self.captureOnCommitCallbacks(execute=True):
            Donation.objects.create(donor=donor, outcome="COLLECTED", volume_ml=450, hemoglobin_g_dl="14.0")
        donor.prediction.refresh_from_db()
        self.assertGreater(donor.prediction.updated_at, before)


class DashboardTests(PipelineTestCase):
    def test_dashboard_and_screening_pages(self):
        user = get_user_model().objects.create_user("staff", password="pass-1234-x")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Donors to invite now")
        payload = response.context["payload"]
        self.assertEqual(len(payload["forecasts"]), 8)
        self.assertTrue(all(card["trained"] for card in payload["models"]))

        api = self.client.get(reverse("api_dashboard"))  # session login works for the API too
        self.assertEqual(api.status_code, 200)

        donor = Donor.objects.first()
        response = self.client.post(reverse("screening"),
                                    {"national_id": donor.national_id, "systolic_bp": 120, "diastolic_bp": 80})
        self.assertContains(response, "deferral risk")

    def test_simulator_runs_against_api(self):
        call_command("simulate_incoming", events=12, seed=3, stdout=open("/dev/null", "w"))
        self.assertGreater(ScreeningCheck.objects.count() + Donation.objects.filter(
            donation_date=timezone.localdate()).count(), 0)
