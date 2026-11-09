"""
Modèle PaiementMobile : trace, côté Django (base "de vérité" MySQL), toutes
les opérations de paiement mobile initiées par les clients. Chaque
enregistrement est synchronisé avec le microservice FastAPI via
`paiement_mobile/client_api.py`.
"""

from django.conf import settings
from django.db import models


class PaiementMobile(models.Model):
    class Operateur(models.TextChoices):
        MTN = "MTN", "MTN Mobile Money"
        ORANGE = "ORANGE", "Orange Money"
        AIRTEL = "AIRTEL", "Airtel Money"
        MOOV = "MOOV", "Moov Money"

    class TypeOperation(models.TextChoices):
        RECHARGE = "RECHARGE", "Recharge (cash-in vers le compte bancaire)"
        RETRAIT_MOBILE = "RETRAIT_MOBILE", "Retrait mobile (cash-out vers mobile money)"
        PAIEMENT_MARCHAND = "PAIEMENT_MARCHAND", "Paiement marchand"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        REUSSI = "REUSSI", "Réussi"
        ECHOUE = "ECHOUE", "Échoué"
        ANNULE = "ANNULE", "Annulé"

    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paiements_mobiles")
    compte_bancaire = models.ForeignKey(
        "transactions.CompteBancaire", on_delete=models.CASCADE, related_name="paiements_mobiles"
    )
    reference_transaction = models.CharField(max_length=40, unique=True, db_index=True)
    reference_externe = models.CharField(
        max_length=80, blank=True, help_text="Identifiant renvoyé par le microservice FastAPI"
    )
    type_operation = models.CharField(max_length=20, choices=TypeOperation.choices)
    operateur = models.CharField(max_length=10, choices=Operateur.choices)
    numero_telephone = models.CharField(max_length=20)
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    frais = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    message_erreur = models.CharField(max_length=255, blank=True)
    cle_idempotence = models.CharField(max_length=80, unique=True, null=True, blank=True)
    transaction_bancaire = models.ForeignKey(
        "transactions.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paiements_mobiles",
        help_text="Débit ou crédit bancaire lié à l'opération mobile.",
    )
    transaction_remboursement = models.ForeignKey(
        "transactions.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="remboursements_paiements_mobiles",
    )
    tentatives_verification = models.PositiveIntegerField(default=0)
    est_simule = models.BooleanField(
        default=True, help_text="Opération de démonstration : aucune transaction monétaire réelle chez l'opérateur."
    )
    date_finalisation = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Paiement mobile"
        verbose_name_plural = "Paiements mobiles"
        indexes = [
            models.Index(fields=["client", "statut"], name="pm_client_statut_idx"),
            models.Index(fields=["statut", "date_creation"], name="pm_statut_date_idx"),
        ]

    @property
    def montant_total(self):
        return self.montant + (self.frais or 0)

    def __str__(self):
        return f"{self.get_type_operation_display()} - {self.montant} FCFA - {self.get_statut_display()}"
