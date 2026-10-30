"""One command to prepare a working demo:

    python manage.py setup_demo

1. generates the synthetic history (replacing existing donor data),
2. trains the three models by reading the API,
3. sends a few incoming events through the ingest API so the dashboard's
   live feed has something to show.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate synthetic data, train the models and simulate incoming events."

    def add_arguments(self, parser):
        parser.add_argument("--donors", type=int, default=3000)
        parser.add_argument("--days", type=int, default=730)
        parser.add_argument("--events", type=int, default=15)
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("1/3 Generating synthetic data"))
        call_command("generate_synthetic_data", donors=options["donors"], days=options["days"],
                     seed=options["seed"], clear=True, stdout=self.stdout)
        self.stdout.write(self.style.MIGRATE_HEADING("2/3 Training models from the API"))
        call_command("train_models", stdout=self.stdout)
        self.stdout.write(self.style.MIGRATE_HEADING("3/3 Simulating incoming data"))
        call_command("simulate_incoming", events=options["events"], seed=options["seed"], stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            "\nDemo ready. Create a login with 'python manage.py createsuperuser' if you have none, "
            "then run 'python manage.py runserver' and open http://127.0.0.1:8000/"
        ))
