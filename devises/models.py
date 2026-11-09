"""
Devises et taux de change administrés.

Aucun taux réel n'est inventé : la devise XAF (franc CFA, monnaie légale au
Tchad) est la devise de référence avec un taux de 1. Les autres devises
peuvent être créées par un administrateur, qui doit explicitement saisir un
taux indicatif et sa source. Une conversion est refusée tant que le taux
n'est pas configuré.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Devise(models.Model):
    code = models.CharField(max_length=3, unique=True, help_text="Code ISO 4217, ex. XAF, EUR, USD")
    nom = models.CharField(max_length=60)
    symbole = models.CharField(max_length=8, blank=True)
    est_active = models.BooleanField(default=True)
    taux_vers_xaf = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Nombre de XAF pour 1 unité de cette devise. Laisser vide si le taux n'est pas configuré.",
    )
    source_taux = models.CharField(
        max_length=120,
        blank=True,
        help_text="Origine du taux (ex. « taux indicatif BEAC du 20/10/2026, saisi par l'administrateur »).",
    )
    date_maj_taux = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Devise"
        verbose_name_plural = "Devises"

    def __str__(self):
        return f"{self.code} — {self.nom}"

    def clean(self):
        self.code = (self.code or "").strip().upper()
        if self.code and len(self.code) != 3:
            raise ValidationError({"code": "Le code devise doit comporter exactement 3 lettres."})
        if self.taux_vers_xaf is not None and self.taux_vers_xaf <= 0:
            raise ValidationError({"taux_vers_xaf": "Le taux doit être strictement positif."})
        if self.code == "XAF" and self.taux_vers_xaf != Decimal("1"):
            raise ValidationError({"taux_vers_xaf": "La devise XAF est la référence : son taux doit valoir 1."})

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        if self.taux_vers_xaf is not None and self.code != "XAF":
            self.date_maj_taux = timezone.now()
        super().save(*args, **kwargs)

    @property
    def taux_configure(self):
        return self.taux_vers_xaf is not None and self.taux_vers_xaf > 0

    @property
    def libelle_affichage(self):
        """Libellé habituel au Tchad pour la devise de référence."""
        return "FCFA" if self.code == "XAF" else (self.symbole or self.code)

    def vers_xaf(self, montant):
        if self.code == "XAF":
            return Decimal(montant)
        if not self.taux_configure:
            raise ValidationError(
                f"Le taux de change {self.code} → XAF n'est pas configuré. "
                "Un administrateur doit saisir un taux et sa source avant toute conversion."
            )
        return (Decimal(montant) * self.taux_vers_xaf).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def depuis_xaf(self, montant):
        if self.code == "XAF":
            return Decimal(montant)
        if not self.taux_configure:
            raise ValidationError(
                f"Le taux de change XAF → {self.code} n'est pas configuré. "
                "Un administrateur doit saisir un taux et sa source avant toute conversion."
            )
        return (Decimal(montant) / self.taux_vers_xaf).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
