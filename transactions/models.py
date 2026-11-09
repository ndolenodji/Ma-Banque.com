"""
Modèles bancaires : comptes, transactions, grille tarifaire, virements
programmés et conversions de devises.

Toutes les opérations financières passent par les méthodes de ce module.
Elles sont atomiques et verrouillent les lignes de compte concernées avec
``SELECT ... FOR UPDATE`` (MySQL/InnoDB) afin d'éviter les doubles débits
et les mises à jour perdues en cas de concurrence.
"""

import secrets
import string
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

CENTIME = Decimal("0.01")


def generer_numero_compte():
    """Génère un numéro de compte à 10 chiffres, unique."""
    while True:
        numero = "".join(secrets.choice(string.digits) for _ in range(10))
        if not CompteBancaire.objects.filter(numero_compte=numero).exists():
            return numero


def generer_reference_transaction():
    """Référence unique, lisible et non devinable : TRX-AAAAMMJJ-XXXXXXXXXXXX."""
    return f"TRX-{timezone.now():%Y%m%d}-{secrets.token_hex(6).upper()}"


def devise_xaf_par_defaut():
    """Retourne la clé primaire de la devise XAF (créée par migration)."""
    from devises.models import Devise

    return Devise.objects.get(code="XAF").pk


def decimal_fiable(valeur):
    """Convertit une valeur en Decimal arrondi au centime, sans passer par float."""
    try:
        decimal = Decimal(str(valeur))
    except (InvalidOperation, TypeError, ValueError) as erreur:
        raise ValidationError("Le montant fourni n'est pas un nombre valide.") from erreur
    if not decimal.is_finite():
        raise ValidationError("Le montant fourni n'est pas valide.")
    return decimal.quantize(CENTIME, rounding=ROUND_HALF_UP)


class CompteBancaire(models.Model):
    class TypeCompte(models.TextChoices):
        COURANT = "COURANT", "Compte courant"
        EPARGNE = "EPARGNE", "Compte épargne"
        PROFESSIONNEL = "PROFESSIONNEL", "Compte professionnel"

    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        BLOQUE = "BLOQUE", "Bloqué"
        FERME = "FERME", "Fermé"

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comptes_bancaires"
    )
    numero_compte = models.CharField(max_length=10, unique=True, default=generer_numero_compte)
    type_compte = models.CharField(max_length=20, choices=TypeCompte.choices, default=TypeCompte.COURANT)
    devise = models.ForeignKey(
        "devises.Devise",
        on_delete=models.PROTECT,
        related_name="comptes",
        default=devise_xaf_par_defaut,
        verbose_name="Devise",
    )

    # ``solde`` est le solde comptable. ``solde_bloque`` (garanties, saisies)
    # et ``solde_en_attente`` (opérations en cours) ne sont pas disponibles.
    solde = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    solde_bloque = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    solde_en_attente = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ACTIF)

    plafond_journalier = models.DecimalField(
        max_digits=14, decimal_places=2, default=settings.PLAFOND_JOURNALIER_DEFAUT
    )
    plafond_hebdomadaire = models.DecimalField(
        max_digits=14, decimal_places=2, default=settings.PLAFOND_HEBDOMADAIRE_DEFAUT
    )
    plafond_mensuel = models.DecimalField(
        max_digits=14, decimal_places=2, default=settings.PLAFOND_MENSUEL_DEFAUT
    )

    date_ouverture = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)
    motif_blocage = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date_ouverture"]
        verbose_name = "Compte bancaire"
        verbose_name_plural = "Comptes bancaires"
        indexes = [
            models.Index(fields=["proprietaire", "statut"], name="compte_prop_statut_idx"),
        ]

    def __str__(self):
        return f"{self.get_type_compte_display()} n°{self.numero_compte} ({self.proprietaire})"

    def est_actif(self):
        return self.statut == self.Statut.ACTIF

    @property
    def solde_disponible(self):
        """Montant réellement mobilisable (hors garanties et opérations en cours)."""
        return (self.solde or Decimal("0")) - (self.solde_bloque or Decimal("0")) - (
            self.solde_en_attente or Decimal("0")
        )

    @property
    def libelle_devise(self):
        return self.devise.libelle_affichage if self.devise_id else "FCFA"

    @property
    def code_devise(self):
        return self.devise.code if self.devise_id else "XAF"

    def _recharger_verrouille(self):
        """Recharge le compte sous verrou de ligne ; met aussi à jour self."""
        compte = type(self).objects.select_for_update().get(pk=self.pk)
        self.solde = compte.solde
        self.solde_bloque = compte.solde_bloque
        self.solde_en_attente = compte.solde_en_attente
        self.statut = compte.statut
        self.devise = compte.devise
        return compte

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------
    def total_mouvemente_sur(self, depuis):
        """Somme des sorties engagées depuis une date (réussies ou en attente)."""
        filtres = {
            "compte_source": self,
            "date_transaction__gte": depuis,
            "statut__in": [Transaction.Statut.REUSSIE, Transaction.Statut.EN_ATTENTE],
        }
        agregat = self.transactions_sortantes.filter(**filtres).aggregate(
            total=models.Sum("montant"), frais=models.Sum("frais")
        )
        return (agregat["total"] or Decimal("0")) + (agregat["frais"] or Decimal("0"))

    def verifier_plafonds(self, montant):
        """Vérifie les plafonds journalier, hebdomadaire et mensuel."""
        montant = decimal_fiable(montant)
        maintenant = timezone.localtime()
        debut_jour = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        debut_semaine = debut_jour - timedelta(days=maintenant.weekday())
        debut_mois = debut_jour.replace(day=1)

        deja_aujourdhui = self.total_mouvemente_sur(debut_jour)
        deja_cette_semaine = self.total_mouvemente_sur(debut_semaine)
        deja_ce_mois = self.total_mouvemente_sur(debut_mois)

        if deja_aujourdhui + montant > self.plafond_journalier:
            raise ValidationError("Le plafond journalier de ce compte serait dépassé.")
        if deja_cette_semaine + montant > self.plafond_hebdomadaire:
            raise ValidationError("Le plafond hebdomadaire de ce compte serait dépassé.")
        if deja_ce_mois + montant > self.plafond_mensuel:
            raise ValidationError("Le plafond mensuel de ce compte serait dépassé.")

    def _controler_debit(self, montant_total):
        if montant_total > self.solde_disponible:
            raise ValidationError("Votre solde est insuffisant.")

    # ------------------------------------------------------------------
    # Opérations bancaires (toutes atomiques et verrouillées)
    # ------------------------------------------------------------------
    @transaction.atomic
    def deposer(
        self,
        montant,
        description="Dépôt d'argent",
        *,
        type_transaction=None,
        categorie="",
        canal=None,
        cree_par=None,
        cle_idempotence=None,
        tiers_nom="",
        tiers_telephone="",
        reference_externe="",
        statut=None,
    ):
        montant = decimal_fiable(montant)
        canal = canal or Transaction.Canal.APPLICATION
        statut = statut or Transaction.Statut.REUSSIE
        if montant <= 0:
            raise ValidationError("Le montant doit être positif.")

        if cle_idempotence:
            existante = Transaction.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante:
                if existante.compte_destination_id == self.pk or existante.compte_source_id == self.pk:
                    return existante
                raise ValidationError("Cette clé d'idempotence est déjà utilisée.")

        compte = self._recharger_verrouille()
        if cle_idempotence:
            existante = Transaction.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante:
                if existante.compte_destination_id == self.pk or existante.compte_source_id == self.pk:
                    return existante
                raise ValidationError("Cette clé d'idempotence est déjà utilisée.")
        if not compte.est_actif():
            raise ValidationError("Ce compte est bloqué : opération impossible.")

        compte.solde = compte.solde + montant
        compte.save(update_fields=["solde", "date_mise_a_jour"])
        self.solde = compte.solde

        return Transaction.objects.create(
            type_transaction=type_transaction or Transaction.Type.DEPOT,
            montant=montant,
            compte_destination=compte,
            statut=statut,
            description=description,
            categorie=categorie,
            canal=canal,
            cree_par=cree_par,
            cle_idempotence=cle_idempotence or None,
            tiers_nom=tiers_nom,
            tiers_telephone=tiers_telephone,
            reference_externe=reference_externe,
            solde_apres_destination=compte.solde,
            devise=compte.code_devise,
        )

    @transaction.atomic
    def retirer(
        self,
        montant,
        description="Retrait d'argent",
        *,
        frais=Decimal("0"),
        type_transaction=None,
        categorie="",
        canal=None,
        cree_par=None,
        cle_idempotence=None,
        tiers_nom="",
        tiers_telephone="",
        reference_externe="",
        statut=None,
    ):
        montant = decimal_fiable(montant)
        frais = decimal_fiable(frais or 0)
        canal = canal or Transaction.Canal.APPLICATION
        statut = statut or Transaction.Statut.REUSSIE
        if montant <= 0:
            raise ValidationError("Le montant doit être positif.")
        if frais < 0:
            raise ValidationError("Les frais ne peuvent pas être négatifs.")
        total = montant + frais

        if cle_idempotence:
            existante = Transaction.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante:
                if existante.compte_source_id == self.pk:
                    return existante
                raise ValidationError("Cette clé d'idempotence est déjà utilisée.")

        compte = self._recharger_verrouille()
        if cle_idempotence:
            existante = Transaction.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante:
                if existante.compte_source_id == self.pk:
                    return existante
                raise ValidationError("Cette clé d'idempotence est déjà utilisée.")
        if not compte.est_actif():
            raise ValidationError("Ce compte est bloqué : opération impossible.")
        compte._controler_debit(total)
        compte.verifier_plafonds(total)

        compte.solde = compte.solde - total
        compte.save(update_fields=["solde", "date_mise_a_jour"])
        self.solde = compte.solde

        return Transaction.objects.create(
            type_transaction=type_transaction or Transaction.Type.RETRAIT,
            montant=montant,
            frais=frais,
            compte_source=compte,
            statut=statut,
            description=description,
            categorie=categorie,
            canal=canal,
            cree_par=cree_par,
            cle_idempotence=cle_idempotence or None,
            tiers_nom=tiers_nom,
            tiers_telephone=tiers_telephone,
            reference_externe=reference_externe,
            solde_apres=compte.solde,
            devise=compte.code_devise,
        )

    @transaction.atomic
    def virer_vers(
        self,
        compte_destination,
        montant,
        description="Virement",
        *,
        frais=Decimal("0"),
        type_transaction=None,
        categorie="",
        canal=None,
        cree_par=None,
        cle_idempotence=None,
    ):
        montant = decimal_fiable(montant)
        frais = decimal_fiable(frais or 0)
        canal = canal or Transaction.Canal.APPLICATION
        if montant <= 0:
            raise ValidationError("Le montant doit être positif.")
        if frais < 0:
            raise ValidationError("Les frais ne peuvent pas être négatifs.")
        if self.pk == compte_destination.pk:
            raise ValidationError("Impossible de virer un montant vers le même compte.")
        total = montant + frais

        if cle_idempotence:
            existante = Transaction.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante:
                if existante.compte_source_id == self.pk:
                    return existante
                raise ValidationError("Cette clé d'idempotence est déjà utilisée.")

        # Verrouillage déterministe (ordre des clés) pour éviter tout interblocage.
        comptes = {
            compte.pk: compte
            for compte in type(self)
            .objects.select_for_update()
            .filter(pk__in=[self.pk, compte_destination.pk])
            .order_by("pk")
        }
        compte_source = comptes.get(self.pk)
        compte_cible = comptes.get(compte_destination.pk)
        if compte_source is None or compte_cible is None:
            raise ValidationError("Un des comptes est introuvable.")
        if cle_idempotence:
            existante = Transaction.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante:
                if existante.compte_source_id == self.pk:
                    return existante
                raise ValidationError("Cette clé d'idempotence est déjà utilisée.")
        if not compte_source.est_actif() or not compte_cible.est_actif():
            raise ValidationError("Un des deux comptes est bloqué : opération impossible.")
        if compte_source.devise_id != compte_cible.devise_id:
            raise ValidationError(
                "Les deux comptes n'utilisent pas la même devise. Utilisez le module de change."
            )
        compte_source._controler_debit(total)
        compte_source.verifier_plafonds(total)

        compte_source.solde = compte_source.solde - total
        compte_cible.solde = compte_cible.solde + montant
        compte_source.save(update_fields=["solde", "date_mise_a_jour"])
        compte_cible.save(update_fields=["solde", "date_mise_a_jour"])
        self.solde = compte_source.solde
        compte_destination.solde = compte_cible.solde

        return Transaction.objects.create(
            type_transaction=type_transaction or Transaction.Type.VIREMENT,
            montant=montant,
            frais=frais,
            compte_source=compte_source,
            compte_destination=compte_cible,
            statut=Transaction.Statut.REUSSIE,
            description=description,
            categorie=categorie,
            canal=canal,
            cree_par=cree_par,
            cle_idempotence=cle_idempotence or None,
            solde_apres=compte_source.solde,
            solde_apres_destination=compte_cible.solde,
            devise=compte_source.code_devise,
        )

    @transaction.atomic
    def crediter_systeme(
        self,
        montant,
        description,
        *,
        cle_idempotence=None,
        type_transaction=None,
        reference_externe="",
        metadata=None,
    ):
        """
        Crédit système idempotent (remboursement, recharge confirmée).

        Il est autorisé même si le compte est bloqué : les fonds déjà reçus
        doivent pouvoir être restitués sans contourner le blocage des débits.
        Un compte définitivement fermé est refusé.
        """
        montant = decimal_fiable(montant)
        if montant <= 0:
            raise ValidationError("Le montant du crédit doit être positif.")
        if cle_idempotence:
            existante = Transaction.objects.filter(cle_idempotence=cle_idempotence).first()
            if existante:
                return existante
        compte = self._recharger_verrouille()
        if compte.statut == self.Statut.FERME:
            raise ValidationError("Le compte est fermé : crédit impossible, contactez la banque.")
        compte.solde = compte.solde + montant
        compte.save(update_fields=["solde", "date_mise_a_jour"])
        self.solde = compte.solde
        return Transaction.objects.create(
            type_transaction=type_transaction or Transaction.Type.REMBOURSEMENT,
            montant=montant,
            compte_destination=compte,
            statut=Transaction.Statut.REUSSIE,
            description=description,
            canal=Transaction.Canal.SYSTEME,
            cle_idempotence=cle_idempotence or None,
            reference_externe=reference_externe,
            solde_apres_destination=compte.solde,
            devise=compte.code_devise,
            metadata=metadata or {},
        )


class Transaction(models.Model):
    class Type(models.TextChoices):
        DEPOT = "DEPOT", "Dépôt"
        RETRAIT = "RETRAIT", "Retrait"
        VIREMENT = "VIREMENT", "Virement"
        PAIEMENT_MOBILE = "PAIEMENT_MOBILE", "Paiement mobile"
        PAIEMENT_FACTURE = "PAIEMENT_FACTURE", "Paiement de facture"
        PAIEMENT_MARCHAND = "PAIEMENT_MARCHAND", "Paiement marchand"
        CONVERSION = "CONVERSION", "Conversion de devises"
        REMBOURSEMENT = "REMBOURSEMENT", "Remboursement"
        FRAIS = "FRAIS", "Frais bancaires"
        INTERETS = "INTERETS", "Intérêts"
        AJUSTEMENT = "AJUSTEMENT", "Ajustement"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        REUSSIE = "REUSSIE", "Réussie"
        ECHOUEE = "ECHOUEE", "Échouée"
        ANNULEE = "ANNULEE", "Annulée"
        REMBOURSEE = "REMBOURSEE", "Remboursée"

    class Categorie(models.TextChoices):
        ALIMENTATION = "ALIMENTATION", "Alimentation"
        TRANSPORT = "TRANSPORT", "Transport"
        LOGEMENT = "LOGEMENT", "Logement"
        FACTURES = "FACTURES", "Factures et charges"
        SANTE = "SANTE", "Santé"
        EDUCATION = "EDUCATION", "Éducation"
        LOISIRS = "LOISIRS", "Loisirs"
        SALAIRE = "SALAIRE", "Salaire / revenu"
        EPARGNE = "EPARGNE", "Épargne"
        AUTRE = "AUTRE", "Autre"

    class Canal(models.TextChoices):
        APPLICATION = "APPLICATION", "Application web"
        AGENCE = "AGENCE", "Agence"
        MOBILE = "MOBILE", "Mobile money"
        MARCHAND = "MARCHAND", "Marchand"
        USSD = "USSD", "USSD"
        SYSTEME = "SYSTEME", "Système"

    type_transaction = models.CharField(max_length=30, choices=Type.choices)
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    frais = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    compte_source = models.ForeignKey(
        CompteBancaire, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions_sortantes"
    )
    compte_destination = models.ForeignKey(
        CompteBancaire, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions_entrantes"
    )
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    description = models.CharField(max_length=255, blank=True)
    categorie = models.CharField(max_length=20, choices=Categorie.choices, blank=True)
    canal = models.CharField(max_length=20, choices=Canal.choices, default=Canal.APPLICATION)
    reference = models.CharField(
        max_length=40, unique=True, db_index=True, default=generer_reference_transaction
    )
    reference_externe = models.CharField(max_length=80, blank=True)
    cle_idempotence = models.CharField(max_length=80, unique=True, null=True, blank=True)
    operation_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)

    # Traçabilité des soldes et opérations multi-devises.
    devise = models.CharField(max_length=3, blank=True, help_text="Code devise au moment de l'opération")
    taux_change = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    contre_valeur = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    solde_apres = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    solde_apres_destination = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    # Dépôt/retrait pour le compte d'un tiers (opération au guichet).
    tiers_nom = models.CharField(max_length=150, blank=True)
    tiers_telephone = models.CharField(max_length=20, blank=True)

    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_creees",
    )
    date_transaction = models.DateTimeField(default=timezone.now, db_index=True)
    date_valeur = models.DateField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-date_transaction"]
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        indexes = [
            models.Index(fields=["compte_source", "date_transaction"], name="tx_source_date_idx"),
            models.Index(fields=["compte_destination", "date_transaction"], name="tx_dest_date_idx"),
            models.Index(fields=["statut", "date_transaction"], name="tx_statut_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(montant__gte=0), name="transaction_montant_positif"
            ),
            models.CheckConstraint(
                condition=models.Q(frais__gte=0), name="transaction_frais_positifs"
            ),
        ]

    def __str__(self):
        return f"{self.get_type_transaction_display()} - {self.montant} {self.devise or 'XAF'} ({self.date_transaction:%d/%m/%Y})"

    @property
    def montant_total_debite(self):
        return (self.montant or Decimal("0")) + (self.frais or Decimal("0"))

    def sens_pour(self, compte):
        if self.compte_destination_id == compte.id:
            return "CREDIT"
        if self.compte_source_id == compte.id:
            return "DEBIT"
        return "NEUTRE"

    def libelle_devise(self):
        if self.devise == "XAF":
            return "FCFA"
        return self.devise or "XAF"

    @property
    def est_debitrice(self):
        return self.compte_source_id is not None


class RegleFrais(models.Model):
    """Grille tarifaire administrable. Aucun frais n'est appliqué par défaut."""

    class Operation(models.TextChoices):
        RETRAIT = "RETRAIT", "Retrait"
        VIREMENT = "VIREMENT", "Virement"
        PAIEMENT_MOBILE = "PAIEMENT_MOBILE", "Paiement mobile"
        PAIEMENT_FACTURE = "PAIEMENT_FACTURE", "Paiement de facture"
        PAIEMENT_MARCHAND = "PAIEMENT_MARCHAND", "Paiement marchand"
        OPERATION_AGENT = "OPERATION_AGENT", "Opération agent"
        CONVERSION = "CONVERSION", "Conversion de devises"

    type_operation = models.CharField(max_length=30, choices=Operation.choices, unique=True)
    libelle = models.CharField(max_length=120)
    frais_fixe = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    taux_pourcentage = models.DecimalField(
        max_digits=6, decimal_places=3, default=0, help_text="Pourcentage du montant, ex. 1.500 = 1,5 %"
    )
    frais_minimum = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    frais_maximum = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, help_text="Laisser vide pour ne pas plafonner."
    )
    seuil_minimum = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, help_text="Montant à partir duquel la règle s'applique."
    )
    actif = models.BooleanField(default=True)
    date_debut = models.DateField(null=True, blank=True)
    date_fin = models.DateField(null=True, blank=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["type_operation"]
        verbose_name = "Règle de frais"
        verbose_name_plural = "Règles de frais"

    def __str__(self):
        return f"{self.get_type_operation_display()} — {self.libelle}"

    def est_applicable(self, montant):
        aujourdhui = timezone.localdate()
        if not self.actif:
            return False
        if self.date_debut and self.date_debut > aujourdhui:
            return False
        if self.date_fin and self.date_fin < aujourdhui:
            return False
        return Decimal(montant) >= self.seuil_minimum

    def calculer(self, montant):
        if not self.est_applicable(montant):
            return Decimal("0.00")
        montant = Decimal(montant)
        frais = self.frais_fixe + (montant * self.taux_pourcentage / Decimal("100"))
        frais = max(frais, self.frais_minimum)
        if self.frais_maximum is not None:
            frais = min(frais, self.frais_maximum)
        return frais.quantize(CENTIME, rounding=ROUND_HALF_UP)


class VirementProgramme(models.Model):
    class Frequence(models.TextChoices):
        UNE_FOIS = "UNE_FOIS", "Une seule fois"
        QUOTIDIEN = "QUOTIDIEN", "Chaque jour"
        HEBDOMADAIRE = "HEBDOMADAIRE", "Chaque semaine"
        MENSUEL = "MENSUEL", "Chaque mois"

    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        PAUSE = "PAUSE", "En pause"
        TERMINE = "TERMINE", "Terminé"
        ANNULE = "ANNULE", "Annulé"

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="virements_programmes"
    )
    compte_source = models.ForeignKey(
        CompteBancaire, on_delete=models.CASCADE, related_name="virements_programmes_sortants"
    )
    compte_destination = models.ForeignKey(
        CompteBancaire,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="virements_programmes_entrants",
    )
    numero_destination = models.CharField(max_length=30)
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    frequence = models.CharField(max_length=20, choices=Frequence.choices, default=Frequence.MENSUEL)
    date_debut = models.DateField(default=timezone.localdate)
    date_fin = models.DateField(null=True, blank=True)
    prochaine_execution = models.DateField(default=timezone.localdate, db_index=True)
    derniere_execution = models.DateTimeField(null=True, blank=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.ACTIF)
    nombre_executions = models.PositiveIntegerField(default=0)
    derniere_erreur = models.CharField(max_length=255, blank=True)
    cle_idempotence = models.CharField(max_length=80, unique=True, null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prochaine_execution", "id"]
        verbose_name = "Virement programmé"
        verbose_name_plural = "Virements programmés"

    def __str__(self):
        return f"{self.montant} {self.compte_source.code_devise} vers {self.numero_destination} ({self.get_frequence_display()})"

    def avancer_echeance(self):
        """Calcule la prochaine date d'exécution après un succès."""
        from datetime import date

        base = self.prochaine_execution or timezone.localdate()
        if self.frequence == self.Frequence.QUOTIDIEN:
            suivante = base + timedelta(days=1)
        elif self.frequence == self.Frequence.HEBDOMADAIRE:
            suivante = base + timedelta(days=7)
        elif self.frequence == self.Frequence.MENSUEL:
            mois = base.month + 1
            annee = base.year + (1 if mois > 12 else 0)
            mois = 1 if mois > 12 else mois
            import calendar

            jour = min(base.day, calendar.monthrange(annee, mois)[1])
            suivante = date(annee, mois, jour)
        else:
            self.statut = self.Statut.TERMINE
            self.prochaine_execution = base
            return
        if self.date_fin and suivante > self.date_fin:
            self.statut = self.Statut.TERMINE
        self.prochaine_execution = suivante


class ConversionDevise(models.Model):
    """Trace une conversion entre deux comptes de devises différentes."""

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="conversions"
    )
    compte_source = models.ForeignKey(
        CompteBancaire, on_delete=models.PROTECT, related_name="conversions_sortantes"
    )
    compte_destination = models.ForeignKey(
        CompteBancaire, on_delete=models.PROTECT, related_name="conversions_entrantes"
    )
    montant_source = models.DecimalField(max_digits=14, decimal_places=2)
    montant_destination = models.DecimalField(max_digits=14, decimal_places=2)
    taux = models.DecimalField(max_digits=18, decimal_places=6)
    source_taux = models.CharField(max_length=120, blank=True)
    frais = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=40, unique=True)
    operation_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    transaction_source = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="conversions_source"
    )
    transaction_destination = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="conversions_destination"
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Conversion de devises"
        verbose_name_plural = "Conversions de devises"

    def __str__(self):
        return (
            f"{self.montant_source} {self.compte_source.code_devise} → "
            f"{self.montant_destination} {self.compte_destination.code_devise}"
        )
