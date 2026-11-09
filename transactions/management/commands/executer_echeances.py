"""Exécution des échéances de virements programmés.

À planifier une fois par jour (cron, planificateur Windows, etc.) :

    python manage.py executer_echeances
"""

from django.core.management.base import BaseCommand

from transactions.services import executer_echeances


class Command(BaseCommand):
    help = "Exécute les échéances de virements programmés arrivées à terme."

    def handle(self, *args, **options):
        succes, echecs = executer_echeances()
        self.stdout.write(
            self.style.SUCCESS(
                f"Virements programmés : {succes} échéance(s) exécutée(s), "
                f"{echecs} non aboutie(s)."
            )
        )
