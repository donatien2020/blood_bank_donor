from django.db import models

from datetime import date, timedelta
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

RWANDA_PHONE_VALIDATOR = RegexValidator(
    regex=r"^(?:\+250|250|0)?7[2389]\d{7}$",
    message="Enter a valid Rwandan mobile number.",
)

class InstitutionType(models.TextChoices):
    BLOOD_BANK = "BLOOD_BANK", "Blood Bank"
    REFERRAL = "REFERRAL", "Referral"
    DISTRICT = "DISTRICT", "District"
    HEALTH_CENTER = "HC", "Health center"
    PRIVATE = "PRIVATE", "Private clinic"

class InstitutionCategory(models.TextChoices):
    HOSPITAL = "HOSPITAL", "Hospital"
    BLOOD_BANK = "BLOOD_BANK", "Blood Bank"


class Institution(models.Model):
    name = models.CharField(max_length=150, unique=True)
    institution_type = models.CharField(max_length=15, choices=InstitutionType.choices)
    institution_category = models.CharField(max_length=15, choices=InstitutionCategory.choices)
    district = models.CharField(max_length=60)
    address = models.CharField(max_length=150, blank=True)
    contact_person = models.CharField(max_length=100)
    phone = models.CharField(max_length=15, validators=[RWANDA_PHONE_VALIDATOR])
    email = models.EmailField(max_length=254, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["district"], name="institution_district_idx"),
            models.Index(fields=["is_active"], name="institution_active_idx"),
        ]

    def clean(self):
        super().clean()
        if self.name:
            qs = Institution.objects.filter(name__iexact=self.name.strip())
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({"name": "A institution with this name already exists."})

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class InstitutionMembership(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="institution_membership",
    )

    institution = models.ForeignKey(
        Institution,
        on_delete=models.PROTECT,
        related_name="staff_members",
    )

    class Meta:
        verbose_name = "Institution Membership"
        verbose_name_plural = "Institution Memberships"

    def __str__(self):
        return f"{self.user.username} - {self.institution.name}"