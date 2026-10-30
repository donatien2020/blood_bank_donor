"""Recompute all stored predictions with the active models (e.g. nightly cron).

    python manage.py refresh_predictions
"""

from django.core.management.base import BaseCommand

from analytics import services


class Command(BaseCommand):
    help = "Recompute demand forecasts and donor-return predictions."

    def handle(self, *args, **options):
        forecasts = services.refresh_demand_forecasts()
        scored = services.refresh_all_donor_predictions()
        if not forecasts and not scored:
            self.stdout.write(self.style.WARNING("No trained models yet. Run: python manage.py train_models"))
            return
        for f in forecasts.values():
            self.stdout.write(
                f"  {f.blood_group:>3}: forecast {f.predicted_units:5.1f} units / 7 days, "
                f"{f.available_units} available -> {f.get_risk_level_display()} risk"
            )
        self.stdout.write(self.style.SUCCESS(f"Updated {len(forecasts)} forecasts and {scored} donor predictions."))
