"""
Épargne : objectifs, contributions, arrondis automatiques et budgets.

Les contributions sont de véritables virements internes entre comptes du
client, tracés dans la table Transaction. Aucun intérêt n'est simulé.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from transactions.models import CompteBancaire, Transaction


class ObjectifEpargne(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "En cours"
        ATTEINT = "ATTEINT", "Atteint"
        ANNULE = "ANNULE", "Annulé"

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="objectifs_epargne"
    )
    nom = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    montant_cible = models.DecimalField(max_digits=14, decimal_places=2)
    montant_actuel = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    compte_epargne = models.ForeignKey(
        CompteBancaire, on_delete=models.PROTECT, related_name="objectifs_epargne"
    )
    date_cible = models.DateField(null=True, blank=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.ACTIF)
    couleur = models.CharField(max_length=20, default="#1656d1")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["statut", "date_cible", "-date_creation"]
        verbose_name = "Objectif d'épargne"
        verbose_name_plural = "Objectifs d'épargne"

    def __str__(self):
        return f"{self.nom} ({self.montant_actuel}/{self.montant_cible})"

    @property
    def progression(self):
        if not self.montant_cible:
            return 0
        return min(100, int((self.montant_actuel / self.montant_cible) * 100))

    @property
    def restant(self):
        return max(Decimal("0"), self.montant_cible - self.montant_actuel)

    def clean(self):
        super().clean()
        if self.montant_cible <= 0:
            raise ValidationError({"montant_cible": "L'objectif doit être supérieur à zéro."})
        if self.compte_epargne_id and self.compte_epargne.proprietaire_id != self.proprietaire_id:
            raise ValidationError({"compte_epargne": "Le compte d'épargne doit vous appartenir."})
        if self.compte_epargne_id and self.compte_epargne.type_compte != CompteBancaire.TypeCompte.EPARGNE:
            raise ValidationError({"compte_epargne": "Sélectionnez un compte de type épargne."})

    @transaction.atomic
    def contribuer(self, compte_source, montant, description="", *, cle_idempotence=None):
        montant = Decimal(montant).quantize(Decimal("0.01"))
        if montant <= 0:
            raise ValidationError("Le montant de la contribution doit être positif.")
        cle = cle_idempotence or f"objectif-{self.pk}-{uuid.uuid4()}"
        existante = Transaction.objects.filter(cle_idempotence=cle).first()
        if existante is not None:
            if (
                existante.compte_source_id == compte_source.pk
                and existante.compte_destination_id == self.compte_epargne_id
                and existante.montant == montant
            ):
                return existante
            raise ValidationError(
                "Cette clé d'idempotence est déjà utilisée pour une autre contribution."
            )
        objectif = type(self).objects.select_for_update().get(pk=self.pk)
        if objectif.statut != self.Statut.ACTIF:
            raise ValidationError("Cet objectif n'est plus actif.")
        if compte_source.proprietaire_id != objectif.proprietaire_id:
            raise ValidationError("Le compte source ne vous appartient pas.")
        if compte_source.pk == objectif.compte_epargne_id:
            raise ValidationError("Le compte source doit être différent du compte d'épargne.")
        if montant > objectif.restant:
            raise ValidationError("La contribution dépasse le montant restant de l'objectif.")

        transaction_effectuee = compte_source.virer_vers(
            objectif.compte_epargne,
            montant,
            description=description or f"Épargne — {objectif.nom}",
            categorie=Transaction.Categorie.EPARGNE,
            cle_idempotence=cle,
        )
        objectif.montant_actuel += montant
        if objectif.montant_actuel >= objectif.montant_cible:
            objectif.statut = self.Statut.ATTEINT
        objectif.save(update_fields=["montant_actuel", "statut", "date_mise_a_jour"])

        ContributionEpargne.objects.create(
            objectif=objectif,
            transaction=transaction_effectuee,
            montant=montant,
            type_contribution=ContributionEpargne.Type.MANUELLE,
        )
        self.montant_actuel = objectif.montant_actuel
        self.statut = objectif.statut
        return transaction_effectuee


class ContributionEpargne(models.Model):
    class Type(models.TextChoices):
        MANUELLE = "MANUELLE", "Versement manuel"
        ARRONDI = "ARRONDI", "Arrondi automatique"

    objectif = models.ForeignKey(ObjectifEpargne, on_delete=models.CASCADE, related_name="contributions")
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="contributions_epargne"
    )
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    type_contribution = models.CharField(max_length=12, choices=Type.choices, default=Type.MANUELLE)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Contribution d'épargne"
        verbose_name_plural = "Contributions d'épargne"


class ConfigArrondi(models.Model):
    """Arrondi automatique des dépenses vers un compte d'épargne."""

    class Multiple(models.IntegerChoices):
        CINQ_CENTS = 500, "500 FCFA"
        MILLE = 1000, "1 000 FCFA"
        CINQ_MILLE = 5000, "5 000 FCFA"

    proprietaire = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="config_arrondi"
    )
    actif = models.BooleanField(default=False)
    compte_source = models.ForeignKey(
        CompteBancaire, on_delete=models.CASCADE, related_name="regles_arrondi_source"
    )
    compte_epargne = models.ForeignKey(
        CompteBancaire, on_delete=models.CASCADE, related_name="regles_arrondi_epargne"
    )
    multiple = models.PositiveIntegerField(choices=Multiple.choices, default=Multiple.MILLE)
    objectif = models.ForeignKey(
        ObjectifEpargne, on_delete=models.SET_NULL, null=True, blank=True, related_name="regles_arrondi"
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Configuration d'arrondi"
        verbose_name_plural = "Configurations d'arrondi"

    def __str__(self):
        return f"Arrondi {'actif' if self.actif else 'inactif'} — {self.proprietaire}"

    def clean(self):
        super().clean()
        if self.compte_source_id and self.compte_source.proprietaire_id != self.proprietaire_id:
            raise ValidationError({"compte_source": "Compte source invalide."})
        if self.compte_epargne_id and self.compte_epargne.proprietaire_id != self.proprietaire_id:
            raise ValidationError({"compte_epargne": "Compte d'épargne invalide."})
        if self.compte_source_id and self.compte_epargne_id and self.compte_source_id == self.compte_epargne_id:
            raise ValidationError("Les comptes source et d'épargne doivent être différents.")
        if self.compte_epargne_id and self.compte_epargne.type_compte != CompteBancaire.TypeCompte.EPARGNE:
            raise ValidationError({"compte_epargne": "Sélectionnez un compte de type épargne."})


class Budget(models.Model):
    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budgets"
    )
    categorie = models.CharField(max_length=20, choices=Transaction.Categorie.choices)
    plafond_mensuel = models.DecimalField(max_digits=14, decimal_places=2)
    seuil_alerte = models.PositiveSmallIntegerField(default=80, help_text="Pourcentage d'alerte (1 à 100)")
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["categorie"]
        unique_together = ("proprietaire", "categorie")
        verbose_name = "Budget"
        verbose_name_plural = "Budgets"

    def __str__(self):
        return f"{self.get_categorie_display()} — {self.plafond_mensuel} / mois"

    def clean(self):
        super().clean()
        if self.plafond_mensuel <= 0:
            raise ValidationError({"plafond_mensuel": "Le plafond doit être positif."})
        if not 1 <= self.seuil_alerte <= 100:
            raise ValidationError({"seuil_alerte": "Le seuil doit être compris entre 1 et 100."})

    def montant_depense(self, mois=None):
        # Bornes calculées en Python : les lookups ``__date`` de MySQL
        # dépendent des tables de fuseaux horaires, qui peuvent être absentes.
        from datetime import date, datetime, time

        mois = mois or timezone.localdate()
        debut = mois.replace(day=1)
        if mois.month == 12:
            fin_exclue = date(mois.year + 1, 1, 1)
        else:
            fin_exclue = date(mois.year, mois.month + 1, 1)
        debut_dt = timezone.make_aware(datetime.combine(debut, time.min))
        fin_dt = min(
            timezone.make_aware(datetime.combine(fin_exclue, time.min)),
            timezone.now(),
        )
        comptes = self.proprietaire.comptes_bancaires.all()
        total = Transaction.objects.filter(
            compte_source__in=comptes,
            categorie=self.categorie,
            statut=Transaction.Statut.REUSSIE,
            date_transaction__gte=debut_dt,
            date_transaction__lt=fin_dt,
        ).aggregate(total=models.Sum("montant"))["total"] or Decimal("0")
        return total

    @property
    def pourcentage_consomme(self):
        if not self.plafond_mensuel:
            return 0
        return min(999, int((self.montant_depense() / self.plafond_mensuel) * 100))

    @property
    def depassement(self):
        return self.montant_depense() > self.plafond_mensuel
