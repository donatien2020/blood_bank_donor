"""Generate a realistic synthetic history for the blood bank.

    python manage.py generate_synthetic_data --donors 3000 --days 730 --clear

What gets simulated (so the models have real patterns to learn):
* Donors have a hidden "loyalty"; loyal donors come back sooner and more often.
  Older donors are a bit more loyal; a deferral makes people less likely to return.
* Each visit has haemoglobin and blood pressure drawn from donor-specific
  baselines. Low Hb (< 12.5 g/dL) or high blood pressure leads to deferral.
* Donation campaigns run in different districts and attract donors who are due.
* Hospital demand follows a weekly pattern (busy Mondays, quiet Sundays), a
  yearly season (rainy season and end-of-year peaks) and a slow upward trend.
* Units are tested, then issued first-expiry-first-out; unused units expire.

All rows obey the project's validation rules (age 18-65, weight >= 50 kg,
56 days between collections, campaign dates, shelf life per component).
"""

import heapq
import math
import random
from datetime import datetime, time, timedelta

import numpy as np
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from analytics.models import DemandForecast, DonorPrediction, ScreeningCheck
from analytics.signals import predictions_paused
from core.models import Institution
from donors.models import (
    BloodUnit, BloodUnitStatus, Donation, DonationCampaign, DonationOutcome, Donor, LabTestResult,
)
from inventory.models import BloodRequest, BloodRequestStatus, IssuedUnit, StockThreshold

BLOOD_GROUP_SHARE = {"O+": 0.45, "A+": 0.25, "B+": 0.18, "AB+": 0.04,
                     "O-": 0.03, "A-": 0.02, "B-": 0.02, "AB-": 0.01}
COMPONENT_SHARE = {"WB": 0.45, "RBC": 0.35, "PLASMA": 0.12, "PLT": 0.08}
SHELF_LIFE = {"WB": 35, "RBC": 42, "PLASMA": 365, "PLT": 5}
DOW_FACTOR = [1.2, 1.1, 1.05, 1.0, 1.0, 0.85, 0.8]  # Monday .. Sunday
DISTRICTS = ["Gasabo", "Kicukiro", "Nyarugenge", "Huye", "Musanze", "Rubavu", "Rwamagana",
             "Muhanga", "Nyagatare", "Rusizi", "Karongi", "Bugesera"]
DISTRICT_WEIGHTS = [16, 12, 12, 8, 8, 7, 6, 6, 6, 5, 5, 9]
FIRST_NAMES_M = ["Jean", "Eric", "Patrick", "Emmanuel", "Claude", "Olivier", "David", "Samuel",
                 "Innocent", "Fabrice", "Kevin", "Didier", "Thierry", "Yves", "Aimable", "Pacifique"]
FIRST_NAMES_F = ["Alice", "Grace", "Diane", "Josiane", "Aline", "Chantal", "Clarisse", "Esther",
                 "Divine", "Sandrine", "Ange", "Solange", "Vestine", "Liliane", "Annet", "Claudine"]
LAST_NAMES = ["Uwimana", "Mukamana", "Niyonzima", "Habimana", "Nshimiyimana", "Uwase", "Ishimwe",
              "Mugisha", "Hakizimana", "Ndayisaba", "Iradukunda", "Nsengiyumva", "Tuyishime",
              "Byiringiro", "Niyomugabo", "Mutoni", "Ingabire", "Uwamahoro", "Bizimana", "Kamanzi"]
BLOOD_BANKS = [("National Blood Bank Kigali", "Gasabo"), ("Regional Blood Bank Huye", "Huye"),
               ("Regional Blood Bank Musanze", "Musanze")]
HOSPITALS = [("Kigali Referral Hospital", "REFERRAL", "Nyarugenge"),
             ("Kicukiro District Hospital", "DISTRICT", "Kicukiro"),
             ("Gasabo District Hospital", "DISTRICT", "Gasabo"),
             ("Huye Referral Hospital", "REFERRAL", "Huye"),
             ("Musanze District Hospital", "DISTRICT", "Musanze"),
             ("Rubavu District Hospital", "DISTRICT", "Rubavu"),
             ("Nyagatare District Hospital", "DISTRICT", "Nyagatare"),
             ("Hope Private Clinic", "PRIVATE", "Kicukiro")]


def _weighted(rng, mapping):
    return rng.choices(list(mapping), weights=list(mapping.values()))[0]


def _phone(rng):
    return f"07{rng.choice('2389')}{rng.randint(0, 9_999_999):07d}"


def _age(dob, day):
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


class Command(BaseCommand):
    help = "Fill the database with a realistic synthetic blood bank history."

    def add_arguments(self, parser):
        parser.add_argument("--donors", type=int, default=3000)
        parser.add_argument("--days", type=int, default=730, help="Length of history in days.")
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--clear", action="store_true",
                            help="Delete existing donors, donations, units and requests first.")

    def handle(self, *args, **options):
        if options["days"] < 120:
            raise CommandError("Use at least 120 days so the models have enough history.")
        if Donor.objects.exists() and not options["clear"]:
            raise CommandError("The database already has donors. Re-run with --clear to replace them.")
        self.rng = random.Random(options["seed"])
        self.np_rng = np.random.default_rng(options["seed"])
        self.today = timezone.localdate()
        self.start = self.today - timedelta(days=options["days"])

        with predictions_paused(), transaction.atomic():
            if options["clear"]:
                self._clear()
            banks, hospitals = self._institutions()
            campaigns = self._campaigns(banks)
            donors = self._donors(options["donors"])
            donations, units = self._donations(donors, campaigns)
            requests, issued = self._requests_and_issuing(units, banks, hospitals)
            self._thresholds()

        self.stdout.write(self.style.SUCCESS(
            f"Created {len(donors)} donors, {len(donations)} donations, {len(units)} blood units, "
            f"{len(requests)} blood requests and {issued} issued units "
            f"({self.start} to {self.today})."
        ))

    # ------------------------------------------------------------ steps --

    def _clear(self):
        IssuedUnit.objects.all().delete()
        BloodRequest.objects.all().delete()
        ScreeningCheck.objects.all().delete()
        DonorPrediction.objects.all().delete()
        DemandForecast.objects.all().delete()
        BloodUnit.objects.all().delete()
        Donation.objects.all().delete()
        DonationCampaign.objects.all().delete()
        Donor.objects.all().delete()

    def _institutions(self):
        banks, hospitals = [], []
        for name, district in BLOOD_BANKS:
            obj, _ = Institution.objects.get_or_create(name=name, defaults=dict(
                institution_type="BLOOD_BANK", institution_category="BLOOD_BANK", district=district,
                contact_person="Head of Collections", phone=_phone(self.rng)))
            banks.append(obj)
        for name, kind, district in HOSPITALS:
            obj, _ = Institution.objects.get_or_create(name=name, defaults=dict(
                institution_type=kind, institution_category="HOSPITAL", district=district,
                contact_person="Lab Manager", phone=_phone(self.rng)))
            hospitals.append(obj)
        return banks, hospitals

    def _campaigns(self, banks):
        rng, campaigns = self.rng, []
        day = self.start + timedelta(days=rng.randint(5, 20))
        end_limit = self.today + timedelta(days=45)  # also plan a few upcoming ones
        while day <= end_limit:
            length = rng.randint(2, 4)
            district = rng.choices(DISTRICTS, weights=DISTRICT_WEIGHTS)[0]
            end = day + timedelta(days=length - 1)
            status = ("COMPLETED" if end < self.today else
                      "ONGOING" if day <= self.today else "PLANNED")
            campaigns.append(DonationCampaign(
                institution=rng.choice(banks), name=f"{district} Blood Drive {day:%b %Y}",
                location=f"{district} sector office", district=district, start_date=day, end_date=end,
                target_units=rng.randint(60, 150), organizer="Community Outreach Team", status=status))
            day += timedelta(days=rng.randint(28, 50))
        DonationCampaign.objects.bulk_create(campaigns)
        return campaigns

    def _donors(self, count):
        rng, donors = self.rng, []
        span = (self.today - self.start).days
        for i in range(count):
            male = rng.random() < 0.62
            age_at_start = int(min(max(rng.gauss(30, 9), 18), 58))
            first_visit = self.start + timedelta(days=int(span * rng.random() ** 1.6))
            dob = first_visit - timedelta(days=int(age_at_start * 365.25) + rng.randint(0, 300))
            if _age(dob, first_visit) < 18:
                dob -= timedelta(days=366)
            weight = max(50.5, rng.gauss(68 if male else 60, 8))
            donor = Donor(
                national_id=f"1{dob.year}{8 if male else 7}{i:07d}{rng.randint(0, 999):03d}",
                first_name=rng.choice(FIRST_NAMES_M if male else FIRST_NAMES_F),
                last_name=rng.choice(LAST_NAMES), gender="M" if male else "F", date_of_birth=dob,
                blood_group=_weighted(rng, BLOOD_GROUP_SHARE), phone=_phone(rng),
                district=rng.choices(DISTRICTS, weights=DISTRICT_WEIGHTS)[0],
                weight_kg=round(weight, 1), is_active=True,
            )
            # Hidden traits used only by the simulation:
            loyalty = rng.betavariate(2, 2.5) + 0.12 * (age_at_start - 30) / 30
            donor._sim = {
                "first_visit": first_visit,
                "loyalty": min(max(loyalty, 0.02), 0.98),
                "hb_offset": rng.gauss(0, 0.6),
                "sys_base": rng.gauss(118 + 0.45 * (age_at_start - 30) + (4 if male else 0), 10),
            }
            donors.append(donor)
        Donor.objects.bulk_create(donors, batch_size=1000)
        for donor in donors:
            donor.registered_at = timezone.make_aware(datetime.combine(donor._sim["first_visit"], time(9)))
        Donor.objects.bulk_update(donors, ["registered_at"], batch_size=1000)
        return donors

    def _visit_date(self, donor, due, campaigns_by_district):
        """Due donors sometimes wait for a campaign in their district."""
        if self.rng.random() < 0.5:
            for campaign in campaigns_by_district.get(donor.district, []):
                if due <= campaign.start_date <= due + timedelta(days=21):
                    day = campaign.start_date + timedelta(
                        days=self.rng.randint(0, (campaign.end_date - campaign.start_date).days))
                    return day, campaign
        return due, None

    def _donations(self, donors, campaigns):
        rng = self.rng
        by_district = {}
        for campaign in sorted(campaigns, key=lambda c: c.start_date):
            by_district.setdefault(campaign.district, []).append(campaign)

        donations = []
        for donor in donors:
            sim = donor._sim
            loyalty, male = sim["loyalty"], donor.gender == "M"
            due, last_collected, deferrals = sim["first_visit"], None, 0
            while True:
                day, campaign = self._visit_date(donor, due, by_district)
                if day > self.today or _age(donor.date_of_birth, day) > 65:
                    break
                recent = last_collected is not None and (day - last_collected).days < 120
                hb = round(rng.gauss(14.6 if male else 13.1, 0.9) + sim["hb_offset"] - (0.4 if recent else 0), 1)
                systolic = int(round(sim["sys_base"] + rng.gauss(0, 7)))
                diastolic = int(round(0.6 * systolic + rng.gauss(6, 5)))
                if hb < 12.5:
                    outcome, reason = DonationOutcome.DEFERRED, f"Low haemoglobin ({hb} g/dL)"
                elif systolic > 160 or diastolic > 100:
                    outcome, reason = DonationOutcome.DEFERRED, f"High blood pressure ({systolic}/{diastolic})"
                elif rng.random() < 0.02:
                    outcome, reason = DonationOutcome.DEFERRED, "Recent illness or medication"
                else:
                    outcome, reason = DonationOutcome.COLLECTED, ""
                donation = Donation(
                    donor=donor, campaign=campaign, donation_date=day, outcome=outcome,
                    volume_ml=350 if float(donor.weight_kg) < 55 else rng.choice([450, 450, 470]),
                    hemoglobin_g_dl=hb, systolic_bp=systolic, diastolic_bp=diastolic,
                    deferral_reason=reason,
                )
                donations.append(donation)

                if outcome == DonationOutcome.COLLECTED:
                    last_collected = day
                    gap = 56 + rng.expovariate(1 / (25 + 520 * (1 - loyalty) ** 2))
                else:
                    deferrals += 1
                    gap = rng.randint(30, 120)
                quit_chance = 0.06 + 0.45 * (1 - loyalty) ** 2 + 0.12 * deferrals
                if rng.random() < quit_chance:
                    break
                due = day + timedelta(days=int(gap))

        donations.sort(key=lambda d: (d.donation_date, d.donor.pk))
        Donation.objects.bulk_create(donations, batch_size=1000)

        units = []
        for i, donation in enumerate(d for d in donations if d.outcome == DonationOutcome.COLLECTED):
            component = _weighted(rng, COMPONENT_SHARE)
            age_days = (self.today - donation.donation_date).days
            if age_days < 2:
                result = LabTestResult.PENDING
            else:
                result = LabTestResult.REACTIVE if rng.random() < 0.025 else LabTestResult.CLEARED
            units.append(BloodUnit(
                unit_number=f"BU-{donation.donation_date:%y%m}-{i:06X}", donation=donation,
                blood_group=donation.donor.blood_group, component=component, volume_ml=donation.volume_ml,
                collection_date=donation.donation_date,
                expiry_date=donation.donation_date + timedelta(days=SHELF_LIFE[component]),
                status=BloodUnitStatus.DISCARDED if result == LabTestResult.REACTIVE else BloodUnitStatus.AVAILABLE,
                test_result=result, storage_location=f"Fridge {rng.randint(1, 6)}",
            ))
        return donations, units

    def _requests_and_issuing(self, units, banks, hospitals):
        rng, np_rng = self.rng, self.np_rng
        days = (self.today - self.start).days + 1
        cleared = [u for u in units if u.test_result == LabTestResult.CLEARED]
        supply_per_day = len(cleared) / days
        base = 1.05 * supply_per_day  # demand runs slightly ahead of supply, as in practice
        users = list(get_user_model().objects.filter(is_staff=True)[:1])
        handler = users[0] if users else None

        # Units become issuable 2 days after collection (testing time).
        by_ready_day = {}
        for index, unit in enumerate(units):
            if unit.test_result == LabTestResult.CLEARED:
                ready = unit.collection_date + timedelta(days=2)
                by_ready_day.setdefault(ready, []).append(index)
        pools = {}  # (group, component) -> heap of (expiry, index)

        requests, allocations, seq = [], [], 0
        mean_dow = sum(DOW_FACTOR) / 7
        for offset in range(days):
            day = self.start + timedelta(days=offset)
            for index in by_ready_day.get(day, []):
                unit = units[index]
                heapq.heappush(pools.setdefault((unit.blood_group, unit.component), []), (unit.expiry_date, index))

            season = 1 + 0.15 * math.sin(2 * math.pi * (day.timetuple().tm_yday - 60) / 365.25)
            if (day.month == 12 and day.day >= 15) or (day.month == 1 and day.day <= 10):
                season *= 1.2
            trend = 0.9 + 0.2 * offset / max(days - 1, 1)
            units_today = base * DOW_FACTOR[day.weekday()] / mean_dow * season * trend

            todays = []
            for group, share in BLOOD_GROUP_SHARE.items():
                for _ in range(np_rng.poisson(units_today * share / 2.0)):
                    quantity = rng.choices([1, 2, 3, 4], weights=[35, 35, 20, 10])[0]
                    urgency = rng.choices(["ROUTINE", "URGENT", "EMERGENCY"], weights=[70, 22, 8])[0]
                    seq += 1
                    moment = timezone.make_aware(datetime.combine(day, time(rng.randint(7, 20), rng.randint(0, 59))))
                    todays.append(BloodRequest(
                        request_number=f"RQ-{day:%y%m}-{seq:06X}", requestor=rng.choice(hospitals),
                        receiver=banks[0] if rng.random() < 0.7 else rng.choice(banks),
                        requested_by=f"Dr. {rng.choice(LAST_NAMES)}", patient_reference=f"PT-{rng.randint(10000, 99999)}",
                        blood_group=group, component=_weighted(rng, COMPONENT_SHARE), quantity_units=quantity,
                        urgency=urgency, request_date=moment,
                        required_by=day + timedelta(days={"EMERGENCY": 0, "URGENT": 1}.get(urgency, rng.randint(2, 5))),
                        status=BloodRequestStatus.PENDING, handled_by=handler,
                    ))
            todays.sort(key=lambda r: ({"EMERGENCY": 0, "URGENT": 1, "ROUTINE": 2}[r.urgency], r.request_date))

            for blood_request in todays:
                requests.append(blood_request)
                recent = (self.today - day).days <= 1
                if recent and rng.random() < 0.5:
                    continue  # not processed yet
                heap = pools.get((blood_request.blood_group, blood_request.component), [])
                issued = 0
                while heap and issued < blood_request.quantity_units:
                    expiry, index = heapq.heappop(heap)
                    if expiry < day:
                        continue  # expired on the shelf
                    units[index].status = BloodUnitStatus.ISSUED
                    allocations.append((blood_request, units[index]))
                    issued += 1
                if issued == blood_request.quantity_units:
                    blood_request.status = BloodRequestStatus.FULFILLED
                elif issued:
                    blood_request.status = BloodRequestStatus.PARTIAL
                elif (self.today - day).days > 3:
                    blood_request.status = BloodRequestStatus.REJECTED
                    blood_request.remarks = "Not enough compatible stock"

        for unit in units:
            if unit.status == BloodUnitStatus.AVAILABLE and unit.expiry_date < self.today:
                unit.status = BloodUnitStatus.EXPIRED

        BloodUnit.objects.bulk_create(units, batch_size=1000)
        BloodRequest.objects.bulk_create(requests, batch_size=1000)
        IssuedUnit.objects.bulk_create(
            [IssuedUnit(request=r, blood_unit=u, issued_by=handler,
                        issued_date=r.request_date + timedelta(hours=rng.randint(1, 4)))
             for r, u in allocations],
            batch_size=1000,
        )
        return requests, len(allocations)

    def _thresholds(self):
        for group, share in BLOOD_GROUP_SHARE.items():
            StockThreshold.objects.update_or_create(
                blood_group=group, defaults={"minimum_units": max(2, round(share * 40))})
