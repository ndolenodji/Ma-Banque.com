"""
Cartes bancaires de démonstration.

Aucune carte réelle n'est émise : le numéro affiché est un identifiant de
démonstration, jamais un PAN valide, et aucun CVV/PIN n'est généré ni stocké.
Les contrôles (gel, plafonds, usage en ligne/international) sont fonctionnels
dans l'application mais ne pilotent aucun réseau de cartes réel.
"""

import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class CarteBancaire(models.Model):
    class TypeCarte(models.TextChoices):
        VIRTUELLE = "VIRTUELLE", "Carte virtuelle de démonstration"
        PHYSIQUE = "PHYSIQUE", "Carte physique de démonstration"

    class Statut(models.TextChoices):
        DEMANDEE = "DEMANDEE", "Demandée"
        ACTIVE = "ACTIVE", "Active"
        GELEE = "GELEE", "Gelée"
        EXPIREE = "EXPIREE", "Expirée"
        ANNULEE = "ANNULEE", "Annulée"

    class ReseauDemo(models.TextChoices):
        DEMO = "DEMO", "Réseau de démonstration"

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cartes_bancaires"
    )
    compte = models.ForeignKey(
        "transactions.CompteBancaire", on_delete=models.PROTECT, related_name="cartes_bancaires"
    )
    type_carte = models.CharField(max_length=12, choices=TypeCarte.choices, default=TypeCarte.VIRTUELLE)
    nom_titulaire = models.CharField(max_length=120)
    identifiant_demo = models.CharField(
        max_length=24,
        unique=True,
        help_text="Identifiant de démonstration — jamais un numéro de carte réel.",
    )
    quatre_derniers = models.CharField(max_length=4, blank=True)
    date_expiration = models.DateField()
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.DEMANDEE)
    reseau = models.CharField(max_length=12, choices=ReseauDemo.choices, default=ReseauDemo.DEMO)

    paiements_en_ligne = models.BooleanField(default=True)
    paiements_internationaux = models.BooleanField(default=False)
    retraits_autorises = models.BooleanField(default=True)
    plafond_journalier = models.DecimalField(max_digits=14, decimal_places=2, default=500000)
    plafond_mensuel = models.DecimalField(max_digits=14, decimal_places=2, default=2000000)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)
    motif_annulation = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Carte bancaire"
        verbose_name_plural = "Cartes bancaires"

    def __str__(self):
        return f"{self.get_type_carte_display()} {self.numero_masque} — {self.utilisateur}"

    def save(self, *args, **kwargs):
        if not self.identifiant_demo:
            self.identifiant_demo = f"DEMO-{secrets.token_hex(6).upper()}"
        if not self.quatre_derniers:
            self.quatre_derniers = f"{secrets.randbelow(10000):04d}"
        super().save(*args, **kwargs)

    @property
    def numero_masque(self):
        return f"•••• •••• •••• {self.quatre_derniers}"

    @property
    def est_utilisable(self):
        return self.statut == self.Statut.ACTIVE and self.date_expiration >= timezone.localdate()

    def clean(self):
        super().clean()
        if self.compte_id and self.compte.proprietaire_id != self.utilisateur_id:
            raise ValidationError({"compte": "La carte doit être adossée à un compte du titulaire."})
        if self.compte_id and self.compte.statut != "ACTIF":
            raise ValidationError({"compte": "Le compte support doit être actif."})
        if self.plafond_journalier <= 0 or self.plafond_mensuel <= 0:
            raise ValidationError("Les plafonds doivent être positifs.")
        if self.plafond_journalier > self.plafond_mensuel:
            raise ValidationError("Le plafond journalier ne peut pas dépasser le plafond mensuel.")

    def geler(self):
        if self.statut != self.Statut.ACTIVE:
            raise ValidationError("Seule une carte active peut être gelée.")
        self.statut = self.Statut.GELEE
        self.save(update_fields=["statut", "date_mise_a_jour"])

    def reactiver(self):
        if self.statut != self.Statut.GELEE:
            raise ValidationError("Seule une carte gelée peut être réactivée.")
        if self.date_expiration < timezone.localdate():
            self.statut = self.Statut.EXPIREE
            self.save(update_fields=["statut", "date_mise_a_jour"])
            raise ValidationError("Cette carte est expirée.")
        self.statut = self.Statut.ACTIVE
        self.save(update_fields=["statut", "date_mise_a_jour"])

    def annuler(self, motif=""):
        if self.statut == self.Statut.ANNULEE:
            return
        self.statut = self.Statut.ANNULEE
        self.motif_annulation = motif[:255]
        self.save(update_fields=["statut", "motif_annulation", "date_mise_a_jour"])
