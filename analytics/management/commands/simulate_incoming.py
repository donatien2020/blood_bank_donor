"""Send new incoming data to the ingest API and print the predictions it returns.

    python manage.py simulate_incoming --events 20
    python manage.py simulate_incoming --events 100 --interval 5 --api-url http://127.0.0.1:8000

Use it during a demo: keep the dashboard open in a browser while this runs
against the server, and watch new screenings, forecasts and donor lists
update (the dashboard refreshes itself every 30 seconds).
"""

import random
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Q
from django.utils import timezone

from analytics.client import ApiError, get_client
from analytics.management.commands.generate_synthetic_data import (
    BLOOD_GROUP_SHARE, COMPONENT_SHARE, DISTRICTS, FIRST_NAMES_F, FIRST_NAMES_M, LAST_NAMES, _phone, _weighted,
)
from core.models import Institution
from donors.models import DonationOutcome, Donor

EVENT_WEIGHTS = {"screening": 40, "donation": 25, "blood_request": 25, "new_donor": 10}


class Command(BaseCommand):
    help = "Simulate new incoming data arriving through the ingest API."

    def add_arguments(self, parser):
        parser.add_argument("--events", type=int, default=20)
        parser.add_argument("--interval", type=float, default=0, help="Seconds to wait between events.")
        parser.add_argument("--api-url", help="Base URL of a running server. Default: call the API in-process.")
        parser.add_argument("--api-key")
        parser.add_argument("--seed", type=int)

    def handle(self, *args, **options):
        self.rng = random.Random(options["seed"])
        self.client = get_client(options["api_url"], options["api_key"])
        self.banks = list(Institution.objects.filter(institution_category="BLOOD_BANK", is_active=True))
        self.hospitals = list(Institution.objects.filter(institution_category="HOSPITAL", is_active=True))
        if not Donor.objects.exists() or not self.banks or not self.hospitals:
            raise CommandError("No data yet. Run: python manage.py generate_synthetic_data")

        for i in range(options["events"]):
            event = _weighted(self.rng, EVENT_WEIGHTS)
            try:
                line = getattr(self, f"_{event}")()
            except ApiError as error:
                raise CommandError(str(error))
            self.stdout.write(f"[{i + 1:>3}] {line}")
            if options["interval"] and i < options["events"] - 1:
                time.sleep(options["interval"])

    # ------------------------------------------------------------ events --

    def _eligible_donor(self):
        today = timezone.localdate()
        donors = (
            Donor.objects.filter(is_active=True)
            .annotate(last=Max("donations__donation_date", filter=Q(donations__outcome=DonationOutcome.COLLECTED)))
            .filter(Q(last__isnull=True) | Q(last__lte=today - timedelta(days=56)))
            .order_by("?")[:20]
        )
        for donor in donors:
            if donor.is_eligible_on(today):
                return donor
        return None

    def _vitals(self, donor):
        systolic = int(self.rng.gauss(122 if donor.gender == "M" else 117, 14))
        diastolic = int(0.6 * systolic + self.rng.gauss(6, 6))
        return systolic, diastolic

    def _screening(self):
        donor = Donor.objects.filter(is_active=True).order_by("?").first()
        systolic, diastolic = self._vitals(donor)
        status, body = self.client.post("/api/v1/ingest/screenings/", {
            "national_id": donor.national_id, "systolic_bp": systolic, "diastolic_bp": diastolic,
            "source": "simulator",
        })
        if status != 201:
            return f"screening   -> HTTP {status}: {body}"
        s = body["screening"]
        return (f"screening   {s['donor_name']} BP {systolic}/{diastolic} -> deferral risk "
                f"{s['deferral_risk']:.0%} ({s['risk_level']})")

    def _donation(self):
        donor = self._eligible_donor()
        if donor is None:
            return "donation    -> no eligible donor found"
        systolic, diastolic = self._vitals(donor)
        hb = round(self.rng.gauss(14.6 if donor.gender == "M" else 13.1, 1.0), 1)
        if hb < 12.5 or systolic > 160 or diastolic > 100:
            outcome = DonationOutcome.DEFERRED
            reason = "Low haemoglobin" if hb < 12.5 else "High blood pressure"
        else:
            outcome, reason = DonationOutcome.COLLECTED, ""
        status, body = self.client.post("/api/v1/ingest/donations/", {
            "national_id": donor.national_id, "outcome": outcome, "hemoglobin_g_dl": hb,
            "systolic_bp": systolic, "diastolic_bp": diastolic, "deferral_reason": reason,
            "volume_ml": 450,
        })
        if status != 201:
            return f"donation    -> HTTP {status}: {body}"
        p = body["predictions"]["donor_return_probability"]
        unit = body["blood_unit"]["unit_number"] if body["blood_unit"] else "no unit"
        return (f"donation    {donor.first_name} {donor.last_name} ({donor.blood_group}) {outcome.lower()}, "
                f"{unit} -> return likelihood {p:.0%}" if p is not None else f"donation    {outcome.lower()}, {unit}")

    def _blood_request(self):
        group = _weighted(self.rng, BLOOD_GROUP_SHARE)
        urgency = self.rng.choices(["ROUTINE", "URGENT", "EMERGENCY"], weights=[70, 22, 8])[0]
        status, body = self.client.post("/api/v1/ingest/blood-requests/", {
            "requestor_id": self.rng.choice(self.hospitals).pk, "receiver_id": self.banks[0].pk,
            "requested_by": f"Dr. {self.rng.choice(LAST_NAMES)}",
            "patient_reference": f"PT-{self.rng.randint(10000, 99999)}", "blood_group": group,
            "component": _weighted(self.rng, COMPONENT_SHARE), "urgency": urgency,
            "quantity_units": self.rng.choices([1, 2, 3, 4], weights=[35, 35, 20, 10])[0],
            "required_by": (timezone.localdate() + timedelta(days=1)).isoformat(),
        })
        if status != 201:
            return f"request     -> HTTP {status}: {body}"
        r, f = body["blood_request"], body["predictions"]["demand_forecast"]
        tail = (f" -> {group} 7-day forecast {f['predicted_units']} units vs {f['available_units']} "
                f"in stock ({f['risk_level']} risk)") if f else ""
        return f"request     {r['request_number']} {r['quantity_units']}x {group} {urgency.lower()}{tail}"

    def _new_donor(self):
        male = self.rng.random() < 0.6
        age = self.rng.randint(18, 50)
        dob = timezone.localdate() - timedelta(days=int(age * 365.25) + self.rng.randint(1, 300))
        while True:
            national_id = f"1{dob.year}{8 if male else 7}{self.rng.randint(0, 10**10 - 1):010d}"
            if not Donor.objects.filter(national_id=national_id).exists():
                break
        status, body = self.client.post("/api/v1/ingest/donors/", {
            "national_id": national_id, "gender": "M" if male else "F", "date_of_birth": dob.isoformat(),
            "first_name": self.rng.choice(FIRST_NAMES_M if male else FIRST_NAMES_F),
            "last_name": self.rng.choice(LAST_NAMES), "blood_group": _weighted(self.rng, BLOOD_GROUP_SHARE),
            "phone": _phone(self.rng), "district": self.rng.choice(DISTRICTS),
            "weight_kg": round(max(50.5, self.rng.gauss(66 if male else 59, 8)), 1),
        })
        if status != 201:
            return f"new donor   -> HTTP {status}: {body}"
        d = body["donor"]
        return f"new donor   {d['first_name']} {d['last_name']} ({d['blood_group']}, {d['district']}) registered"
