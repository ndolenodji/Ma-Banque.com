"""
Support client : base de connaissances et tickets avec fil de messages.
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def reference_ticket():
    return f"TCK-{timezone.now():%Y%m%d}-{secrets.token_hex(3).upper()}"


class ArticleFAQ(models.Model):
    class Categorie(models.TextChoices):
        COMPTE = "COMPTE", "Comptes et profil"
        TRANSFERT = "TRANSFERT", "Virements"
        MOBILE = "MOBILE", "Paiement mobile"
        CARTE = "CARTE", "Cartes"
        SECURITE = "SECURITE", "Sécurité"
        FRAIS = "FRAIS", "Frais et plafonds"

    categorie = models.CharField(max_length=20, choices=Categorie.choices)
    question = models.CharField(max_length=200)
    reponse = models.TextField()
    ordre = models.PositiveSmallIntegerField(default=0)
    publie = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["categorie", "ordre", "id"]
        verbose_name = "Article de la FAQ"
        verbose_name_plural = "FAQ"

    def __str__(self):
        return self.question


class TicketSupport(models.Model):
    class Categorie(models.TextChoices):
        COMPTE = "COMPTE", "Compte et accès"
        TRANSACTION = "TRANSACTION", "Transaction contestée"
        MOBILE = "MOBILE", "Paiement mobile"
        CARTE = "CARTE", "Carte bancaire"
        KYC = "KYC", "Vérification d'identité"
        AUTRE = "AUTRE", "Autre demande"

    class Priorite(models.TextChoices):
        BASSE = "BASSE", "Basse"
        NORMALE = "NORMALE", "Normale"
        HAUTE = "HAUTE", "Haute"
        URGENTE = "URGENTE", "Urgente"

    class Statut(models.TextChoices):
        OUVERT = "OUVERT", "Ouvert"
        EN_COURS = "EN_COURS", "En cours"
        RESOLU = "RESOLU", "Résolu"
        FERME = "FERME", "Fermé"

    reference = models.CharField(max_length=30, unique=True, default=reference_ticket)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tickets_support"
    )
    sujet = models.CharField(max_length=180)
    categorie = models.CharField(max_length=20, choices=Categorie.choices, default=Categorie.AUTRE)
    priorite = models.CharField(max_length=10, choices=Priorite.choices, default=Priorite.NORMALE)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.OUVERT)
    assigne_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets_assignes",
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)
    date_cloture = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["statut", "-date_mise_a_jour"]
        verbose_name = "Ticket support"
        verbose_name_plural = "Tickets support"

    def __str__(self):
        return f"{self.reference} — {self.sujet}"

    @property
    def est_ouvert(self):
        return self.statut in (self.Statut.OUVERT, self.Statut.EN_COURS)


class MessageSupport(models.Model):
    ticket = models.ForeignKey(TicketSupport, on_delete=models.CASCADE, related_name="messages")
    auteur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    message = models.TextField()
    est_note_interne = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date_creation"]
        verbose_name = "Message support"
        verbose_name_plural = "Messages support"

    def __str__(self):
        return f"Message de {self.auteur} sur {self.ticket.reference}"
