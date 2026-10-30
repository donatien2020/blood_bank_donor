from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "analytics"
    verbose_name = "Analytics & predictions"

    def ready(self):
        from . import signals  # noqa: F401  (connects the signal handlers)
