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
        if self.date_of_birth:
            today = timezone.localdate()
            age = today.year - self.date_of_birth.year - (
                    (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
            if not 18 <= age <= 65:
                errors["date_of_birth"] = "Donor must be between 18 and 65 years old."
        if self.weight_kg is not None and self.weight_kg < 50:
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

    def is_eligible_on(self, on_date=None):
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
        last = self.donations.filter(outcome=DonationOutcome.COLLECTED).order_by(
            "-donation_date", "-pk"
        ).first()
        return last is None or on_date >= last.donation_date + timedelta(days=56)

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
            if not donor.is_eligible_on(self.donation_date):
                errors["donor"] = "Donor is not eligible on this date (age, weight, status, or 56-day interval)."
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


