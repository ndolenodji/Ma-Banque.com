"""
Vues des opérations bancaires : ouverture de comptes, dépôt, retrait,
virement immédiat ou programmé, historique, reçus et relevés (PDF, Excel,
CSV).

Les mouvements d'argent restent exclusivement réalisés par les méthodes
verrouillées de ``CompteBancaire`` ; les vues orchestrent les contrôles, les
traces et les documents.
"""

import csv
import io
import logging
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from comptes.models import JournalAudit
from comptes.services_notifications import notifier, notifier_personnel

from . import services
from .forms import (
    FormulaireDepot,
    FormulaireFiltreHistorique,
    FormulaireFiltreReleve,
    FormulaireOuvertureCompte,
    FormulaireRetrait,
    FormulaireVirement,
    FormulaireVirementProgramme,
)
from .models import CompteBancaire, RegleFrais, Transaction, VirementProgramme, decimal_fiable

logger = logging.getLogger(__name__)

LIBELLE_DEVISE = {"XAF": "FCFA"}


# ---------------------------------------------------------------------------
# Helpers généraux
# ---------------------------------------------------------------------------
def _message_erreur(erreur):
    """Extrait un message lisible d'une ValidationError Django."""
    messages_erreur = getattr(erreur, "messages", None)
    if messages_erreur:
        return " ".join(messages_erreur)
    return getattr(erreur, "message", str(erreur))


def _notifier_sans_echec(*args, **kwargs):
    """Une notification ne doit jamais invalider une opération déjà enregistrée."""
    try:
        return notifier(*args, **kwargs)
    except Exception:  # noqa: BLE001 - traçabilité applicative, pas de perte financière
        logger.exception("Notification impossible après une opération bancaire.")
        return None


def _notifier_personnel_sans_echec(*args, **kwargs):
    try:
        return notifier_personnel(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("Notification du personnel impossible.")
        return None


def _arrondi_sans_echec(utilisateur, compte, montant, operation):
    """Déclenche l'arrondi épargne sans jamais compromettre l'opération principale."""
    try:
        return services.appliquer_arrondi(utilisateur, compte, montant, operation)
    except Exception:  # noqa: BLE001
        logger.exception("Arrondi épargne impossible après une opération.")
        return None


def _regles_frais(*operations):
    """Grille tarifaire réellement configurée, affichée sur les formulaires."""
    return RegleFrais.objects.filter(type_operation__in=operations, actif=True).order_by("type_operation")


def _transaction_existante(cle, compte):
    if not cle:
        return None
    return Transaction.objects.filter(
        Q(compte_source=compte) | Q(compte_destination=compte), cle_idempotence=str(cle)
    ).first()


def _cle_saisie(formulaire):
    return str(formulaire.cleaned_data.get("cle_idempotence") or uuid.uuid4())


def _lien_compte(compte):
    return reverse("transactions:detail_compte", kwargs={"compte_id": compte.id})


# ---------------------------------------------------------------------------
# Comptes
# ---------------------------------------------------------------------------
@login_required
def liste_comptes(request):
    comptes = request.user.comptes_bancaires.select_related("devise")
    return render(request, "transactions/liste_comptes.html", {"comptes": comptes})


@login_required
def ouvrir_compte(request):
    """Ouverture d'un compte supplémentaire, réservée aux clients vérifiés KYC."""
    from kyc.models import DossierKYC

    dossier = getattr(request.user, "dossier_kyc", None)
    verifie = request.user.identite_verifiee or (
        dossier is not None and dossier.statut == DossierKYC.Statut.APPROUVE
    )
    if not verifie:
        messages.warning(
            request,
            "Votre identité doit être vérifiée avant l'ouverture d'un nouveau compte.",
        )
        return redirect("kyc:mon_dossier")

    if request.method == "POST":
        formulaire = FormulaireOuvertureCompte(request.POST)
        if formulaire.is_valid():
            compte = CompteBancaire.objects.create(
                proprietaire=request.user,
                type_compte=formulaire.cleaned_data["type_compte"],
                devise=formulaire.cleaned_data["devise"],
            )
            JournalAudit.journaliser(
                request.user,
                "Ouverture de compte",
                f"{compte.get_type_compte_display()} n° {compte.numero_compte} ({compte.code_devise})",
                getattr(request, "adresse_ip_client", None),
            )
            _notifier_sans_echec(
                request.user,
                "Nouveau compte ouvert",
                f"Votre {compte.get_type_compte_display().lower()} n° {compte.numero_compte} "
                f"en {compte.code_devise} est actif. Solde initial : 0,00.",
                type_notification="SYSTEME",
                niveau="SUCCES",
                lien=_lien_compte(compte),
            )
            _notifier_personnel_sans_echec(
                "Nouveau compte client",
                f"{request.user.get_full_name() or request.user.username} a ouvert le compte "
                f"n° {compte.numero_compte} ({compte.code_devise}).",
                niveau="INFO",
                lien=f"/administration/clients/",
                roles=("ADMIN", "AGENT"),
            )
            messages.success(request, f"Votre compte n° {compte.numero_compte} a été ouvert.")
            return redirect("transactions:detail_compte", compte_id=compte.id)
    else:
        formulaire = FormulaireOuvertureCompte()

    return render(
        request,
        "transactions/ouvrir_compte.html",
        {
            "formulaire": formulaire,
            "dossier": dossier,
            "plafond_journalier": settings.PLAFOND_JOURNALIER_DEFAUT,
            "plafond_hebdomadaire": settings.PLAFOND_HEBDOMADAIRE_DEFAUT,
            "plafond_mensuel": settings.PLAFOND_MENSUEL_DEFAUT,
        },
    )


@login_required
def depots_retraits(request):
    """Page « hub » regroupant dépôt et retrait."""
    return render(request, "transactions/depots_retraits.html")


@login_required
def releves(request):
    """Page dédiée aux relevés de compte (PDF, Excel, CSV) sur période choisie."""
    comptes = request.user.comptes_bancaires.select_related("devise")
    formulaire = FormulaireFiltreReleve(request.GET or None, utilisateur=request.user)
    return render(
        request,
        "transactions/releves.html",
        {"comptes": comptes, "formulaire": formulaire},
    )


@login_required
def detail_compte(request, compte_id):
    compte = get_object_or_404(
        CompteBancaire.objects.select_related("devise", "proprietaire"),
        id=compte_id,
        proprietaire=request.user,
    )
    transactions = (
        Transaction.objects.filter(compte_source=compte)
        | Transaction.objects.filter(compte_destination=compte)
    ).select_related("compte_source", "compte_destination").distinct().order_by(
        "-date_transaction", "-id"
    )[:100]
    return render(
        request,
        "transactions/detail_compte.html",
        {"compte": compte, "transactions": transactions},
    )


# ---------------------------------------------------------------------------
# Opérations courantes
# ---------------------------------------------------------------------------
@login_required
def deposer(request):
    if request.method == "POST":
        formulaire = FormulaireDepot(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            compte = formulaire.cleaned_data["compte"]
            montant = formulaire.cleaned_data["montant"]
            cle = _cle_saisie(formulaire)
            existante = _transaction_existante(cle, compte)
            if existante:
                if existante.montant != montant:
                    messages.error(
                        request,
                        "Cette soumission a déjà été traitée avec un autre montant. "
                        "Rechargez le formulaire pour effectuer une nouvelle opération.",
                    )
                else:
                    messages.info(
                        request,
                        f"Cette opération a déjà été enregistrée (référence {existante.reference}).",
                    )
                return redirect("transactions:detail_compte", compte_id=compte.id)

            tiers_nom = formulaire.cleaned_data.get("tiers_nom", "")
            tiers_telephone = formulaire.cleaned_data.get("tiers_telephone", "")
            description = "Dépôt d'argent"
            if tiers_nom:
                description = f"Dépôt effectué par {tiers_nom}"
            try:
                operation = compte.deposer(
                    montant,
                    description=description,
                    canal=Transaction.Canal.APPLICATION,
                    cree_par=request.user,
                    cle_idempotence=cle,
                    tiers_nom=tiers_nom,
                    tiers_telephone=tiers_telephone,
                )
            except ValidationError as erreur:
                messages.error(request, _message_erreur(erreur))
            else:
                JournalAudit.journaliser(
                    request.user,
                    "Dépôt",
                    f"{montant} {compte.code_devise} sur {compte.numero_compte} — réf {operation.reference}",
                )
                _notifier_sans_echec(
                    request.user,
                    "Dépôt enregistré",
                    f"{montant} {compte.code_devise} crédités sur le compte n° {compte.numero_compte}. "
                    f"Référence : {operation.reference}.",
                    type_notification="TRANSACTION",
                    niveau="SUCCES",
                    lien=_lien_compte(compte),
                )
                messages.success(
                    request,
                    "Votre virement ou votre transaction a été effectué(e) avec succès.",
                )
                messages.info(
                    request,
                    f"Dépôt de {montant} {compte.code_devise} enregistré — "
                    f"référence {operation.reference}. Nouveau solde : "
                    f"{compte.solde} {compte.code_devise}.",
                )
                return redirect("transactions:detail_compte", compte_id=compte.id)
    else:
        formulaire = FormulaireDepot(utilisateur=request.user)

    return render(
        request,
        "transactions/deposer.html",
        {"formulaire": formulaire},
    )


@login_required
def retirer(request):
    if request.method == "POST":
        formulaire = FormulaireRetrait(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            compte = formulaire.cleaned_data["compte"]
            montant = formulaire.cleaned_data["montant"]
            cle = _cle_saisie(formulaire)
            existante = _transaction_existante(cle, compte)
            if existante:
                if existante.montant != montant:
                    messages.error(
                        request,
                        "Cette soumission a déjà été traitée avec un autre montant. "
                        "Rechargez le formulaire pour effectuer une nouvelle opération.",
                    )
                else:
                    messages.info(
                        request,
                        f"Cette opération a déjà été enregistrée (référence {existante.reference}).",
                    )
                return redirect("transactions:detail_compte", compte_id=compte.id)

            try:
                frais = services.calculer_frais(RegleFrais.Operation.RETRAIT, montant)
                operation = compte.retirer(
                    montant,
                    description="Retrait d'argent via l'application",
                    frais=frais,
                    categorie=formulaire.cleaned_data.get("categorie", ""),
                    canal=Transaction.Canal.APPLICATION,
                    cree_par=request.user,
                    cle_idempotence=cle,
                )
            except ValidationError as erreur:
                messages.error(request, _message_erreur(erreur))
            else:
                arrondi = _arrondi_sans_echec(request.user, compte, montant, operation)
                JournalAudit.journaliser(
                    request.user,
                    "Retrait",
                    f"{montant} {compte.code_devise} (frais {frais}) sur {compte.numero_compte} "
                    f"— réf {operation.reference}",
                )
                _notifier_sans_echec(
                    request.user,
                    "Retrait enregistré",
                    f"{montant} {compte.code_devise} retirés du compte n° {compte.numero_compte}. "
                    f"Frais : {frais} {compte.code_devise}. Référence : {operation.reference}.",
                    type_notification="TRANSACTION",
                    niveau="SUCCES",
                    lien=_lien_compte(compte),
                )
                complement_frais = f" (frais : {frais} {compte.code_devise})" if frais else ""
                if arrondi is not None:
                    messages.info(
                        request,
                        f"Arrondi épargne : {arrondi.montant} {compte.code_devise} transférés "
                        "vers votre compte d'épargne.",
                    )
                messages.success(
                    request,
                    "Votre virement ou votre transaction a été effectué(e) avec succès.",
                )
                messages.info(
                    request,
                    f"Retrait de {montant} {compte.code_devise} enregistré"
                    f"{complement_frais} — référence {operation.reference}. "
                    f"Nouveau solde : {compte.solde} {compte.code_devise}.",
                )
                return redirect("transactions:detail_compte", compte_id=compte.id)
    else:
        formulaire = FormulaireRetrait(utilisateur=request.user)

    return render(
        request,
        "transactions/retirer.html",
        {"formulaire": formulaire, "regles_frais": _regles_frais(RegleFrais.Operation.RETRAIT)},
    )


@login_required
def virement(request):
    beneficiaires = request.user.beneficiaires.filter(
        type_beneficiaire="BANCAIRE"
    ).order_by("nom_complet")
    if request.method == "POST":
        formulaire = FormulaireVirement(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            compte_source = formulaire.cleaned_data["compte_source"]
            numero_destination = formulaire.cleaned_data["numero_compte_destination"]
            montant = formulaire.cleaned_data["montant"]
            description = formulaire.cleaned_data["description"] or "Virement"
            cle = _cle_saisie(formulaire)
            existante = _transaction_existante(cle, compte_source)
            if existante:
                if existante.montant != montant:
                    messages.error(
                        request,
                        "Cette soumission a déjà été traitée avec un autre montant. "
                        "Rechargez le formulaire pour effectuer une nouvelle opération.",
                    )
                else:
                    messages.info(
                        request,
                        f"Ce virement a déjà été enregistré (référence {existante.reference}).",
                    )
                return redirect("transactions:detail_compte", compte_id=compte_source.id)

            compte_destination = get_object_or_404(CompteBancaire, numero_compte=numero_destination)
            try:
                frais = services.calculer_frais(RegleFrais.Operation.VIREMENT, montant)
                operation = compte_source.virer_vers(
                    compte_destination,
                    montant,
                    description,
                    frais=frais,
                    categorie=formulaire.cleaned_data.get("categorie", ""),
                    canal=Transaction.Canal.APPLICATION,
                    cree_par=request.user,
                    cle_idempotence=cle,
                )
            except ValidationError as erreur:
                messages.error(request, _message_erreur(erreur))
            else:
                arrondi = None
                if compte_destination.proprietaire_id != request.user.id:
                    arrondi = _arrondi_sans_echec(
                        request.user, compte_source, montant, operation
                    )
                JournalAudit.journaliser(
                    request.user,
                    "Virement",
                    f"{montant} {compte_source.code_devise} de {compte_source.numero_compte} "
                    f"vers {compte_destination.numero_compte} — réf {operation.reference}",
                )
                _notifier_sans_echec(
                    request.user,
                    "Virement émis",
                    f"{montant} {compte_source.code_devise} envoyés vers le compte "
                    f"n° {compte_destination.numero_compte}. Frais : {frais} {compte_source.code_devise}. "
                    f"Référence : {operation.reference}.",
                    type_notification="TRANSACTION",
                    niveau="SUCCES",
                    lien=_lien_compte(compte_source),
                )
                if compte_destination.proprietaire_id != request.user.id:
                    _notifier_sans_echec(
                        compte_destination.proprietaire,
                        "Virement reçu",
                        f"Vous avez reçu {montant} {compte_destination.code_devise} de "
                        f"{request.user.get_full_name() or request.user.username} "
                        f"sur le compte n° {compte_destination.numero_compte}.",
                        type_notification="TRANSACTION",
                        niveau="SUCCES",
                        lien=_lien_compte(compte_destination),
                    )
                if arrondi is not None:
                    messages.info(
                        request,
                        f"Arrondi épargne : {arrondi.montant} {compte_source.code_devise} transférés "
                        "vers votre compte d'épargne.",
                    )
                complement_frais = f" (frais : {frais} {compte_source.code_devise})" if frais else ""
                messages.success(
                    request,
                    "Votre virement ou votre transaction a été effectué(e) avec succès.",
                )
                messages.info(
                    request,
                    f"Virement de {montant} {compte_source.code_devise} vers le compte "
                    f"n° {compte_destination.numero_compte}{complement_frais} — "
                    f"référence {operation.reference}.",
                )
                return redirect("transactions:detail_compte", compte_id=compte_source.id)
    else:
        initial = {}
        beneficiaire_id = request.GET.get("beneficiaire")
        if beneficiaire_id:
            beneficiaire = beneficiaires.filter(pk=beneficiaire_id).first()
            if beneficiaire:
                initial["numero_compte_destination"] = beneficiaire.numero_compte
        formulaire = FormulaireVirement(utilisateur=request.user, initial=initial)

    return render(
        request,
        "transactions/virement.html",
        {
            "formulaire": formulaire,
            "regles_frais": _regles_frais(RegleFrais.Operation.VIREMENT),
            "beneficiaires": beneficiaires,
        },
    )


@login_required
def historique(request):
    comptes_utilisateur = request.user.comptes_bancaires.all()
    transactions = Transaction.objects.filter(
        compte_source__in=comptes_utilisateur
    ) | Transaction.objects.filter(compte_destination__in=comptes_utilisateur)
    transactions = transactions.select_related("compte_source", "compte_destination").distinct()

    recherche = request.GET.get("recherche", "").strip()
    if recherche:
        transactions = transactions.filter(
            Q(description__icontains=recherche) | Q(reference__icontains=recherche)
        )

    formulaire = FormulaireFiltreHistorique(request.GET or None)
    if formulaire.is_valid():
        # Bornes en datetime local : les lookups ``__date`` dépendent des
        # tables de fuseaux horaires MySQL, qui ne sont pas toujours chargées.
        if formulaire.cleaned_data.get("date_debut"):
            debut = formulaire.cleaned_data["date_debut"]
            transactions = transactions.filter(
                date_transaction__gte=timezone.make_aware(datetime.combine(debut, time.min))
            )
        if formulaire.cleaned_data.get("date_fin"):
            fin = formulaire.cleaned_data["date_fin"] + timedelta(days=1)
            transactions = transactions.filter(
                date_transaction__lt=timezone.make_aware(datetime.combine(fin, time.min))
            )
        if formulaire.cleaned_data.get("type_transaction"):
            transactions = transactions.filter(
                type_transaction=formulaire.cleaned_data["type_transaction"]
            )
        if formulaire.cleaned_data.get("montant_min"):
            transactions = transactions.filter(montant__gte=formulaire.cleaned_data["montant_min"])

    transactions = transactions.order_by("-date_transaction", "-id")

    return render(
        request,
        "transactions/historique.html",
        {"transactions": transactions, "formulaire": formulaire, "recherche": recherche},
    )


# ---------------------------------------------------------------------------
# Virements programmés
# ---------------------------------------------------------------------------
@login_required
def virements_programmes(request):
    programmes = request.user.virements_programmes.select_related(
        "compte_source", "compte_source__devise", "compte_destination"
    )
    if request.method == "POST":
        formulaire = FormulaireVirementProgramme(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            cle = _cle_saisie(formulaire)
            existant = VirementProgramme.objects.filter(cle_idempotence=cle).first()
            if existant and existant.proprietaire_id == request.user.id:
                messages.info(request, "Ce virement programmé a déjà été enregistré.")
                return redirect("transactions:virements_programmes")
            source = formulaire.cleaned_data["compte_source"]
            destination = CompteBancaire.objects.filter(
                numero_compte=formulaire.cleaned_data["numero_destination"]
            ).first()
            programme = VirementProgramme.objects.create(
                proprietaire=request.user,
                compte_source=source,
                compte_destination=destination,
                numero_destination=formulaire.cleaned_data["numero_destination"],
                montant=formulaire.cleaned_data["montant"],
                description=formulaire.cleaned_data["description"],
                frequence=formulaire.cleaned_data["frequence"],
                date_debut=formulaire.cleaned_data["date_debut"],
                date_fin=formulaire.cleaned_data["date_fin"],
                prochaine_execution=formulaire.cleaned_data["date_debut"],
                cle_idempotence=cle,
            )
            JournalAudit.journaliser(
                request.user,
                "Création de virement programmé",
                f"#{programme.pk} — {programme.montant} {source.code_devise} vers "
                f"{programme.numero_destination} ({programme.get_frequence_display()})",
            )
            _notifier_sans_echec(
                request.user,
                "Virement programmé créé",
                f"Le virement #{programme.pk} de {programme.montant} {source.code_devise} "
                f"vers {programme.numero_destination} est programmé "
                f"({programme.get_frequence_display().lower()}).",
                type_notification="TRANSACTION",
                niveau="SUCCES",
                lien="/operations/virements-programmes/",
            )
            messages.success(request, "Virement programmé enregistré.")
            return redirect("transactions:virements_programmes")
    else:
        formulaire = FormulaireVirementProgramme(utilisateur=request.user)

    return render(
        request,
        "transactions/virements_programmes.html",
        {
            "programmes": programmes,
            "formulaire": formulaire,
            "regles_frais": _regles_frais(RegleFrais.Operation.VIREMENT),
        },
    )


@login_required
@require_POST
def basculer_virement_programme(request, virement_id):
    virement = get_object_or_404(
        VirementProgramme, pk=virement_id, proprietaire=request.user
    )
    if virement.statut == VirementProgramme.Statut.ACTIF:
        virement.statut = VirementProgramme.Statut.PAUSE
        action, libelle = "Pause de virement programmé", "mis en pause"
    elif virement.statut == VirementProgramme.Statut.PAUSE:
        virement.statut = VirementProgramme.Statut.ACTIF
        action, libelle = "Reprise de virement programmé", "réactivé"
    else:
        messages.error(request, "Ce virement est terminé ou annulé : il ne peut pas être réactivé.")
        return redirect("transactions:virements_programmes")
    virement.derniere_erreur = ""
    virement.save(update_fields=["statut", "derniere_erreur"])
    JournalAudit.journaliser(
        request.user, action, f"Virement programmé #{virement.pk} {libelle}."
    )
    _notifier_sans_echec(
        request.user,
        f"Virement programmé {libelle}",
        f"Le virement #{virement.pk} vers {virement.numero_destination} est désormais "
        f"{virement.get_statut_display().lower()}.",
        type_notification="TRANSACTION",
        niveau="INFO",
        lien="/operations/virements-programmes/",
    )
    messages.success(request, f"Virement programmé {libelle}.")
    return redirect("transactions:virements_programmes")


@login_required
@require_POST
def annuler_virement_programme(request, virement_id):
    virement = get_object_or_404(
        VirementProgramme, pk=virement_id, proprietaire=request.user
    )
    if virement.statut in (VirementProgramme.Statut.TERMINE, VirementProgramme.Statut.ANNULE):
        messages.info(request, "Ce virement programmé est déjà clôturé.")
        return redirect("transactions:virements_programmes")
    virement.statut = VirementProgramme.Statut.ANNULE
    virement.save(update_fields=["statut"])
    JournalAudit.journaliser(
        request.user, "Annulation de virement programmé", f"Virement programmé #{virement.pk}."
    )
    _notifier_sans_echec(
        request.user,
        "Virement programmé annulé",
        f"Le virement #{virement.pk} vers {virement.numero_destination} a été annulé.",
        type_notification="TRANSACTION",
        niveau="ATTENTION",
        lien="/operations/virements-programmes/",
    )
    messages.success(request, "Virement programmé annulé.")
    return redirect("transactions:virements_programmes")


@login_required
@require_POST
def executer_virement_maintenant(request, virement_id):
    virement = get_object_or_404(
        VirementProgramme, pk=virement_id, proprietaire=request.user
    )
    operation = services.executer_virement_programme(virement.pk, forcer=True)
    virement.refresh_from_db()
    if operation is not None:
        JournalAudit.journaliser(
            request.user,
            "Exécution manuelle de virement programmé",
            f"Virement programmé #{virement.pk} — réf {operation.reference}",
        )
        messages.success(
            request,
            f"Échéance exécutée (référence {operation.reference}). "
            f"Prochaine échéance : {virement.prochaine_execution:%d/%m/%Y}.",
        )
    elif virement.derniere_execution and timezone.localtime(
        virement.derniere_execution
    ).date() == timezone.localdate():
        messages.info(request, "Cette échéance a déjà été exécutée aujourd'hui.")
    elif virement.derniere_erreur:
        messages.error(request, f"Exécution impossible : {virement.derniere_erreur}")
    elif virement.statut != VirementProgramme.Statut.ACTIF:
        messages.warning(request, "Seul un virement actif peut être exécuté.")
    else:
        messages.warning(request, "Aucune échéance n'a pu être exécutée.")
    return redirect("transactions:virements_programmes")


# ---------------------------------------------------------------------------
# Relevés de compte (période, soldes d'ouverture/fermeture, PDF/Excel/CSV)
# ---------------------------------------------------------------------------
def _date_parametre(valeur):
    if not valeur:
        return None
    try:
        return datetime.strptime(valeur, "%Y-%m-%d").date()
    except (TypeError, ValueError) as erreur:
        raise ValidationError("Date invalide : utilisez le format AAAA-MM-JJ.") from erreur


def _periode_releve(request):
    aujourdhui = timezone.localdate()
    debut = _date_parametre(request.GET.get("date_debut")) or aujourdhui - timedelta(days=29)
    fin = _date_parametre(request.GET.get("date_fin")) or aujourdhui
    if fin < debut:
        raise ValidationError("La date de fin ne peut pas précéder la date de début.")
    if (fin - debut).days > 366:
        raise ValidationError("La période d'un relevé est limitée à 366 jours.")
    return debut, fin


def _bornes_periode(debut, fin):
    debut_dt = timezone.make_aware(datetime.combine(debut, time.min))
    fin_dt = timezone.make_aware(datetime.combine(fin + timedelta(days=1), time.min))
    return debut_dt, fin_dt


def _transactions_compte(compte, debut_dt=None, fin_dt=None):
    requete = Transaction.objects.filter(
        Q(compte_source=compte) | Q(compte_destination=compte)
    )
    if debut_dt is not None:
        requete = requete.filter(date_transaction__gte=debut_dt)
    if fin_dt is not None:
        requete = requete.filter(date_transaction__lt=fin_dt)
    return requete.select_related("compte_source", "compte_destination").order_by(
        "date_transaction", "id"
    )


def _mouvement(transaction, compte):
    """Variation du solde du compte provoquée par une transaction."""
    delta = Decimal("0")
    if transaction.compte_destination_id == compte.id:
        delta += transaction.montant or Decimal("0")
    if transaction.compte_source_id == compte.id:
        delta -= (transaction.montant or Decimal("0")) + (transaction.frais or Decimal("0"))
    return delta


def _construire_releve(compte, debut, fin):
    compte = CompteBancaire.objects.select_related("devise", "proprietaire").get(pk=compte.pk)
    debut_dt, fin_dt = _bornes_periode(debut, fin)

    apres = _transactions_compte(compte, debut_dt=fin_dt)
    mouvement_apres = sum((_mouvement(t, compte) for t in apres), Decimal("0"))
    solde_cloture = (compte.solde or Decimal("0")) - mouvement_apres

    transactions = list(_transactions_compte(compte, debut_dt=debut_dt, fin_dt=fin_dt))
    mouvement_periode = sum((_mouvement(t, compte) for t in transactions), Decimal("0"))
    solde_ouverture = solde_cloture - mouvement_periode

    lignes = []
    courant = solde_ouverture
    total_credits = Decimal("0")
    total_debits = Decimal("0")
    for transaction in transactions:
        delta = _mouvement(transaction, compte)
        courant += delta
        if delta > 0:
            credit, debit = delta, Decimal("0")
            total_credits += delta
        else:
            credit, debit = Decimal("0"), -delta
            total_debits += -delta
        lignes.append(
            {
                "transaction": transaction,
                "credit": credit,
                "debit": debit,
                "solde": courant,
            }
        )

    return {
        "compte": compte,
        "debut": debut,
        "fin": fin,
        "lignes": lignes,
        "solde_ouverture": solde_ouverture,
        "solde_cloture": solde_cloture,
        "total_credits": total_credits,
        "total_debits": total_debits,
        "nombre": len(lignes),
    }


def _nom_fichier_releve(compte, debut, fin, extension):
    return f"releve_{compte.numero_compte}_{debut:%Y%m%d}_{fin:%Y%m%d}.{extension}"


def _pied_de_page_releve(canvas_obj, doc):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import cm

    largeur = landscape(A4)[0]
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.setFillColor(colors.HexColor("#64748b"))
    canvas_obj.drawString(
        1.5 * cm,
        1.0 * cm,
        "Ma Banque — relevé généré automatiquement, sans signature manuscrite.",
    )
    canvas_obj.drawRightString(largeur - 1.5 * cm, 1.0 * cm, f"Page {doc.page}")
    canvas_obj.restoreState()


def _pdf_releve(releve):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    compte = releve["compte"]
    tampon = io.BytesIO()
    document = SimpleDocTemplate(
        tampon,
        pagesize=landscape(A4),
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.7 * cm,
        title=f"Relevé du compte {compte.numero_compte}",
        author="Ma Banque",
    )
    styles = getSampleStyleSheet()
    petit = ParagraphStyle("petit", parent=styles["Normal"], fontSize=7.5, leading=9)
    elements = [
        Paragraph("Ma Banque — Relevé de compte", styles["Title"]),
        Spacer(1, 0.25 * cm),
        Paragraph(
            f"Titulaire : {escape(compte.proprietaire.get_full_name() or compte.proprietaire.username)}"
            f" — Compte n° {compte.numero_compte} ({escape(compte.get_type_compte_display())}, "
            f"{compte.code_devise})",
            styles["Normal"],
        ),
        Paragraph(
            f"Période : du {releve['debut']:%d/%m/%Y} au {releve['fin']:%d/%m/%Y} — "
            f"édité le {timezone.localtime():%d/%m/%Y %H:%M}",
            styles["Normal"],
        ),
        Paragraph(
            f"Solde d'ouverture : {releve['solde_ouverture']} {compte.code_devise} — "
            f"Solde de clôture : {releve['solde_cloture']} {compte.code_devise} — "
            f"Crédits : {releve['total_credits']} — Débits : {releve['total_debits']} — "
            f"{releve['nombre']} mouvement(s)",
            styles["Normal"],
        ),
        Spacer(1, 0.45 * cm),
    ]

    donnees = [
        ["Date", "Type", "Description", "Référence", "Débit", "Crédit", "Solde"]
    ]
    for ligne in releve["lignes"]:
        transaction = ligne["transaction"]
        donnees.append(
            [
                timezone.localtime(transaction.date_transaction).strftime("%d/%m/%Y %H:%M"),
                Paragraph(escape(transaction.get_type_transaction_display()), petit),
                Paragraph(escape(transaction.description or "-"), petit),
                Paragraph(escape(transaction.reference), petit),
                f"{ligne['debit']:.2f}" if ligne["debit"] else "",
                f"{ligne['credit']:.2f}" if ligne["credit"] else "",
                f"{ligne['solde']:.2f}",
            ]
        )
    if not releve["lignes"]:
        donnees.append(
            [
                "",
                "",
                Paragraph("Aucun mouvement sur la période.", petit),
                "",
                "",
                "",
                "",
            ]
        )
    donnees.append(
        [
            "",
            "",
            Paragraph("<b>Totaux de la période</b>", petit),
            "",
            f"{releve['total_debits']:.2f}",
            f"{releve['total_credits']:.2f}",
            f"{releve['solde_cloture']:.2f}",
        ]
    )

    tableau = Table(
        donnees,
        repeatRows=1,
        colWidths=[2.2 * cm, 2.7 * cm, 7.4 * cm, 3.6 * cm, 2.4 * cm, 2.4 * cm, 2.5 * cm],
    )
    tableau.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d91")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f5f8ff")]),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    elements.append(tableau)
    document.build(elements, onFirstPage=_pied_de_page_releve, onLaterPages=_pied_de_page_releve)
    return tampon.getvalue()


def _csv_releve(releve):
    compte = releve["compte"]
    tampon = io.StringIO()
    tampon.write("\ufeff")
    writer = csv.writer(tampon, delimiter=";")
    writer.writerow(["Ma Banque — Relevé de compte"])
    writer.writerow(
        [
            "Titulaire",
            compte.proprietaire.get_full_name() or compte.proprietaire.username,
            "Compte",
            compte.numero_compte,
            "Devise",
            compte.code_devise,
        ]
    )
    writer.writerow(
        ["Période", f"{releve['debut']:%d/%m/%Y}", "au", f"{releve['fin']:%d/%m/%Y}"]
    )
    writer.writerow(
        [
            "Solde d'ouverture",
            f"{releve['solde_ouverture']}",
            "Solde de clôture",
            f"{releve['solde_cloture']}",
        ]
    )
    writer.writerow([])
    writer.writerow(["Date", "Type", "Description", "Référence", "Débit", "Crédit", "Solde", "Statut"])
    for ligne in releve["lignes"]:
        transaction = ligne["transaction"]
        writer.writerow(
            [
                timezone.localtime(transaction.date_transaction).strftime("%d/%m/%Y %H:%M"),
                transaction.get_type_transaction_display(),
                transaction.description or "",
                transaction.reference,
                f"{ligne['debit']}" if ligne["debit"] else "",
                f"{ligne['credit']}" if ligne["credit"] else "",
                f"{ligne['solde']}",
                transaction.get_statut_display(),
            ]
        )
    writer.writerow([])
    writer.writerow(
        ["Totaux", "", "", "", f"{releve['total_debits']}", f"{releve['total_credits']}", f"{releve['solde_cloture']}"]
    )
    return tampon.getvalue().encode("utf-8")


def _excel_releve(releve):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    compte = releve["compte"]
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Relevé"

    bleu = "0B3D91"
    entete = PatternFill("solid", fgColor=bleu)
    gris = PatternFill("solid", fgColor="E2E8F0")
    bordure = Border(bottom=Side(style="thin", color="CBD5E1"))

    feuille.merge_cells("A1:H1")
    titre = feuille["A1"]
    titre.value = "Ma Banque — Relevé de compte"
    titre.font = Font(size=15, bold=True, color=bleu)
    titre.alignment = Alignment(horizontal="center")
    feuille.merge_cells("A2:H2")
    feuille["A2"] = (
        f"Titulaire : {compte.proprietaire.get_full_name() or compte.proprietaire.username} — "
        f"Compte n° {compte.numero_compte} ({compte.get_type_compte_display()}, {compte.code_devise})"
    )
    feuille["A2"].alignment = Alignment(horizontal="center")
    feuille["A3"] = "Période"
    feuille["B3"] = f"{releve['debut']:%d/%m/%Y} au {releve['fin']:%d/%m/%Y}"
    feuille["D3"] = "Solde d'ouverture"
    feuille["E3"] = releve["solde_ouverture"]
    feuille["F3"] = "Solde de clôture"
    feuille["G3"] = releve["solde_cloture"]
    for cellule in ("D3", "F3"):
        feuille[cellule].font = Font(bold=True)

    entetes = ["Date", "Type", "Description", "Référence", "Débit", "Crédit", "Solde", "Statut"]
    ligne_entete = 5
    for index, valeur in enumerate(entetes, start=1):
        cellule = feuille.cell(row=ligne_entete, column=index, value=valeur)
        cellule.fill = entete
        cellule.font = Font(color="FFFFFF", bold=True)
        cellule.alignment = Alignment(horizontal="center")

    ligne = ligne_entete + 1
    for item in releve["lignes"]:
        transaction = item["transaction"]
        valeurs = [
            timezone.localtime(transaction.date_transaction).strftime("%d/%m/%Y %H:%M"),
            transaction.get_type_transaction_display(),
            transaction.description or "",
            transaction.reference,
            item["debit"] if item["debit"] else None,
            item["credit"] if item["credit"] else None,
            item["solde"],
            transaction.get_statut_display(),
        ]
        for index, valeur in enumerate(valeurs, start=1):
            cellule = feuille.cell(row=ligne, column=index, value=valeur)
            cellule.border = bordure
            if index in (5, 6, 7):
                cellule.number_format = '#,##0.00'
                cellule.alignment = Alignment(horizontal="right")
        ligne += 1

    feuille.cell(row=ligne, column=3, value="Totaux de la période").font = Font(bold=True)
    feuille.cell(row=ligne, column=5, value=releve["total_debits"]).number_format = '#,##0.00'
    feuille.cell(row=ligne, column=6, value=releve["total_credits"]).number_format = '#,##0.00'
    feuille.cell(row=ligne, column=7, value=releve["solde_cloture"]).number_format = '#,##0.00'
    for colonne in range(1, 9):
        feuille.cell(row=ligne, column=colonne).fill = gris
        feuille.cell(row=ligne, column=colonne).font = Font(bold=True)

    largeurs = [18, 22, 42, 30, 14, 14, 16, 14]
    for index, largeur in enumerate(largeurs, start=1):
        feuille.column_dimensions[get_column_letter(index)].width = largeur
    feuille.freeze_panes = feuille.cell(row=ligne_entete + 1, column=1)

    tampon = io.BytesIO()
    classeur.save(tampon)
    classeur.close()
    return tampon.getvalue()


def _reponse_releve(releve, format_export):
    compte = releve["compte"]
    format_export = (format_export or "").lower()
    if format_export == "pdf":
        contenu = _pdf_releve(releve)
        content_type = "application/pdf"
        extension = "pdf"
    elif format_export == "csv":
        contenu = _csv_releve(releve)
        content_type = "text/csv; charset=utf-8"
        extension = "csv"
    elif format_export == "xlsx":
        contenu = _excel_releve(releve)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"
    else:
        raise Http404("Format d'export inconnu.")

    reponse = HttpResponse(contenu, content_type=content_type)
    nom = _nom_fichier_releve(compte, releve["debut"], releve["fin"], extension)
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    return reponse


@login_required
def telecharger_releve(request, compte_id):
    """Relevé PDF d'un compte ; période optionnelle, 30 derniers jours par défaut."""
    compte = get_object_or_404(
        CompteBancaire.objects.select_related("devise", "proprietaire"),
        id=compte_id,
        proprietaire=request.user,
    )
    try:
        debut, fin = _periode_releve(request)
    except ValidationError as erreur:
        messages.error(request, _message_erreur(erreur))
        return redirect("transactions:releves")
    return _reponse_releve(_construire_releve(compte, debut, fin), "pdf")


@login_required
def exporter_releve(request, format_export):
    """Export d'un relevé (PDF, CSV, Excel) sur une période choisie."""
    compte_id = request.GET.get("compte")
    if not compte_id:
        messages.error(request, "Sélectionnez un compte pour générer le relevé.")
        return redirect("transactions:releves")
    compte = get_object_or_404(
        CompteBancaire.objects.select_related("devise", "proprietaire"),
        id=compte_id,
        proprietaire=request.user,
    )
    try:
        debut, fin = _periode_releve(request)
    except ValidationError as erreur:
        messages.error(request, _message_erreur(erreur))
        return redirect("transactions:releves")
    return _reponse_releve(_construire_releve(compte, debut, fin), format_export)


# ---------------------------------------------------------------------------
# Reçu de transaction
# ---------------------------------------------------------------------------
def _pied_de_page_recu(canvas_obj, doc):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm

    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.setFillColor(colors.HexColor("#64748b"))
    canvas_obj.drawString(
        2 * cm,
        1.2 * cm,
        "Document généré automatiquement — ni signature ni cachet manuscrit.",
    )
    canvas_obj.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas_obj.restoreState()


@login_required
def telecharger_recu(request, transaction_id):
    """Reçu PDF d'une transaction, accessible au titulaire ou au personnel."""
    transaction = get_object_or_404(
        Transaction.objects.select_related(
            "compte_source",
            "compte_source__devise",
            "compte_destination",
            "compte_destination__devise",
            "cree_par",
        ),
        pk=transaction_id,
    )
    est_personnel = (
        request.user.est_administrateur() or request.user.est_agent() or request.user.is_superuser
    )
    if not est_personnel:
        comptes_ids = set(request.user.comptes_bancaires.values_list("id", flat=True))
        if transaction.compte_source_id not in comptes_ids and transaction.compte_destination_id not in comptes_ids:
            raise Http404("Reçu introuvable.")

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    tampon = io.BytesIO()
    document = SimpleDocTemplate(
        tampon,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=2 * cm,
        title=f"Reçu {transaction.reference}",
        author="Ma Banque",
    )
    styles = getSampleStyleSheet()
    petit = ParagraphStyle("petit", parent=styles["Normal"], fontSize=8.5, leading=11)
    elements = [
        Paragraph("Ma Banque", styles["Title"]),
        Paragraph("Reçu de transaction", styles["Heading2"]),
        Spacer(1, 0.4 * cm),
        Paragraph(f"<b>Référence :</b> {escape(transaction.reference)}", styles["Normal"]),
        Paragraph(
            f"<b>Date :</b> {timezone.localtime(transaction.date_transaction):%d/%m/%Y à %H:%M}",
            styles["Normal"],
        ),
        Paragraph(f"<b>Statut :</b> {escape(transaction.get_statut_display())}", styles["Normal"]),
        Paragraph(
            f"<b>Canal :</b> {escape(transaction.get_canal_display())} — "
            f"<b>Type :</b> {escape(transaction.get_type_transaction_display())}",
            styles["Normal"],
        ),
        Spacer(1, 0.35 * cm),
    ]

    lignes = []
    if transaction.compte_source_id:
        source = transaction.compte_source
        lignes.append(["Compte émetteur", f"n° {source.numero_compte} ({source.code_devise})"])
    if transaction.compte_destination_id:
        destination = transaction.compte_destination
        lignes.append(["Compte bénéficiaire", f"n° {destination.numero_compte} ({destination.code_devise})"])
    lignes.extend(
        [
            ["Description", transaction.description or "-"],
            ["Montant", f"{transaction.montant} {transaction.devise or 'XAF'}"],
            ["Frais", f"{transaction.frais} {transaction.devise or 'XAF'}"],
            ["Total débité", f"{transaction.montant_total_debite} {transaction.devise or 'XAF'}"],
        ]
    )
    if transaction.tiers_nom:
        lignes.append(["Déposant / tiers", transaction.tiers_nom])
    if transaction.tiers_telephone:
        lignes.append(["Téléphone du tiers", transaction.tiers_telephone])
    if transaction.reference_externe:
        lignes.append(["Référence externe", transaction.reference_externe])
    lignes.append(["Identifiant d'opération", str(transaction.operation_id)])

    tableau = Table(lignes, colWidths=[5 * cm, 11 * cm])
    tableau.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f5f8ff")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    elements.append(tableau)
    elements.append(Spacer(1, 0.45 * cm))
    elements.append(
        Paragraph(
            "Ce reçu est émis électroniquement par Ma Banque pour la transaction référencée "
            "ci-dessus. Il ne comporte aucune signature manuscrite ni cachet. En cas de "
            "contestation, contactez votre agence en citant la référence.",
            petit,
        )
    )
    document.build(elements, onFirstPage=_pied_de_page_recu, onLaterPages=_pied_de_page_recu)

    reponse = HttpResponse(tampon.getvalue(), content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="recu_{transaction.reference}.pdf"'
    return reponse
