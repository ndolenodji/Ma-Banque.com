"""Branchement du moteur de risque sur les transactions financières.

Le signal ne s'exécute que sur la création d'une transaction. Une erreur du
moteur est journalisée sans faire échouer l'opération bancaire elle-même :
la détection est un contrôle secondaire, jamais un point de blocage des fonds.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from transactions.models import Transaction

from .services import analyser_transaction

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Transaction, dispatch_uid="risques_analyser_nouvelle_transaction")
def analyser_nouvelle_transaction(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        analyser_transaction(instance)
    except Exception:  # pragma: no cover - garde-fou opérationnel
        logger.exception("Échec de l'analyse de risque pour la transaction %s", instance.pk)
