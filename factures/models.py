"""
Paiement de factures simulé.

Aucun fournisseur réel n'est connecté : les fournisseurs sont explicitement
marqués « simulation » et les factures sont créées manuellement ou par un
adaptateur de démonstration. Un paiement débite réellement le compte du
client et trace la transaction, mais ne règle aucune facture externe réelle.
"""

import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction as db_transaction
from django.utils import timezone

from transactions.models import CompteBancaire, Transaction


def reference_facture():
    return f"FAC-{timezone.now():%Y%m%d}-{secrets.token_hex(4).upper()}"


class FournisseurFacture(models.Model):
    class Categorie(models.TextChoices):
        EAU = "EAU", "Eau"
        ELECTRICITE = "ELECTRICITE", "Électricité"
        INTERNET = "INTERNET", "Internet"
        TELECOM = "TELECOM", "Télécom"
        TELEVISION = "TELEVISION", "Télévision"
        AUTRE = "AUTRE", "Autre"

    nom = models.CharField(max_length=120)
    code = models.CharField(max_length=30, unique=True)
    categorie = models.CharField(max_length=20, choices=Categorie.choices)
    description = models.CharField(max_length=255, blank=True)
    est_simule = models.BooleanField(
        default=True, help_text="Toujours vrai : aucun raccordement à un fournisseur réel n'est effectué."
    )
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["categorie", "nom"]
        verbose_name = "Fournisseur de factures (simulation)"
        verbose_name_plural = "Fournisseurs de factures (simulation)"

    def __str__(self):
        return f"{self.nom} ({self.code})"


class CompteFacture(models.Model):
    """Référence client chez un fournisseur, mémorisée pour paiement rapide."""

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comptes_factures"
    )
    fournisseur = models.ForeignKey(FournisseurFacture, on_delete=models.CASCADE, related_name="comptes_clients")
    reference_client = models.CharField(max_length=80)
    libelle = models.CharField(max_length=120, blank=True)
    est_favori = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-est_favori", "fournisseur__nom"]
        unique_together = ("proprietaire", "fournisseur", "reference_client")
        verbose_name = "Référence de facture"
        verbose_name_plural = "Références de factures"

    def __str__(self):
        return f"{self.fournisseur.nom} — {self.reference_client}"


class Facture(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        PAYEE = "PAYEE", "Payée"
        EXPIREE = "EXPIREE", "Expirée"
        ANNULEE = "ANNULEE", "Annulée"

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="factures"
    )
    fournisseur = models.ForeignKey(FournisseurFacture, on_delete=models.PROTECT, related_name="factures")
    reference = models.CharField(max_length=40, unique=True, default=reference_facture)
    reference_externe = models.CharField(max_length=80, blank=True, help_text="Référence chez le fournisseur (simulée)")
    libelle = models.CharField(max_length=160, blank=True)
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    date_emission = models.DateField(default=timezone.localdate)
    date_echeance = models.DateField()
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    date_paiement = models.DateTimeField(null=True, blank=True)
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="factures_payees"
    )
    rappel_envoye = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["statut", "date_echeance"]
        verbose_name = "Facture (simulation)"
        verbose_name_plural = "Factures (simulation)"

    def __str__(self):
        return f"{self.fournisseur.nom} — {self.reference} — {self.montant}"

    def clean(self):
        super().clean()
        if self.montant <= 0:
            raise ValidationError({"montant": "Le montant doit être positif."})
        if self.date_echeance and self.date_emission and self.date_echeance < self.date_emission:
            raise ValidationError({"date_echeance": "L'échéance ne peut pas précéder l'émission."})

    @property
    def est_en_retard(self):
        return self.statut == self.Statut.EN_ATTENTE and self.date_echeance < timezone.localdate()

    @db_transaction.atomic
    def payer(self, compte, *, frais=Decimal("0"), cree_par=None, cle_idempotence=None):
        facture = type(self).objects.select_for_update().get(pk=self.pk)
        if facture.statut != self.Statut.EN_ATTENTE:
            raise ValidationError("Cette facture n'est plus payable.")
        if facture.proprietaire_id != compte.proprietaire_id:
            raise ValidationError("Cette facture ne vous appartient pas.")
        transaction_effectuee = compte.retirer(
            facture.montant,
            description=f"Facture {facture.fournisseur.nom} — {facture.reference}",
            frais=frais,
            type_transaction=Transaction.Type.PAIEMENT_FACTURE,
            categorie=Transaction.Categorie.FACTURES,
            canal=Transaction.Canal.APPLICATION,
            cree_par=cree_par,
            cle_idempotence=cle_idempotence,
            reference_externe=facture.reference_externe,
        )
        facture.statut = self.Statut.PAYEE
        facture.date_paiement = timezone.now()
        facture.transaction = transaction_effectuee
        facture.save(update_fields=["statut", "date_paiement", "transaction"])
        self.statut = facture.statut
        self.date_paiement = facture.date_paiement
        self.transaction = transaction_effectuee
        return transaction_effectuee
