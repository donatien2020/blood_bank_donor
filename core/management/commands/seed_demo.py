"""
Seed the database with realistic demo data so the dashboard, lists and
reports have meaningful content to show in screenshots / demonstrations.

Usage:
    python manage.py seed_demo            # add demo data (skips if already seeded)
    python manage.py seed_demo --reset    # wipe app data first, then reseed
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Institution, InstitutionMembership, InstitutionType, InstitutionCategory
from donors.models import (
    Donor, DonationCampaign, Donation, BloodUnit,
    BloodGroup, BloodComponent, DonationOutcome, CampaignStatus, LabTestResult,
)
from inventory.models import (
    BloodRequest, IssuedUnit, StockThreshold,
    BloodRequestStatus, RequestUrgency,
)

FIRST_NAMES = [
    "Jean", "Marie", "Eric", "Alice", "Patrick", "Claudine", "Emmanuel", "Divine",
    "Olivier", "Grace", "Innocent", "Josiane", "Samuel", "Pacifique", "Yvonne",
    "Vincent", "Chantal", "Theogene", "Solange", "Bosco",
]
LAST_NAMES = [
    "Uwimana", "Mugisha", "Niyonsaba", "Habimana", "Ingabire", "Nkurunziza",
    "Mukamana", "Nshimiyimana", "Uwase", "Byiringiro", "Nizeyimana", "Umutoni",
    "Twagirayezu", "Iradukunda", "Hakizimana",
]
DISTRICTS = ["Gasabo", "Kicukiro", "Nyarugenge", "Huye", "Musanze", "Rubavu", "Nyagatare", "Muhanga"]


class Command(BaseCommand):
    help = "Populate the database with demo institutions, donors, donations, stock and requests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Delete existing app data from the three apps before reseeding.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write("Wiping existing demo-able data...")
            IssuedUnit.objects.all().delete()
            BloodRequest.objects.all().delete()
            BloodUnit.objects.all().delete()
            Donation.objects.all().delete()
            DonationCampaign.objects.all().delete()
            Donor.objects.all().delete()
            StockThreshold.objects.all().delete()
            InstitutionMembership.objects.all().delete()
            Institution.objects.all().delete()

        if Donor.objects.exists():
            self.stdout.write(self.style.WARNING(
                "Donors already exist — skipping seed. Use --reset to reseed from scratch."
            ))
            return

        with transaction.atomic():
            user = self._get_staff_user()
            institutions = self._create_institutions()
            self._create_membership(user, institutions)
            donors = self._create_donors()
            campaigns = self._create_campaigns(institutions)
            self._create_donations(donors, campaigns, user)
            self._tune_blood_units()
            self._create_stock_thresholds()
            self._create_requests_and_issuances(institutions, user)

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully."))

    # ------------------------------------------------------------------
    def _get_staff_user(self):
        User = get_user_model()
        user = User.objects.filter(is_superuser=True).first()
        if not user:
            user = User.objects.create_superuser("admin", "admin@example.com", "ChangeMe123!")
        return user

    def _create_institutions(self):
        data = [
            ("Kigali Central Blood Bank", InstitutionType.BLOOD_BANK, InstitutionCategory.BLOOD_BANK, "Gasabo", "Jean de Dieu Habimana", "0788100001"),
            ("King Faisal Hospital", InstitutionType.REFERRAL, InstitutionCategory.HOSPITAL, "Kicukiro", "Alice Mukandayisenga", "0788100002"),
            ("CHUK Teaching Hospital", InstitutionType.REFERRAL, InstitutionCategory.HOSPITAL, "Nyarugenge", "Eric Ndayishimiye", "0788100003"),
            ("Kacyiru Health Center", InstitutionType.HEALTH_CENTER, InstitutionCategory.HOSPITAL, "Gasabo", "Claudine Uwimana", "0788100004"),
        ]
        institutions = []
        for name, itype, icat, district, contact, phone in data:
            inst, _ = Institution.objects.get_or_create(
                name=name,
                defaults=dict(
                    institution_type=itype, institution_category=icat, district=district,
                    address=f"{district} District, Kigali", contact_person=contact,
                    phone=phone, email=f"{name.split()[0].lower()}@example.rw", is_active=True,
                ),
            )
            institutions.append(inst)
        return institutions

    def _create_membership(self, user, institutions):
        InstitutionMembership.objects.get_or_create(user=user, defaults={"institution": institutions[0]})

    def _create_donors(self):
        donors = []
        groups = list(BloodGroup.values)
        today = timezone.localdate()
        for i in range(24):
            first = FIRST_NAMES[i % len(FIRST_NAMES)]
            last = LAST_NAMES[i % len(LAST_NAMES)]
            age = random.randint(19, 58)
            dob = today.replace(year=today.year - age) - timedelta(days=random.randint(0, 300))
            donor = Donor.objects.create(
                national_id=f"{1198000000000000 + i:016d}",
                first_name=first,
                last_name=last,
                gender="M" if i % 2 == 0 else "F",
                date_of_birth=dob,
                blood_group=groups[i % len(groups)],
                phone=f"078{2000000 + i:07d}",
                email=f"{first.lower()}.{last.lower()}{i}@example.rw",
                district=DISTRICTS[i % len(DISTRICTS)],
                address=f"Cell {i + 1}, {DISTRICTS[i % len(DISTRICTS)]}",
                weight_kg=random.choice([55, 60, 65, 70, 75, 80, 85]),
                is_active=True,
            )
            donors.append(donor)
        return donors

    def _create_campaigns(self, institutions):
        today = timezone.localdate()
        c1 = DonationCampaign.objects.create(
            institution=institutions[0],
            name="World Blood Donor Day Drive 2026",
            location="Kigali Convention Centre",
            district="Gasabo",
            start_date=today.replace(day=1),
            end_date=today.replace(day=1) + timedelta(days=24),
            target_units=100,
            organizer="Rwanda Red Cross Society",
            status=CampaignStatus.ONGOING,
            description="Monthly flagship donation drive held in partnership with the Rwanda Red Cross.",
        )
        c2_start = today.replace(day=1) - timedelta(days=60)
        c2 = DonationCampaign.objects.create(
            institution=institutions[1],
            name="King Faisal Staff Donation Day",
            location="King Faisal Hospital Grounds",
            district="Kicukiro",
            start_date=c2_start,
            end_date=c2_start + timedelta(days=9),
            target_units=80,
            organizer="King Faisal Hospital HR Department",
            status=CampaignStatus.COMPLETED,
            description="Internal staff-focused donation campaign.",
        )
        return [c1, c2]

    def _create_donations(self, donors, campaigns, user):
        today = timezone.localdate()
        c1, c2 = campaigns
        for i, donor in enumerate(donors):
            # Spread donations across: current campaign, past campaign, and no campaign.
            bucket = i % 3
            if bucket == 0:
                campaign = c1
                # Stay inside the campaign: it ends on the 25th, but today may be later in the month.
                last_day = min(today, c1.end_date)
                donation_date = c1.start_date + timedelta(days=random.randint(0, (last_day - c1.start_date).days))
            elif bucket == 1:
                campaign = c2
                donation_date = c2.start_date + timedelta(days=random.randint(0, 8))
            else:
                campaign = None
                donation_date = today - timedelta(days=random.randint(1, 85))

            deferred = i % 9 == 0  # roughly 1 in 9 donors deferred
            if deferred:
                hemoglobin = Decimal(str(round(random.uniform(10.5, 12.4), 1)))
                outcome = DonationOutcome.DEFERRED
                deferral_reason = "Hemoglobin below the minimum safe donation threshold."
            else:
                hemoglobin = Decimal(str(round(random.uniform(12.6, 16.5), 1)))
                outcome = DonationOutcome.COLLECTED
                deferral_reason = ""

            Donation.objects.create(
                donor=donor,
                campaign=campaign,
                donation_date=donation_date,
                outcome=outcome,
                volume_ml=random.choice([400, 420, 450, 450, 450, 470]),
                hemoglobin_g_dl=hemoglobin,
                systolic_bp=random.randint(110, 135),
                diastolic_bp=random.randint(70, 88),
                deferral_reason=deferral_reason,
                recorded_by=user,
                notes="",
            )

    def _tune_blood_units(self):
        """Give the auto-created blood units varied lab/status data for a realistic dashboard."""
        today = timezone.localdate()
        units = list(BloodUnit.objects.all().order_by("pk"))
        for idx, unit in enumerate(units):
            slot = idx % 10
            if slot in (0, 1, 2, 3, 4, 5):
                # Cleared and available.
                unit.test_result = LabTestResult.CLEARED
                if slot in (4, 5):
                    # A couple expiring soon (still valid, but within the next week).
                    unit.expiry_date = today + timedelta(days=random.choice([2, 4, 6]))
                unit.save()
            elif slot in (6,):
                # Lab rejected.
                unit.status = "DISCARDED"
                unit.test_result = LabTestResult.REACTIVE
                unit.save()
            elif slot in (7,):
                # Already expired stock, still on record.
                unit.test_result = LabTestResult.CLEARED
                unit.expiry_date = today - timedelta(days=random.choice([2, 5, 10]))
                unit.status = "EXPIRED"
                unit.save()
            # Remaining slots (8, 9) stay PENDING lab test — realistic backlog.

    def _create_stock_thresholds(self):
        from django.db.models import Count
        available_counts = dict(
            BloodUnit.objects.available().values_list("blood_group").annotate(c=Count("pk"))
        )
        for group in BloodGroup.values:
            current = available_counts.get(group, 0)
            # Set the bar just above current stock for ~half the groups so the
            # low-stock alert panel has something real to show.
            minimum = max(current + 2, 4) if group in ("AB-", "O-", "B-") else 3
            StockThreshold.objects.create(blood_group=group, minimum_units=minimum)

    def _create_requests_and_issuances(self, institutions, user):
        blood_bank, king_faisal, chuk, kacyiru = institutions
        today = timezone.localtime()

        requests_data = [
            (king_faisal, blood_bank, "A+", "WB", 3, RequestUrgency.URGENT, 2),
            (chuk, blood_bank, "O+", "RBC", 2, RequestUrgency.EMERGENCY, 1),
            (kacyiru, blood_bank, "B+", "WB", 4, RequestUrgency.ROUTINE, 5),
            (king_faisal, blood_bank, "O-", "WB", 2, RequestUrgency.EMERGENCY, 1),
            (chuk, blood_bank, "AB+", "PLASMA", 1, RequestUrgency.ROUTINE, 6),
            (kacyiru, blood_bank, "A-", "WB", 2, RequestUrgency.URGENT, 3),
            (king_faisal, blood_bank, "B-", "RBC", 3, RequestUrgency.ROUTINE, 7),
            (chuk, blood_bank, "O+", "WB", 5, RequestUrgency.URGENT, 2),
        ]

        created_requests = []
        for i, (requestor, receiver, group, component, qty, urgency, due_in) in enumerate(requests_data):
            req = BloodRequest.objects.create(
                requestor=requestor,
                receiver=receiver,
                requested_by=f"Dr. {LAST_NAMES[i % len(LAST_NAMES)]}",
                patient_reference=f"PT-{2026}-{100 + i}",
                blood_group=group,
                component=component,
                quantity_units=qty,
                urgency=urgency,
                request_date=today - timedelta(days=random.randint(0, 10)),
                required_by=timezone.localdate() + timedelta(days=due_in),
                handled_by=user,
                remarks="",
            )
            created_requests.append(req)

        # Reject/cancel a couple for status variety (no issued units attached yet).
        created_requests[-1].status = BloodRequestStatus.CANCELLED
        created_requests[-1].save(update_fields=["status"])

        # Issue cleared, available units against matching requests where possible.
        for req in created_requests[:-1]:
            candidates = BloodUnit.objects.available().filter(
                blood_group=req.blood_group, component=req.component
            )[: req.quantity_units]
            for unit in candidates:
                IssuedUnit.objects.create(
                    request=req, blood_unit=unit, issued_by=user,
                    issued_date=timezone.now(), remarks="",
                )
