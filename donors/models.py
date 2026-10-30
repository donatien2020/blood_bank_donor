from django.db import models

# Create your models here.


from datetime import date, timedelta
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from core.models import Institution
from core.models import RWANDA_PHONE_VALIDATOR



NATIONAL_ID_VALIDATOR = RegexValidator(
    regex=r"^\d{16}$",
    message="National ID must contain exactly 16 digits.",
)


class BloodGroup(models.TextChoices):
    A_POS = "A+", "A+"
    A_NEG = "A-", "A-"
    B_POS = "B+", "B+"
    B_NEG = "B-", "B-"
    AB_POS = "AB+", "AB+"
    AB_NEG = "AB-", "AB-"
    O_POS = "O+", "O+"
    O_NEG = "O-", "O-"


class BloodComponent(models.TextChoices):
    WHOLE_BLOOD = "WB", "Whole blood"
    RED_CELLS = "RBC", "Red blood cells"
    PLASMA = "PLASMA", "Plasma"
    PLATELETS = "PLT", "Platelets"


class DonationOutcome(models.TextChoices):
    COLLECTED = "COLLECTED", "Collected"
    DEFERRED = "DEFERRED", "Deferred"


class CampaignStatus(models.TextChoices):
    PLANNED = "PLANNED", "Planned"
    ONGOING = "ONGOING", "Ongoing"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"


class BloodUnitStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE", "Available"
    RESERVED = "RESERVED", "Reserved"
    ISSUED = "ISSUED", "Issued"
    EXPIRED = "EXPIRED", "Expired"
    DISCARDED = "DISCARDED", "Discarded"


class LabTestResult(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CLEARED = "CLEARED", "Cleared"
    REACTIVE = "REACTIVE", "Reactive"



class Donor(models.Model):

    national_id = models.CharField(
        max_length=16, unique=True, validators=[NATIONAL_ID_VALIDATOR]
    )
    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60)
    gender = models.CharField(
        max_length=1,
        choices=(("M", "Male"), ("F", "Female")),
    )
    date_of_birth = models.DateField()
    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices)
    phone = models.CharField(max_length=15, validators=[RWANDA_PHONE_VALIDATOR])
    email = models.EmailField(max_length=254, blank=True)
    district = models.CharField(max_length=60)
    address = models.CharField(max_length=150, blank=True)
    weight_kg = models.DecimalField(
        max_digits=4, decimal_places=1, validators=[MinValueValidator(0)]
    )
    is_active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["blood_group"], name="donor_blood_group_idx"),
            models.Index(fields=["is_active"], name="donor_active_idx"),
            models.Index(fields=["district"], name="donor_district_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        # Registration rules apply when a donor is first registered. Existing
        # donors can always be edited (e.g. deactivated after turning 66);
        # whether they may donate is decided by is_eligible_on().
        is_new = self._state.adding
        if is_new and self.date_of_birth:
            today = timezone.localdate()
            age = today.year - self.date_of_birth.year - (
                    (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
            if not 18 <= age <= 65:
                errors["date_of_birth"] = "Donor must be between 18 and 65 years old."
        if is_new and self.weight_kg is not None and self.weight_kg < 50:
            errors["weight_kg"] = "Donor must weigh at least 50 kg."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def last_collected_donation(self):
        return self.donations.filter(outcome=DonationOutcome.COLLECTED).order_by(
            "-donation_date", "-pk"
        ).first()

    @property
    def next_eligible_date(self):
        last = self.last_collected_donation
        return last.donation_date + timedelta(days=56) if last else None

    def is_eligible_on(self, on_date=None, exclude_donation_pk=None):
        """Whether the donor may give blood on on_date.

        exclude_donation_pk lets a donation being edited ignore itself when
        checking the 56-day interval.
        """
        on_date = on_date or timezone.localdate()
        if not self.is_active:
            return False
        if not self.date_of_birth:
            return False
        age = on_date.year - self.date_of_birth.year - (
                (on_date.month, on_date.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )
        if not 18 <= age <= 65 or self.weight_kg < 50:
            return False
        others = self.donations.filter(outcome=DonationOutcome.COLLECTED)
        if exclude_donation_pk:
            others = others.exclude(pk=exclude_donation_pk)
        # Any other collected donation within 56 days either side blocks this date.
        window = timedelta(days=55)
        return not others.filter(
            donation_date__gte=on_date - window, donation_date__lte=on_date + window
        ).exists()

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.national_id})"


class DonationCampaign(models.Model):
    institution = models.ForeignKey(
        Institution, on_delete=models.PROTECT, related_name="donation_campaigns", db_index=True
    )
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=150)
    district = models.CharField(max_length=60)
    start_date = models.DateField()
    end_date = models.DateField()
    target_units = models.PositiveIntegerField(default=100, validators=[MinValueValidator(1)])
    organizer = models.CharField(max_length=120)
    status = models.CharField(
        max_length=10, choices=CampaignStatus.choices, default=CampaignStatus.PLANNED
    )
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "name"]
        indexes = [models.Index(fields=["start_date", "end_date"], name="campaign_dates_idx")]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Campaign end date cannot be before start date."})
        if self.target_units is not None and self.target_units < 1:
            raise ValidationError({"target_units": "Target units must be at least 1."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def collected_units(self):
        return BloodUnit.objects.filter(
            donation__campaign=self, donation__outcome=DonationOutcome.COLLECTED
        ).count()

    @property
    def progress_percent(self):
        return min(100, round(self.collected_units * 100 / self.target_units)) if self.target_units else 0

    def __str__(self):
        return self.name


class Donation(models.Model):
    donor = models.ForeignKey(
        Donor, on_delete=models.PROTECT, related_name="donations", db_index=True
    )
    campaign = models.ForeignKey(
        DonationCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="donations",
        db_index=True,
    )
    donation_date = models.DateField(default=timezone.localdate, db_index=True)
    outcome = models.CharField(
        max_length=10, choices=DonationOutcome.choices, default=DonationOutcome.COLLECTED
    )
    volume_ml = models.PositiveIntegerField(
        default=450, validators=[MinValueValidator(350), MaxValueValidator(500)]
    )
    hemoglobin_g_dl = models.DecimalField(max_digits=4, decimal_places=1)
    systolic_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    diastolic_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    deferral_reason = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_donations",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-donation_date", "-pk"]
        indexes = [
            models.Index(fields=["donor", "donation_date"], name="donor_donation_date_idx"),
            models.Index(fields=["outcome", "donation_date"], name="don_outcome_date_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.donation_date and self.donation_date > timezone.localdate():
            errors["donation_date"] = "Donation date cannot be in the future."
        if self.volume_ml is not None and not 350 <= self.volume_ml <= 500:
            errors["volume_ml"] = "Donation volume must be between 350 and 500 ml."
        if self.hemoglobin_g_dl is not None and self.hemoglobin_g_dl < 12.5:
            if self.outcome != DonationOutcome.DEFERRED:
                errors["outcome"] = "Hemoglobin below 12.5 g/dL requires a deferred outcome."
        if self.outcome == DonationOutcome.DEFERRED and not self.deferral_reason.strip():
            errors["deferral_reason"] = "A deferral reason is required for deferred donations."
        if self.outcome == DonationOutcome.COLLECTED and self.deferral_reason.strip():
            errors["deferral_reason"] = "Deferral reason must be empty for collected donations."
        if self.donor_id and self.donation_date and self.outcome == DonationOutcome.COLLECTED:
            donor = self.donor
            if not donor.is_active:
                errors["donor"] = "A suspended donor cannot donate."
            if not donor.is_eligible_on(self.donation_date, exclude_donation_pk=self.pk):
                errors["donor"] = "Donor is not eligible on this date (age, weight, status, or 56-day interval)."
        if self.pk and self.outcome == DonationOutcome.DEFERRED:
            unit = BloodUnit.objects.filter(donation_id=self.pk).first()
            if unit and unit.status == BloodUnitStatus.ISSUED:
                errors["outcome"] = "This donation's blood unit was already issued; it cannot be changed to deferred."
        if self.campaign_id and self.donation_date:
            campaign = self.campaign
            if not campaign.start_date <= self.donation_date <= campaign.end_date:
                errors["campaign"] = "Donation date must fall within the campaign dates."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            result = super().save(*args, **kwargs)
            if self.outcome == DonationOutcome.COLLECTED:
                unit, created = BloodUnit.objects.get_or_create(
                    donation=self,
                    defaults={
                        "blood_group": self.donor.blood_group,
                        "volume_ml": self.volume_ml,
                        "collection_date": self.donation_date,
                        "expiry_date": None,
                    },
                )
                if not created:
                    # Keep the denormalized values aligned if a donation is corrected.
                    unit.blood_group = self.donor.blood_group
                    unit.volume_ml = self.volume_ml
                    unit.collection_date = self.donation_date
                    unit.save()
            else:
                # A deferred donation must never have a blood unit.
                if hasattr(self, "blood_unit"):
                    self.blood_unit.delete()
            return result

    def delete(self, *args, **kwargs):
        # BloodUnit.PROTECT intentionally prevents deleting a donation with a unit.
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"Donation #{self.pk} — {self.donor}"

class BloodUnitQuerySet(models.QuerySet):
    def available(self):
        return self.filter(
            status=BloodUnitStatus.AVAILABLE,
            test_result=LabTestResult.CLEARED,
            expiry_date__gte=timezone.localdate(),
        )

    def expiring_soon(self, days=7):
        today = timezone.localdate()
        return self.filter(
            status=BloodUnitStatus.AVAILABLE,
            test_result=LabTestResult.CLEARED,
            expiry_date__gte=today,
            expiry_date__lte=today + timedelta(days=days),
        ).order_by("expiry_date")

    def stock_by_group(self):
        from django.db.models import Count
        return (
            self.available()
            .values("blood_group")
            .annotate(units=Count("pk"))
            .order_by("blood_group")
        )


class BloodUnit(models.Model):
    unit_number = models.CharField(max_length=20, unique=True, editable=False)
    donation = models.OneToOneField(
        Donation, on_delete=models.PROTECT, related_name="blood_unit"
    )
    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices, editable=False)
    component = models.CharField(
        max_length=6, choices=BloodComponent.choices, default=BloodComponent.WHOLE_BLOOD
    )
    volume_ml = models.PositiveIntegerField(default=450, validators=[MinValueValidator(1)])
    collection_date = models.DateField(editable=False)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=BloodUnitStatus.choices, default=BloodUnitStatus.AVAILABLE
    )
    test_result = models.CharField(
        max_length=10, choices=LabTestResult.choices, default=LabTestResult.PENDING
    )
    storage_location = models.CharField(max_length=60, blank=True)

    objects = BloodUnitQuerySet.as_manager()

    SHELF_LIFE_DAYS = {
        BloodComponent.WHOLE_BLOOD: 35,
        BloodComponent.RED_CELLS: 42,
        BloodComponent.PLASMA: 365,
        BloodComponent.PLATELETS: 5,
    }

    class Meta:
        ordering = ["expiry_date", "unit_number"]
        indexes = [
            models.Index(fields=["blood_group", "status"], name="unit_group_status_idx"),
            models.Index(fields=["expiry_date"], name="unit_expiry_idx"),
            models.Index(fields=["component", "test_result"], name="unit_comp_test_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(volume_ml__gt=0), name="unit_volume_positive"),
        ]

    def _make_unit_number(self):
        return f"BU-{timezone.localdate():%y%m}-{uuid.uuid4().hex[:6].upper()}"

    def clean(self):
        super().clean()
        if self.donation_id:
            if self.blood_group and self.blood_group != self.donation.donor.blood_group:
                raise ValidationError({"blood_group": "Blood group must match the donor."})
            if self.collection_date and self.collection_date != self.donation.donation_date:
                raise ValidationError({"collection_date": "Collection date must match the donation date."})
            if self.donation.outcome != DonationOutcome.COLLECTED:
                raise ValidationError({"donation": "A deferred donation cannot produce a blood unit."})
        if self.component and self.collection_date and not self.expiry_date:
            self.expiry_date = self.collection_date + timedelta(days=self.SHELF_LIFE_DAYS[self.component])
        if self.collection_date and self.expiry_date and self.expiry_date < self.collection_date:
            raise ValidationError({"expiry_date": "Expiry date cannot be before collection date."})
        if self.status == BloodUnitStatus.AVAILABLE and self.test_result == LabTestResult.REACTIVE:
            raise ValidationError({"status": "A reactive unit cannot be available."})

    def save(self, *args, **kwargs):
        if not self.unit_number:
            self.unit_number = self._make_unit_number()
        if self.donation_id:
            self.blood_group = self.donation.donor.blood_group
            self.collection_date = self.donation.donation_date
            if not self.volume_ml:
                self.volume_ml = self.donation.volume_ml
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_issuable(self):
        return (
                self.status == BloodUnitStatus.AVAILABLE
                and self.test_result == LabTestResult.CLEARED
                and self.expiry_date >= timezone.localdate()
        )

    def __str__(self):
        return f"{self.unit_number} — {self.blood_group} / {self.get_component_display()}"

