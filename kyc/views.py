"""
Parcours KYC côté client : constitution du dossier, dépôt de justificatifs
privés, soumission, suivi et téléchargement contrôlé.

Règles :
- les fichiers sont stockés hors MEDIA_ROOT, dans PRIVATE_STORAGE_ROOT ;
- un client ne peut lire que ses propres documents ;
- les agents/administrateurs peuvent lire les documents pour instruction ;
- toute soumission/décision est historisée, notifiée et auditée.
"""

import mimetypes
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from comptes.models import JournalAudit
from comptes.services_notifications import envoyer_sms_simule, notifier, notifier_personnel

from .forms import FormulaireDocumentKYC, FormulaireDossierKYC
from .models import DocumentKYC, DossierKYC, HistoriqueKYC


def _dossier_client(utilisateur):
    dossier, _ = DossierKYC.objects.get_or_create(utilisateur=utilisateur)
    return dossier


def _peut_consulter_document(utilisateur, document):
    if document.dossier.utilisateur_id == utilisateur.id:
        return True
    return utilisateur.est_agent() or utilisateur.est_administrateur()


@login_required
def mon_dossier(request):
    dossier = _dossier_client(request.user)
    formulaire_dossier = FormulaireDossierKYC(instance=dossier)
    formulaire_document = FormulaireDocumentKYC()
    return render(
        request,
        "kyc/dossier.html",
        {
            "dossier": dossier,
            "formulaire_dossier": formulaire_dossier,
            "formulaire_document": formulaire_document,
            "documents": dossier.documents.filter(est_actif=True),
            "historique": dossier.historique.select_related("acteur")[:20],
        },
    )


@login_required
@require_POST
def enregistrer_informations(request):
    dossier = _dossier_client(request.user)
    if dossier.statut == DossierKYC.Statut.APPROUVE:
        messages.info(request, "Votre dossier est déjà approuvé ; ces informations ne sont plus modifiables.")
        return redirect("kyc:mon_dossier")

    formulaire = FormulaireDossierKYC(request.POST, instance=dossier)
    if formulaire.is_valid():
        formulaire.save()
        JournalAudit.journaliser(request.user, "KYC — informations enregistrées", adresse_ip=getattr(request, "adresse_ip_client", None))
        messages.success(request, "Vos informations ont été enregistrées. Ajoutez vos justificatifs puis soumettez le dossier.")
    else:
        messages.error(request, "Certaines informations sont invalides. Merci de corriger le formulaire.")
    return redirect("kyc:mon_dossier")


@login_required
@require_POST
def ajouter_document(request):
    dossier = _dossier_client(request.user)
    if dossier.statut == DossierKYC.Statut.APPROUVE:
        messages.info(request, "Votre dossier est approuvé : l'ajout de documents est désactivé.")
        return redirect("kyc:mon_dossier")

    formulaire = FormulaireDocumentKYC(request.POST, request.FILES)
    if formulaire.is_valid():
        document = formulaire.save(commit=False)
        document.dossier = dossier
        document.save()
        HistoriqueKYC.objects.create(
            dossier=dossier,
            acteur=request.user,
            action=HistoriqueKYC.Action.DOCUMENT_AJOUTE,
            ancien_statut=dossier.statut,
            nouveau_statut=dossier.statut,
            commentaire=f"{document.get_type_document_display()} déposé.",
        )
        JournalAudit.journaliser(
            request.user,
            "KYC — document ajouté",
            f"{document.get_type_document_display()} (SHA-256 : {document.empreinte_sha256[:12]}…)",
            getattr(request, "adresse_ip_client", None),
        )
        messages.success(request, "Document ajouté. Il sera examiné avec votre dossier.")
    else:
        for erreurs in formulaire.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
    return redirect("kyc:mon_dossier")


@login_required
@require_POST
def supprimer_document(request, document_id):
    document = get_object_or_404(DocumentKYC, id=document_id, dossier__utilisateur=request.user)
    if document.dossier.statut == DossierKYC.Statut.APPROUVE:
        messages.info(request, "Un dossier approuvé ne peut plus être modifié.")
        return redirect("kyc:mon_dossier")
    document.est_actif = False
    document.save(update_fields=["est_actif"])
    dossier = document.dossier
    if not dossier.documents_obligatoires_presents() and dossier.statut == DossierKYC.Statut.EN_ATTENTE:
        dossier.statut = DossierKYC.Statut.NON_SOUMIS
        dossier.save(update_fields=["statut", "date_mise_a_jour"])
    HistoriqueKYC.objects.create(
        dossier=dossier,
        acteur=request.user,
        action=HistoriqueKYC.Action.DOCUMENT_AJOUTE,
        ancien_statut=dossier.statut,
        nouveau_statut=dossier.statut,
        commentaire=f"{document.get_type_document_display()} retiré.",
    )
    messages.success(request, "Document retiré.")
    return redirect("kyc:mon_dossier")


@login_required
@require_POST
def soumettre_dossier(request):
    dossier = _dossier_client(request.user)
    if dossier.statut == DossierKYC.Statut.APPROUVE:
        messages.info(request, "Votre dossier est déjà approuvé.")
        return redirect("kyc:mon_dossier")
    if not dossier.type_piece or not dossier.numero_piece:
        messages.error(request, "Renseignez d'abord le type et le numéro de votre pièce d'identité.")
        return redirect("kyc:mon_dossier")
    try:
        dossier.soumettre(acteur=request.user)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect("kyc:mon_dossier")

    notifier(
        request.user,
        "Dossier d'identité soumis",
        "Votre dossier est en attente de vérification. Vous serez notifié de la décision.",
        type_notification="KYC",
        niveau="INFO",
        lien="/kyc/",
    )
    envoyer_sms_simule(request.user, "Ma Banque : votre dossier d'identité a été soumis pour vérification.")
    notifier_personnel(
        "Nouveau dossier KYC à instruire",
        f"Le client {request.user.get_full_name() or request.user.username} a soumis son dossier d'identité.",
        lien="/administration/kyc/",
        roles=("ADMIN", "AGENT"),
    )
    JournalAudit.journaliser(request.user, "KYC — dossier soumis", adresse_ip=getattr(request, "adresse_ip_client", None))
    messages.success(request, "Votre dossier a été soumis. Un agent va le vérifier.")
    return redirect("kyc:mon_dossier")


@login_required
def telecharger_document(request, document_id):
    document = get_object_or_404(DocumentKYC.objects.select_related("dossier"), id=document_id)
    if not _peut_consulter_document(request.user, document):
        raise Http404("Document introuvable.")
    if not document.fichier or not document.fichier.storage.exists(document.fichier.name):
        raise Http404("Fichier introuvable.")
    extension = os.path.splitext(document.fichier.name)[1].lower()
    type_mime = mimetypes.types_map.get(extension, "application/octet-stream")
    JournalAudit.journaliser(
        request.user,
        "KYC — consultation document",
        f"Document #{document.pk} du dossier de {document.dossier.utilisateur}",
        getattr(request, "adresse_ip_client", None),
    )
    return FileResponse(
        document.fichier.open("rb"),
        as_attachment=True,
        filename=f"kyc_{document.type_document.lower()}{extension}",
        content_type=type_mime,
    )
