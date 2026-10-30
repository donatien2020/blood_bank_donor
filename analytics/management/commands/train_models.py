"""Train the prediction models on data read from the API.

    python manage.py train_models                      # reads the API in-process
    python manage.py train_models --api-url http://127.0.0.1:8000 --api-key KEY
    python manage.py train_models --only DEMAND
"""

import json

from django.core.management.base import BaseCommand, CommandError

from analytics import services
from analytics.client import ApiError, get_client
from analytics.ml.training import train_all
from analytics.models import ModelKind, TrainedModel


class Command(BaseCommand):
    help = "Read data from the blood bank API, train the models and deploy them."

    def add_arguments(self, parser):
        parser.add_argument("--api-url", help="Base URL of a running server. Default: call the API in-process.")
        parser.add_argument("--api-key", help="X-API-Key value. Default: settings.ANALYTICS_API_KEY.")
        parser.add_argument("--only", nargs="+", choices=[k.value for k in ModelKind],
                            help="Train only these models.")

    def handle(self, *args, **options):
        client = get_client(options["api_url"], options["api_key"])
        try:
            results = train_all(client, kinds=options["only"], log=self.stdout.write)
        except ApiError as error:
            raise CommandError(str(error))

        trained = [r for r in results.values() if isinstance(r, TrainedModel)]
        if not trained:
            raise CommandError("No model could be trained. Generate data first: python manage.py generate_synthetic_data")

        self.stdout.write("\nEvaluation on the held-out (most recent) period:")
        for record in trained:
            self.stdout.write(f"  {record}: {json.dumps(record.metrics)}")

        self.stdout.write("\nRefreshing stored predictions with the new models ...")
        forecasts = services.refresh_demand_forecasts()
        scored = services.refresh_all_donor_predictions()
        self.stdout.write(self.style.SUCCESS(
            f"Done: {len(forecasts)} blood-group forecasts and {scored} donor predictions updated."
        ))
