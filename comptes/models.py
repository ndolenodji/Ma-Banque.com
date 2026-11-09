"""
Modèles liés aux utilisateurs, à la sécurité, aux notifications et à la
traçabilité (journal d'audit chaîné, tentatives de connexion, sessions).
"""

import hashlib
import json

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


class Utilisateur(AbstractUser):
    """
    Utilisateur étendu de la plateforme.

    Trois rôles : CLIENT (par défaut à l'inscription), AGENT (agent bancaire)
    et ADMIN (administrateur). Les superutilisateurs techniques restent
    autorisés, mais les vues métier appliquent le rôle.
    """

    class Role(models.TextChoices):
        CLIENT = "CLIENT", "Client"
        AGENT = "AGENT", "Agent bancaire"
        ADMIN = "ADMIN", "Administrateur"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    telephone = models.CharField(max_length=20, unique=True, help_text="Numéro utilisé pour le paiement mobile")
    adresse = models.CharField(max_length=255, blank=True)
    photo_profil = models.ImageField(upload_to="profils/", blank=True, null=True)

    # --- Vérification d'identité (le dossier complet est géré par l'app KYC) ---
    numero_piece_identite = models.CharField(max_length=50, blank=True)
    photo_piece_identite = models.ImageField(upload_to="kyc/", blank=True, null=True)
    identite_verifiee = models.BooleanField(default=False)

    # --- Double authentification TOTP (secret chiffré, jamais exposé) ---
    double_authentification_active = models.BooleanField(default=False)
    totp_secret_chiffre = models.CharField(max_length=255, blank=True)
    date_activation_2fa = models.DateTimeField(null=True, blank=True)

    # --- Statut du compte utilisateur (indépendant des comptes bancaires) ---
    est_actif_bancaire = models.BooleanField(
        default=True, help_text="Si False, l'utilisateur ne peut plus se connecter (fraude suspectée)."
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    def est_client(self):
        return self.role == self.Role.CLIENT

    def est_agent(self):
        return self.role == self.Role.AGENT

    def est_administrateur(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


class TentativeConnexion(models.Model):
    """Historique des tentatives de connexion et protection contre le brute-force."""

    utilisateur = models.ForeignKey(
        Utilisateur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tentatives_connexion",
    )
    identifiant = models.CharField(max_length=150, db_index=True)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    appareil = models.CharField(max_length=80, blank=True)
    reussie = models.BooleanField(default=False)
    motif = models.CharField(max_length=120, blank=True)
    horodatage = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-horodatage"]
        verbose_name = "Tentative de connexion"
        verbose_name_plural = "Tentatives de connexion"
        indexes = [
            models.Index(fields=["identifiant", "reussie", "horodatage"], name="tentative_ident_date_idx"),
            models.Index(fields=["adresse_ip", "reussie", "horodatage"], name="tentative_ip_date_idx"),
        ]

    def __str__(self):
        etat = "réussie" if self.reussie else "échouée"
        return f"{self.identifiant} - {etat} - {self.horodatage:%d/%m/%Y %H:%M}"


class JournalAudit(models.Model):
    """
    Journal d'audit append-only et chaîné par empreintes SHA-256.

    Chaque entrée contient l'empreinte de la précédente : toute modification
    ou suppression d'une ligne intermédiaire rompt la chaîne et est détectée
    par ``verifier_chaine()``. Le journal ne doit jamais être modifiable dans
    l'interface d'administration.
    """

    utilisateur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name="actions_journalisees"
    )
    action = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    date_action = models.DateTimeField(default=timezone.now)
    empreinte_precedente = models.CharField(max_length=64, blank=True)
    empreinte = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["-date_action", "-id"]
        verbose_name = "Entrée de journal d'audit"
        verbose_name_plural = "Journal d'audit"
        indexes = [models.Index(fields=["date_action"], name="audit_date_idx")]

    def __str__(self):
        who = self.utilisateur.username if self.utilisateur else "anonyme"
        return f"[{self.date_action:%d/%m/%Y %H:%M}] {who} - {self.action}"

    @staticmethod
    def _calculer_empreinte(entree, empreinte_precedente):
        charge = "|".join(
            [
                empreinte_precedente or "",
                str(entree.utilisateur_id or ""),
                entree.action or "",
                entree.details or "",
                entree.adresse_ip or "",
                entree.date_action.isoformat() if entree.date_action else "",
            ]
        )
        return hashlib.sha256(charge.encode("utf-8")).hexdigest()

    @classmethod
    def journaliser(cls, utilisateur, action, details="", adresse_ip=None):
        with transaction.atomic():
            precedente = cls.objects.select_for_update().order_by("-id").first()
            empreinte_precedente = precedente.empreinte if precedente else ""
            entree = cls(
                utilisateur=utilisateur,
                action=action,
                details=details,
                adresse_ip=adresse_ip,
                date_action=timezone.now(),
                empreinte_precedente=empreinte_precedente,
            )
            entree.empreinte = cls._calculer_empreinte(entree, empreinte_precedente)
            entree.save()
            return entree

    @classmethod
    def verifier_chaine(cls):
        """Retourne (valide, nombre_entrees, première_anomalie)."""
        precedente = ""
        total = 0
        for entree in cls.objects.order_by("id").iterator():
            total += 1
            if entree.empreinte_precedente != precedente:
                return False, total, f"Chaînage rompu à l'entrée #{entree.pk}"
            attendue = cls._calculer_empreinte(entree, precedente)
            if entree.empreinte != attendue:
                return False, total, f"Empreinte invalide à l'entrée #{entree.pk}"
            precedente = entree.empreinte
        return True, total, ""


class SessionConnexion(models.Model):
    """Session active connue, pour l'écran « appareils et sessions »."""

    utilisateur = models.ForeignKey(
        Utilisateur, on_delete=models.CASCADE, related_name="sessions_connexion"
    )
    cle_session = models.CharField(max_length=64, unique=True)
    appareil = models.CharField(max_length=120, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    date_connexion = models.DateTimeField(default=timezone.now)
    derniere_activite = models.DateTimeField(default=timezone.now)
    est_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-derniere_activite"]
        verbose_name = "Session de connexion"
        verbose_name_plural = "Sessions de connexion"

    def __str__(self):
        return f"{self.utilisateur} — {self.appareil or 'appareil inconnu'}"


class CodeSecours(models.Model):
    """Code de secours à usage unique pour la double authentification."""

    utilisateur = models.ForeignKey(
        Utilisateur, on_delete=models.CASCADE, related_name="codes_secours"
    )
    empreinte = models.CharField(max_length=128)
    utilise_le = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Code de secours"
        verbose_name_plural = "Codes de secours"

    def __str__(self):
        return f"Code de secours {'utilisé' if self.utilise_le else 'actif'} — {self.utilisateur}"


class Notification(models.Model):
    class Type(models.TextChoices):
        SYSTEME = "SYSTEME", "Système"
        TRANSACTION = "TRANSACTION", "Transaction"
        SECURITE = "SECURITE", "Sécurité"
        KYC = "KYC", "Vérification d'identité"
        PAIEMENT = "PAIEMENT", "Paiement"
        EPARGNE = "EPARGNE", "Épargne"
        FACTURE = "FACTURE", "Facture"
        SUPPORT = "SUPPORT", "Support"
        FRAUDE = "FRAUDE", "Alerte fraude"

    class Niveau(models.TextChoices):
        INFO = "INFO", "Information"
        SUCCES = "SUCCES", "Succès"
        ATTENTION = "ATTENTION", "Attention"
        CRITIQUE = "CRITIQUE", "Critique"

    destinataire = models.ForeignKey(
        Utilisateur, on_delete=models.CASCADE, related_name="notifications"
    )
    type_notification = models.CharField(max_length=20, choices=Type.choices, default=Type.SYSTEME)
    niveau = models.CharField(max_length=10, choices=Niveau.choices, default=Niveau.INFO)
    titre = models.CharField(max_length=160)
    message = models.TextField()
    lien = models.CharField(max_length=255, blank=True)
    lue = models.BooleanField(default=False)
    date_creation = models.DateTimeField(default=timezone.now)
    date_lecture = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        indexes = [
            models.Index(fields=["destinataire", "lue", "date_creation"], name="notif_dest_lue_date_idx"),
        ]

    def __str__(self):
        return f"{self.titre} → {self.destinataire}"


class SmsSimule(models.Model):
    """Journal des SMS simulés (aucune passerelle SMS réelle n'est connectée)."""

    destinataire = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_simules"
    )
    numero_telephone = models.CharField(max_length=20)
    message = models.TextField()
    date_creation = models.DateTimeField(auto_now_add=True)
    statut = models.CharField(max_length=20, default="SIMULE")

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "SMS simulé"
        verbose_name_plural = "SMS simulés"

    def __str__(self):
        return f"SMS simulé vers {self.numero_telephone} — {self.date_creation:%d/%m/%Y %H:%M}"


class Beneficiaire(models.Model):
    """Bénéficiaire favori : compte interne ou portefeuille mobile money."""

    class Type(models.TextChoices):
        BANCAIRE = "BANCAIRE", "Compte bancaire interne"
        MOBILE_MONEY = "MOBILE_MONEY", "Portefeuille mobile money"

    proprietaire = models.ForeignKey(
        Utilisateur, on_delete=models.CASCADE, related_name="beneficiaires"
    )
    nom_complet = models.CharField(max_length=150)
    type_beneficiaire = models.CharField(max_length=20, choices=Type.choices, default=Type.BANCAIRE)
    numero_compte = models.CharField(max_length=30, help_text="Numéro de compte ou numéro mobile money")
    operateur = models.CharField(max_length=10, blank=True)
    surnom = models.CharField(max_length=50, blank=True, help_text="Surnom pratique, ex : « Maman »")
    est_favori = models.BooleanField(default=True)
    date_ajout = models.DateTimeField(auto_now_add=True)
    date_derniere_utilisation = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-est_favori", "nom_complet"]
        unique_together = ("proprietaire", "numero_compte")
        verbose_name = "Bénéficiaire"
        verbose_name_plural = "Bénéficiaires"

    def __str__(self):
        return f"{self.nom_complet} ({self.numero_compte})"

    def clean(self):
        super().clean()
        if self.type_beneficiaire == self.Type.MOBILE_MONEY:
            numero = (self.numero_compte or "").replace(" ", "")
            if not numero.isdigit() or len(numero) < 8:
                raise ValidationError({"numero_compte": "Numéro mobile money invalide."})
            if not self.operateur:
                raise ValidationError({"operateur": "Sélectionnez un opérateur mobile money."})
        if self.proprietaire_id and self.type_beneficiaire == self.Type.BANCAIRE:
            from transactions.models import CompteBancaire

            if CompteBancaire.objects.filter(
                proprietaire_id=self.proprietaire_id, numero_compte=self.numero_compte
            ).exists():
                raise ValidationError({"numero_compte": "Vous ne pouvez pas ajouter votre propre compte."})
