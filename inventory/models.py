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
from donors.models import Donation



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


class BloodRequestStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PARTIAL = "PARTIAL", "Partially fulfilled"
    FULFILLED = "FULFILLED", "Fulfilled"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


class RequestUrgency(models.TextChoices):
    ROUTINE = "ROUTINE", "Routine"
    URGENT = "URGENT", "Urgent"
    EMERGENCY = "EMERGENCY", "Emergency"



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


class BloodRequest(models.Model):
    request_number = models.CharField(max_length=20, unique=True, editable=False)
    requestor = models.ForeignKey(
        Institution, on_delete=models.PROTECT, related_name="sent_blood_requests", db_index=True
    )
    receiver = models.ForeignKey(
        Institution, on_delete=models.PROTECT, related_name="received_blood_requests", db_index=True
    )
    requested_by = models.CharField(max_length=100)
    patient_reference = models.CharField(max_length=50)
    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices)
    component = models.CharField(
        max_length=6, choices=BloodComponent.choices, default=BloodComponent.WHOLE_BLOOD
    )
    quantity_units = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    urgency = models.CharField(
        max_length=10, choices=RequestUrgency.choices, default=RequestUrgency.ROUTINE
    )
    request_date = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    required_by = models.DateField()
    status = models.CharField(
        max_length=10, choices=BloodRequestStatus.choices, default=BloodRequestStatus.PENDING
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_blood_requests",
    )
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-request_date"]
        indexes = [
            models.Index(fields=["status", "urgency"], name="request_status_urg_idx"),
            models.Index(fields=["requestor", "request_date"], name="sent_request_hosp_date_idx"),
            models.Index(fields=["receiver", "request_date"], name="received_request_hosp_date_idx"),
            models.Index(fields=["required_by"], name="request_required_by_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(quantity_units__gte=1), name="request_qty_positive"),
        ]

    def _make_request_number(self):
        return f"RQ-{timezone.localdate():%y%m}-{uuid.uuid4().hex[:6].upper()}"

    @property
    def issued_count(self):
        return self.issued_units.count()

    @property
    def remaining_quantity(self):
        return max(0, self.quantity_units - self.issued_count)

    def clean(self):
        super().clean()
        errors = {}
        if self.required_by and self.request_date and self.required_by < timezone.localtime(self.request_date).date():
            errors["required_by"] = "Required-by date cannot be earlier than the request date."
        if self.quantity_units is not None and self.quantity_units < 1:
            errors["quantity_units"] = "Quantity must be at least one unit."
        if self.requestor_id and not self.requestor.is_active:
            errors["institution"] = "An inactive institution cannot submit a new request."
        if self.status in (BloodRequestStatus.REJECTED, BloodRequestStatus.CANCELLED) and self.issued_units.exists():
            errors["status"] = "A request with issued units cannot be rejected or cancelled."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.request_number:
            self.request_number = self._make_request_number()
        self.full_clean()
        return super().save(*args, **kwargs)

    def recalculate_status(self):
        issued = self.issued_count
        if issued == 0:
            new_status = BloodRequestStatus.PENDING
        elif issued < self.quantity_units:
            new_status = BloodRequestStatus.PARTIAL
        else:
            new_status = BloodRequestStatus.FULFILLED
        if self.status not in (BloodRequestStatus.REJECTED, BloodRequestStatus.CANCELLED):
            if self.status != new_status:
                self.status = new_status
                self.save(update_fields=["status"])

    def __str__(self):
        return f"{self.request_number} — {self.requestor.name}"


class IssuedUnit(models.Model):
    request = models.ForeignKey(
        BloodRequest, on_delete=models.CASCADE, related_name="issued_units", db_index=True
    )
    blood_unit = models.OneToOneField(
        BloodUnit, on_delete=models.PROTECT, related_name="issue"
    )
    issued_date = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_blood_units",
    )
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-issued_date"]
        indexes = [models.Index(fields=["issued_date"], name="issued_date_idx")]

    def clean(self):
        super().clean()
        errors = {}
        if self.request_id and self.blood_unit_id:
            request = self.request
            unit = self.blood_unit
            if request.status in (BloodRequestStatus.REJECTED, BloodRequestStatus.CANCELLED):
                errors["request"] = "Cannot issue blood against a rejected or cancelled request."
            if unit.blood_group != request.blood_group or unit.component != request.component:
                errors["blood_unit"] = "Unit blood group and component must exactly match the request."
            if not unit.is_issuable and not (self.pk and unit.status == BloodUnitStatus.ISSUED):
                errors["blood_unit"] = "Only available, cleared, unexpired units can be issued."
            existing = IssuedUnit.objects.filter(request=request).exclude(pk=self.pk)
            if existing.count() >= request.quantity_units:
                errors["request"] = "Cannot issue more units than the request quantity."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            is_new = self.pk is None
            result = super().save(*args, **kwargs)
            self.blood_unit.status = BloodUnitStatus.ISSUED
            self.blood_unit.save(update_fields=["status"])
            self.request.recalculate_status()
            return result

    def delete(self, *args, **kwargs):
        request = self.request
        unit = self.blood_unit
        with transaction.atomic():
            result = super().delete(*args, **kwargs)
            unit.status = BloodUnitStatus.AVAILABLE
            unit.save(update_fields=["status"])
            request.recalculate_status()
            return result

    def __str__(self):
        return f"{self.blood_unit.unit_number} issued to {self.request.request_number}"


class StockThreshold(models.Model):
    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices, unique=True)
    minimum_units = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["blood_group"]
        constraints = [
            models.CheckConstraint(condition=Q(minimum_units__gte=1), name="threshold_min_positive"),
        ]

    def clean(self):
        super().clean()
        if self.minimum_units is not None and self.minimum_units < 1:
            raise ValidationError({"minimum_units": "Minimum stock threshold must be positive."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def current_stock(self):
        return BloodUnit.objects.available().filter(blood_group=self.blood_group).count()

    @property
    def is_low_stock(self):
        return self.current_stock < self.minimum_units

    def __str__(self):
        return f"{self.blood_group}: minimum {self.minimum_units} units"
