from django.conf import settings
from django.db import models

from donors.models import BloodGroup, Donor


class ModelKind(models.TextChoices):
    DEMAND = "DEMAND", "Blood demand forecast"
    DONOR_RETURN = "DONOR_RETURN", "Donor return likelihood"
    DEFERRAL = "DEFERRAL", "Donation deferral risk"


class RiskLevel(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"


class TrainedModel(models.Model):
    """One trained version of a model. The weights live in a JSON file."""

    kind = models.CharField(max_length=20, choices=ModelKind.choices)
    version = models.PositiveIntegerField()
    file_name = models.CharField(max_length=200)
    trained_at = models.DateTimeField(auto_now_add=True)
    data_source = models.CharField(max_length=200, help_text="API the training data was read from")
    train_samples = models.PositiveIntegerField(default=0)
    test_samples = models.PositiveIntegerField(default=0)
    metrics = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "version"], name="unique_model_kind_version"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} v{self.version}"


class DemandForecast(models.Model):
    """Latest 7-day demand forecast for one blood group."""

    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices, unique=True)
    horizon_days = models.PositiveSmallIntegerField(default=7)
    predicted_units = models.FloatField()
    available_units = models.PositiveIntegerField()
    expiring_units = models.PositiveIntegerField(
        help_text="Available units that expire inside the forecast horizon"
    )
    shortfall_units = models.FloatField()
    risk_level = models.CharField(max_length=6, choices=RiskLevel.choices)
    model = models.ForeignKey(TrainedModel, on_delete=models.SET_NULL, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["blood_group"]

    @property
    def usable_units(self):
        return self.available_units - self.expiring_units


class DonorPrediction(models.Model):
    """Latest return-likelihood prediction for one donor."""

    donor = models.OneToOneField(Donor, on_delete=models.CASCADE, related_name="prediction")
    return_probability = models.FloatField(help_text="Chance of donating again within 180 days")
    model = models.ForeignKey(TrainedModel, on_delete=models.SET_NULL, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["return_probability"], name="donorpred_prob_idx")]


class ScreeningCheck(models.Model):
    """A pre-donation screening received from the API, with its deferral risk."""

    donor = models.ForeignKey(Donor, on_delete=models.CASCADE, related_name="screenings")
    screening_date = models.DateField()
    systolic_bp = models.PositiveSmallIntegerField()
    diastolic_bp = models.PositiveSmallIntegerField()
    weight_kg = models.DecimalField(max_digits=4, decimal_places=1)
    deferral_risk = models.FloatField()
    risk_level = models.CharField(max_length=6, choices=RiskLevel.choices)
    top_factors = models.JSONField(default=list)
    source = models.CharField(max_length=60, default="api")
    model = models.ForeignKey(TrainedModel, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-created_at"]
