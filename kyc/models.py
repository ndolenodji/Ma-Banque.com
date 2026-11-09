"""
Dossier de connaissance client (KYC) : statuts, justificatifs privés et
historique des décisions. Les fichiers ne sont accessibles que par des vues
contrôlées (propriétaire ou personnel habilité).
"""

import hashlib
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .storage import stockage_prive_kyc


def chemin_document(instance, nom_fichier):
    """Chemin privé : kyc/<utilisateur>/<horodatage>-<nom nettoyé>."""
    extension = os.path.splitext(nom_fichier)[1].lower()
    return f"utilisateur_{instance.dossier.utilisateur_id}/{timezone.now():%Y%m%d%H%M%S}_{instance.type_document}{extension}"


class DossierKYC(models.Model):
    class Statut(models.TextChoices):
        NON_SOUMIS = "NON_SOUMIS", "Non soumis"
        EN_ATTENTE = "EN_ATTENTE", "En attente de vérification"
        APPROUVE = "APPROUVE", "Approuvé"
        REJETE = "REJETE", "Rejeté"

    class TypePiece(models.TextChoices):
        CNI = "CNI", "Carte nationale d'identité"
        PASSEPORT = "PASSEPORT", "Passeport"
        PERMIS = "PERMIS", "Permis de conduire"
        CARTE_SEJOUR = "CARTE_SEJOUR", "Carte de séjour"
        AUTRE = "AUTRE", "Autre document officiel"

    utilisateur = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dossier_kyc"
    )
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.NON_SOUMIS)
    type_piece = models.CharField(max_length=20, choices=TypePiece.choices, blank=True)
    numero_piece = models.CharField(max_length=60, blank=True)
    date_naissance = models.DateField(null=True, blank=True)
    nationalite = models.CharField(max_length=60, blank=True, default="Tchadienne")
    adresse = models.CharField(max_length=255, blank=True)
    profession = models.CharField(max_length=120, blank=True)
    motif_rejet = models.TextField(blank=True)
    date_soumission = models.DateTimeField(null=True, blank=True)
    date_traitement = models.DateTimeField(null=True, blank=True)
    traite_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dossiers_kyc_traites",
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_soumission", "-date_creation"]
        verbose_name = "Dossier KYC"
        verbose_name_plural = "Dossiers KYC"

    def __str__(self):
        return f"KYC {self.utilisateur} — {self.get_statut_display()}"

    def documents_obligatoires_presents(self):
        types = set(self.documents.filter(est_actif=True).values_list("type_document", flat=True))
        return DocumentKYC.TypeDocument.CNI_RECTO in types and DocumentKYC.TypeDocument.CNI_VERSO in types

    def soumettre(self, acteur=None):
        if not self.documents_obligatoires_presents():
            raise ValidationError("Les deux faces de la pièce d'identité sont obligatoires.")
        ancien_statut = self.statut
        self.statut = self.Statut.EN_ATTENTE
        self.date_soumission = timezone.now()
        self.motif_rejet = ""
        self.save(update_fields=["statut", "date_soumission", "motif_rejet", "date_mise_a_jour"])
        HistoriqueKYC.objects.create(
            dossier=self,
            acteur=acteur or self.utilisateur,
            action="SOUMISSION",
            ancien_statut=ancien_statut,
            nouveau_statut=self.Statut.EN_ATTENTE,
            commentaire="Dossier soumis pour vérification.",
        )


class DocumentKYC(models.Model):
    class TypeDocument(models.TextChoices):
        CNI_RECTO = "CNI_RECTO", "Pièce d'identité — recto"
        CNI_VERSO = "CNI_VERSO", "Pièce d'identité — verso"
        JUSTIFICATIF_DOMICILE = "JUSTIFICATIF_DOMICILE", "Justificatif de domicile"
        SELFIE = "SELFIE", "Photo du titulaire"
        AUTRE = "AUTRE", "Autre justificatif"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        VALIDE = "VALIDE", "Validé"
        REJETE = "REJETE", "Rejeté"

    dossier = models.ForeignKey(DossierKYC, on_delete=models.CASCADE, related_name="documents")
    type_document = models.CharField(max_length=30, choices=TypeDocument.choices)
    fichier = models.FileField(storage=stockage_prive_kyc, upload_to=chemin_document)
    empreinte_sha256 = models.CharField(max_length=64, blank=True)
    taille_octets = models.PositiveIntegerField(default=0)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    motif_rejet = models.CharField(max_length=255, blank=True)
    est_actif = models.BooleanField(default=True)
    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["type_document", "-date_ajout"]
        verbose_name = "Document KYC"
        verbose_name_plural = "Documents KYC"

    def __str__(self):
        return f"{self.get_type_document_display()} — {self.dossier.utilisateur}"

    def save(self, *args, **kwargs):
        if self.fichier and not self.empreinte_sha256:
            self.fichier.seek(0)
            empreinte = hashlib.sha256()
            for bloc in self.fichier.chunks():
                empreinte.update(bloc)
            self.empreinte_sha256 = empreinte.hexdigest()
            self.fichier.seek(0)
            self.taille_octets = self.fichier.size
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        extensions_autorisees = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
        if self.fichier:
            extension = os.path.splitext(self.fichier.name)[1].lower()
            if extension not in extensions_autorisees:
                raise ValidationError({"fichier": "Format non autorisé (JPG, PNG, WEBP ou PDF)."})
            if self.fichier.size > 5 * 1024 * 1024:
                raise ValidationError({"fichier": "Le fichier ne doit pas dépasser 5 Mo."})


class HistoriqueKYC(models.Model):
    class Action(models.TextChoices):
        SOUMISSION = "SOUMISSION", "Soumission"
        APPROBATION = "APPROBATION", "Approbation"
        REJET = "REJET", "Rejet"
        DOCUMENT_AJOUTE = "DOCUMENT_AJOUTE", "Document ajouté"
        DEMANDE_COMPLEMENT = "DEMANDE_COMPLEMENT", "Demande de complément"

    dossier = models.ForeignKey(DossierKYC, on_delete=models.CASCADE, related_name="historique")
    acteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    ancien_statut = models.CharField(max_length=20, blank=True)
    nouveau_statut = models.CharField(max_length=20, blank=True)
    commentaire = models.TextField(blank=True)
    date_action = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_action"]
        verbose_name = "Étape KYC"
        verbose_name_plural = "Historique KYC"

    def __str__(self):
        return f"{self.get_action_display()} — {self.dossier}"
