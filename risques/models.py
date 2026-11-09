"""
Détection et suivi des opérations à risque.

Le moteur attribue un score explicable et crée une alerte soumise à revue
humaine. Aucun blocage automatique de fonds n'est effectué par le score :
les décisions sensibles restent humaines et journalisées.
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def reference_alerte():
    return f"ALR-{timezone.now():%Y%m%d}-{secrets.token_hex(3).upper()}"


class AlerteFraude(models.Model):
    class Niveau(models.TextChoices):
        FAIBLE = "FAIBLE", "Faible"
        MOYEN = "MOYEN", "Moyen"
        ELEVE = "ELEVE", "Élevé"
        CRITIQUE = "CRITIQUE", "Critique"

    class Statut(models.TextChoices):
        NOUVELLE = "NOUVELLE", "Nouvelle"
        EN_REVUE = "EN_REVUE", "En revue"
        CONFIRMEE = "CONFIRMEE", "Confirmée"
        REJETEE = "REJETEE", "Rejetée (faux positif)"

    reference = models.CharField(max_length=30, unique=True, default=reference_alerte)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alertes_fraude"
    )
    transaction = models.ForeignKey(
        "transactions.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertes_fraude",
    )
    paiement_mobile = models.ForeignKey(
        "paiement_mobile.PaiementMobile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertes_fraude",
    )
    score = models.PositiveSmallIntegerField(default=0)
    niveau = models.CharField(max_length=10, choices=Niveau.choices, default=Niveau.FAIBLE)
    regles = models.JSONField(default=list, blank=True)
    explication = models.TextField(blank=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.NOUVELLE)
    traite_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertes_traitees",
    )
    commentaire = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_traitement = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["statut", "-score", "-date_creation"]
        verbose_name = "Alerte de fraude"
        verbose_name_plural = "Alertes de fraude"

    def __str__(self):
        return f"{self.reference} — score {self.score} — {self.utilisateur}"
