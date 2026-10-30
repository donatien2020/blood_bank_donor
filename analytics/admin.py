from django.contrib import admin

from .models import DemandForecast, DonorPrediction, ScreeningCheck, TrainedModel


@admin.register(TrainedModel)
class TrainedModelAdmin(admin.ModelAdmin):
    list_display = ("kind", "version", "is_active", "trained_at", "train_samples", "test_samples", "data_source")
    list_filter = ("kind", "is_active")
    readonly_fields = ("kind", "version", "file_name", "trained_at", "data_source",
                       "train_samples", "test_samples", "metrics")


@admin.register(DemandForecast)
class DemandForecastAdmin(admin.ModelAdmin):
    list_display = ("blood_group", "predicted_units", "available_units", "expiring_units",
                    "shortfall_units", "risk_level", "updated_at")


@admin.register(DonorPrediction)
class DonorPredictionAdmin(admin.ModelAdmin):
    list_display = ("donor", "return_probability", "model", "updated_at")
    search_fields = ("donor__first_name", "donor__last_name", "donor__national_id")
    ordering = ("-return_probability",)


@admin.register(ScreeningCheck)
class ScreeningCheckAdmin(admin.ModelAdmin):
    list_display = ("donor", "screening_date", "systolic_bp", "diastolic_bp", "deferral_risk", "risk_level", "source")
    list_filter = ("risk_level", "source")
    search_fields = ("donor__first_name", "donor__last_name", "donor__national_id")
