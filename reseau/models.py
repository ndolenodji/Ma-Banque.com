"""
Réseau d'agents et de marchands (simulation de bout en bout).

Les opérations de caisse et les paiements marchands déplacent réellement des
fonds entre comptes internes de la maquette. Aucun règlement interbancaire,
aucun terminal de paiement et aucune API d'acquéreur réelle ne sont utilisés.
"""

import secrets
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from transactions.models import CompteBancaire, Transaction


def generer_reference_operation():
    return f"AGC-{timezone.now():%Y%m%d}-{secrets.token_hex(4).upper()}"


def generer_code_agent():
    # 15 caractères : compatible avec code_agent (max_length=20).
    return f"AGC-{timezone.now():%Y%m%d}-{secrets.token_hex(3).upper()}"


def generer_code_marchand():
    return f"MCH-{secrets.token_hex(3).upper()}"


class ProfilAgent(models.Model):
    utilisateur = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profil_agent"
    )
    code_agent = models.CharField(max_length=20, unique=True, default=generer_code_agent)
    nom_agence = models.CharField(max_length=120)
    ville = models.CharField(max_length=80, blank=True)
    float_caisse = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plafond_journalier = models.DecimalField(max_digits=14, decimal_places=2, default=5000000)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Profil agent"
        verbose_name_plural = "Profils agents"

    def __str__(self):
        return f"{self.utilisateur} — {self.nom_agence} ({self.code_agent})"


class OperationAgent(models.Model):
    class Type(models.TextChoices):
        CAISSE_IN = "CAISSE_IN", "Dépôt espèces (cash-in)"
        CAISSE_OUT = "CAISSE_OUT", "Retrait espèces (cash-out)"

    class Statut(models.TextChoices):
        REUSSIE = "REUSSIE", "Réussie"
        ANNULEE = "ANNULEE", "Annulée"

    reference = models.CharField(max_length=40, unique=True, default=generer_reference_operation)
    agent = models.ForeignKey(ProfilAgent, on_delete=models.PROTECT, related_name="operations")
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="operations_agent"
    )
    compte = models.ForeignKey(CompteBancaire, on_delete=models.PROTECT, related_name="operations_agent")
    type_operation = models.CharField(max_length=20, choices=Type.choices)
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.REUSSIE)
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="operations_agent"
    )
    note = models.CharField(max_length=255, blank=True)
    cle_idempotence = models.CharField(max_length=80, unique=True, null=True, blank=True)
    date_operation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_operation"]
        verbose_name = "Opération agent"
        verbose_name_plural = "Opérations agent"

    def __str__(self):
        return f"{self.get_type_operation_display()} {self.montant} — {self.reference}"

    @classmethod
    @db_transaction.atomic
    def effectuer(
        cls,
        agent,
        compte,
        montant,
        type_operation,
        note="",
        cree_par=None,
        cle_idempotence=None,
    ):
        montant = Decimal(montant).quantize(Decimal("0.01"))
        if montant <= 0:
            raise ValidationError("Le montant doit être positif.")
        if cle_idempotence:
            existante = cls.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante is not None:
                if (
                    existante.agent_id == agent.pk
                    and existante.compte_id == compte.pk
                    and existante.type_operation == type_operation
                    and existante.montant == montant
                ):
                    return existante
                raise ValidationError(
                    "Cette clé d'idempotence est déjà utilisée pour une autre opération."
                )
        if not agent.actif:
            raise ValidationError("Cet agent est désactivé.")
        if compte.statut != CompteBancaire.Statut.ACTIF:
            raise ValidationError("Le compte client n'est pas actif.")

        profil = ProfilAgent.objects.select_for_update().get(pk=agent.pk)
        aujourdhui = timezone.localdate()
        debut_jour = timezone.make_aware(datetime.combine(aujourdhui, time.min))
        fin_jour = timezone.make_aware(datetime.combine(aujourdhui + timedelta(days=1), time.min))
        total_jour = (
            cls.objects.filter(
                agent=profil,
                statut=cls.Statut.REUSSIE,
                date_operation__gte=debut_jour,
                date_operation__lt=fin_jour,
            ).aggregate(total=Sum("montant"))["total"]
            or Decimal("0")
        )
        if total_jour + montant > profil.plafond_journalier:
            raise ValidationError(
                "Plafond journalier de l'agent dépassé : "
                f"{total_jour} déjà réalisés sur {profil.plafond_journalier} autorisés."
            )
        if type_operation == cls.Type.CAISSE_OUT:
            if profil.float_caisse < montant:
                raise ValidationError("Float de caisse insuffisant pour ce retrait.")
            transaction_effectuee = compte.retirer(
                montant,
                description=f"Retrait agent {profil.nom_agence}",
                type_transaction=Transaction.Type.RETRAIT,
                canal=Transaction.Canal.AGENCE,
                cree_par=cree_par or agent.utilisateur,
                tiers_nom=str(compte.proprietaire),
            )
            profil.float_caisse -= montant
        else:
            transaction_effectuee = compte.deposer(
                montant,
                description=f"Dépôt agent {profil.nom_agence}",
                type_transaction=Transaction.Type.DEPOT,
                canal=Transaction.Canal.AGENCE,
                cree_par=cree_par or agent.utilisateur,
                tiers_nom=str(compte.proprietaire),
            )
            profil.float_caisse += montant
        profil.save(update_fields=["float_caisse"])

        return cls.objects.create(
            agent=profil,
            client=compte.proprietaire,
            compte=compte,
            type_operation=type_operation,
            montant=montant,
            transaction=transaction_effectuee,
            note=note,
            cle_idempotence=cle_idempotence or None,
        )


class Marchand(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente de validation"
        ACTIF = "ACTIF", "Actif"
        SUSPENDU = "SUSPENDU", "Suspendu"

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="marchands"
    )
    nom = models.CharField(max_length=120)
    code_marchand = models.CharField(max_length=20, unique=True, default=generer_code_marchand)
    compte_reglement = models.ForeignKey(
        CompteBancaire, on_delete=models.PROTECT, related_name="marchands"
    )
    categorie = models.CharField(max_length=80, blank=True)
    ville = models.CharField(max_length=80, blank=True)
    adresse = models.CharField(max_length=180, blank=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom"]
        verbose_name = "Marchand"
        verbose_name_plural = "Marchands"

    def __str__(self):
        return f"{self.nom} ({self.code_marchand})"

    def clean(self):
        super().clean()
        if self.compte_reglement_id and self.compte_reglement.proprietaire_id != self.proprietaire_id:
            raise ValidationError({"compte_reglement": "Le compte de règlement doit appartenir au marchand."})
        if self.compte_reglement_id and self.compte_reglement.statut != CompteBancaire.Statut.ACTIF:
            raise ValidationError({"compte_reglement": "Le compte de règlement doit être actif."})


class DemandePaiementMarchand(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        PAYEE = "PAYEE", "Payée"
        EXPIREE = "EXPIREE", "Expirée"
        ANNULEE = "ANNULEE", "Annulée"

    marchand = models.ForeignKey(Marchand, on_delete=models.CASCADE, related_name="demandes")
    jeton = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=160, blank=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_expiration = models.DateTimeField()
    date_paiement = models.DateTimeField(null=True, blank=True)
    payeur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demandes_marchand_payees",
    )
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="demandes_marchand"
    )

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Demande de paiement marchand"
        verbose_name_plural = "Demandes de paiement marchand"

    def __str__(self):
        return f"{self.marchand.nom} — {self.montant} — {self.get_statut_display()}"

    @property
    def est_expiree(self):
        return self.statut == self.Statut.EN_ATTENTE and self.date_expiration < timezone.now()

    def clean(self):
        super().clean()
        if self.montant <= 0:
            raise ValidationError({"montant": "Le montant doit être positif."})

    @db_transaction.atomic
    def payer(self, compte, *, frais=Decimal("0"), payeur=None):
        demande = type(self).objects.select_for_update().get(pk=self.pk)
        if demande.statut != self.Statut.EN_ATTENTE:
            raise ValidationError("Cette demande n'est plus payable.")
        if demande.date_expiration < timezone.now():
            demande.statut = self.Statut.EXPIREE
            demande.save(update_fields=["statut"])
            raise ValidationError("Cette demande a expiré.")
        if demande.marchand.statut != Marchand.Statut.ACTIF:
            raise ValidationError("Ce marchand n'est pas actif.")
        if compte.proprietaire_id == demande.marchand.proprietaire_id:
            raise ValidationError("Vous ne pouvez pas payer votre propre demande marchand.")
        if payeur is None or compte.proprietaire_id != payeur.id:
            raise ValidationError("Compte payeur invalide.")

        transaction_effectuee = compte.virer_vers(
            demande.marchand.compte_reglement,
            demande.montant,
            description=demande.description or f"Paiement marchand {demande.marchand.nom}",
            frais=frais,
            type_transaction=Transaction.Type.PAIEMENT_MARCHAND,
            categorie=Transaction.Categorie.AUTRE,
            canal=Transaction.Canal.MARCHAND,
            cree_par=payeur,
            cle_idempotence=f"marchand-{demande.pk}",
        )
        demande.statut = self.Statut.PAYEE
        demande.date_paiement = timezone.now()
        demande.payeur = payeur
        demande.transaction = transaction_effectuee
        demande.save(update_fields=["statut", "date_paiement", "payeur", "transaction"])
        self.statut = demande.statut
        self.date_paiement = demande.date_paiement
        self.transaction = transaction_effectuee
        return transaction_effectuee
