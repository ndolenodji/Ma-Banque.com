"""
Vues du paiement mobile côté client : recharge du compte bancaire depuis
le mobile money (cash-in), retrait vers le mobile money (cash-out), suivi
et historique.

Toute la logique financière (débit, crédit, remboursement, idempotence)
est centralisée dans `services.py`. Les vues ne font que valider le
formulaire et présenter le résultat. Le microservice FastAPI est une
SIMULATION explicitement identifiée : aucun opérateur réel n'est appelé.
"""

import io
import uuid
from decimal import Decimal, InvalidOperation

try:
    # Dépendance optionnelle : l'application démarre même si elle est absente.
    import qrcode
except ImportError:  # pragma: no cover - environnement Python incomplet
    qrcode = None

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from comptes.securite import image_qr_data_uri
from transactions.models import CompteBancaire, RegleFrais

from . import providers, services
from .forms import FormulairePaiementMobile
from .models import PaiementMobile


def _cle_idempotence(request):
    """Jeton anti double-soumission, régénéré à chaque affichage du formulaire."""
    cle = (request.POST.get("cle_idempotence") or "").strip()
    try:
        return str(uuid.UUID(cle))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid4())


def _message_validation(erreur):
    if hasattr(erreur, "messages"):
        return " ".join(erreur.messages)
    return str(erreur)


def _contexte_informatif():
    """Informations publiques sur les opérateurs et la grille de frais."""
    return {
        "operateurs": providers.OPERATEURS.values(),
        "regle_frais": RegleFrais.objects.filter(
            type_operation=RegleFrais.Operation.PAIEMENT_MOBILE, actif=True
        ).first(),
    }


@login_required
def recharger_compte(request):
    """Cash-in : le portefeuille mobile money crédite le compte bancaire."""
    cle = _cle_idempotence(request)
    if request.method == "POST":
        formulaire = FormulairePaiementMobile(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            try:
                paiement = services.initier_recharge(
                    request.user,
                    formulaire.cleaned_data["compte_bancaire"],
                    formulaire.cleaned_data["montant"],
                    formulaire.cleaned_data["operateur"],
                    formulaire.cleaned_data["numero_telephone"],
                    cle_idempotence=cle,
                )
            except ValidationError as erreur:
                messages.error(request, f"Recharge mobile impossible : {_message_validation(erreur)}")
            else:
                if paiement.statut == PaiementMobile.Statut.ECHOUE:
                    messages.error(
                        request, f"La recharge n'a pas pu être initiée : {paiement.message_erreur}"
                    )
                else:
                    messages.info(
                        request,
                        "Votre demande de recharge a été transmise. "
                        "Le compte sera crédité uniquement après confirmation de l'opérateur.",
                    )
                return redirect("paiement_mobile:statut", paiement_id=paiement.id)
    else:
        formulaire = FormulairePaiementMobile(utilisateur=request.user)

    contexte = {
        "formulaire": formulaire,
        "cle_idempotence": cle,
        "mode": "recharge",
    }
    contexte.update(_contexte_informatif())
    return render(request, "paiement_mobile/recharger.html", contexte)


@login_required
def retrait_mobile(request):
    """Cash-out : le compte bancaire est débité, puis l'opérateur paie le wallet."""
    cle = _cle_idempotence(request)
    if request.method == "POST":
        formulaire = FormulairePaiementMobile(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            try:
                paiement = services.initier_retrait(
                    request.user,
                    formulaire.cleaned_data["compte_bancaire"],
                    formulaire.cleaned_data["montant"],
                    formulaire.cleaned_data["operateur"],
                    formulaire.cleaned_data["numero_telephone"],
                    cle_idempotence=cle,
                )
            except ValidationError as erreur:
                messages.error(request, f"Retrait mobile impossible : {_message_validation(erreur)}")
            else:
                if paiement.statut == PaiementMobile.Statut.ECHOUE:
                    messages.error(request, f"Retrait mobile échoué : {paiement.message_erreur}")
                else:
                    messages.info(
                        request,
                        "Votre demande de retrait est en cours. "
                        "Le montant est réservé sur votre compte et sera remboursé en cas d'échec.",
                    )
                return redirect("paiement_mobile:statut", paiement_id=paiement.id)
    else:
        formulaire = FormulairePaiementMobile(utilisateur=request.user)

    contexte = {
        "formulaire": formulaire,
        "cle_idempotence": cle,
        "mode": "retrait",
    }
    contexte.update(_contexte_informatif())
    return render(request, "paiement_mobile/retrait_mobile.html", contexte)


@login_required
def statut_paiement(request, paiement_id):
    """Synchronise le statut auprès de l'opérateur, puis affiche l'état connu."""
    paiement = get_object_or_404(
        PaiementMobile.objects.select_related(
            "compte_bancaire", "transaction_bancaire", "transaction_remboursement"
        ),
        id=paiement_id,
        client=request.user,
    )
    paiement = services.synchroniser_paiement(paiement, request.user)
    return render(
        request,
        "paiement_mobile/statut.html",
        {
            "paiement": paiement,
            "transaction_debit": (
                paiement.transaction_bancaire
                if paiement.type_operation == PaiementMobile.TypeOperation.RETRAIT_MOBILE
                else None
            ),
            "transaction_credit": (
                paiement.transaction_bancaire
                if paiement.type_operation == PaiementMobile.TypeOperation.RECHARGE
                else None
            ),
        },
    )


def _contenu_qr_compte(utilisateur, compte):
    """Charge utile lisible par un lecteur QR, sans aucune donnée secrète."""
    titulaire = " ".join((utilisateur.get_full_name() or utilisateur.username).split())
    return (
        "MABANQUE|TYPE=COMPTE|"
        f"TITULAIRE={titulaire}|COMPTE={compte.numero_compte}|DEVISE={compte.code_devise}"
    )


def _image_qr_png(contenu):
    if qrcode is None:
        return None
    image = qrcode.make(contenu)
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return tampon.getvalue()


@login_required
def mon_code_qr(request):
    """QR personnel du client : numéro de compte et titulaire, rien de sensible."""
    comptes = list(
        request.user.comptes_bancaires.filter(statut=CompteBancaire.Statut.ACTIF)
        .select_related("devise")
        .order_by("date_ouverture")
    )
    compte = None
    if comptes:
        compte_id = request.GET.get("compte")
        compte = next((c for c in comptes if str(c.pk) == str(compte_id)), comptes[0])
    contenu = _contenu_qr_compte(request.user, compte) if compte else ""
    return render(
        request,
        "paiement_mobile/mon_code_qr.html",
        {
            "comptes": comptes,
            "compte": compte,
            "contenu_qr": contenu,
            "qr_data_uri": image_qr_data_uri(contenu) if contenu else "",
        },
    )


@login_required
def telecharger_mon_qr(request):
    """Téléchargement PNG du QR personnel, toujours limité au compte du client."""
    comptes = request.user.comptes_bancaires.filter(statut=CompteBancaire.Statut.ACTIF)
    compte = comptes.filter(pk=request.GET.get("compte")).first() or comptes.first()
    if compte is None:
        messages.error(request, "Aucun compte actif : impossible de générer un QR code.")
        return redirect("paiement_mobile:mon_code_qr")
    image = _image_qr_png(_contenu_qr_compte(request.user, compte))
    if image is None:
        messages.error(
            request,
            "Téléchargement du QR indisponible : la bibliothèque « qrcode » est absente "
            "de l'environnement Python utilisé.",
        )
        return redirect("paiement_mobile:mon_code_qr")
    reponse = HttpResponse(image, content_type="image/png")
    reponse["Content-Disposition"] = f'attachment; filename="qr_compte_{compte.numero_compte}.png"'
    return reponse


@login_required
def paiement_marchand(request):
    """
    Génération d'un QR code de démonstration pour le paiement marchand.
    Aucun flux d'argent réel n'est déclenché ici : le paiement marchand
    réel est traité par l'application `reseau` après validation du marchand.
    """
    comptes = request.user.comptes_bancaires.filter(statut=CompteBancaire.Statut.ACTIF)
    reference_qr = None
    montant_qr = None
    qr_data_uri = ""

    if request.method == "POST":
        compte = comptes.filter(id=request.POST.get("compte_bancaire")).first()
        montant_saisi = (request.POST.get("montant") or "").strip().replace(",", ".")
        try:
            montant = Decimal(montant_saisi)
            if not montant.is_finite() or montant <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            messages.error(request, "Saisissez un montant valide supérieur à zéro.")
        else:
            if compte is None:
                messages.error(request, "Sélectionnez un compte bancaire actif.")
            else:
                reference_qr = f"QR-DEMO-{uuid.uuid4().hex[:10].upper()}"
                montant_qr = montant.quantize(Decimal("0.01"))
                qr_data_uri = image_qr_data_uri(
                    "MABANQUE|TYPE=PAIEMENT_MARCHAND|"
                    f"REFERENCE={reference_qr}|MONTANT={montant_qr}|"
                    f"DEVISE={compte.code_devise}|COMPTE={compte.numero_compte}"
                )

    return render(
        request,
        "paiement_mobile/paiement_marchand.html",
        {
            "comptes": comptes,
            "reference_qr": reference_qr,
            "montant_qr": montant_qr,
            "qr_data_uri": qr_data_uri,
        },
    )


@login_required
def historique_paiements(request):
    base = PaiementMobile.objects.filter(client=request.user)
    paiements = base.select_related("compte_bancaire")

    statut = (request.GET.get("statut") or "").strip()
    operateur = (request.GET.get("operateur") or "").strip()
    type_operation = (request.GET.get("type_operation") or "").strip()
    if statut in PaiementMobile.Statut.values:
        paiements = paiements.filter(statut=statut)
    if operateur in PaiementMobile.Operateur.values:
        paiements = paiements.filter(operateur=operateur)
    if type_operation in PaiementMobile.TypeOperation.values:
        paiements = paiements.filter(type_operation=type_operation)

    statistiques = base.aggregate(
        nb_total=Count("id"),
        nb_reussi=Count("id", filter=Q(statut=PaiementMobile.Statut.REUSSI)),
        nb_attente=Count("id", filter=Q(statut=PaiementMobile.Statut.EN_ATTENTE)),
        nb_echec=Count(
            "id",
            filter=Q(
                statut__in=[PaiementMobile.Statut.ECHOUE, PaiementMobile.Statut.ANNULE]
            ),
        ),
        volume_reussi=Sum("montant", filter=Q(statut=PaiementMobile.Statut.REUSSI)),
        frais_reussi=Sum("frais", filter=Q(statut=PaiementMobile.Statut.REUSSI)),
    )
    for cle, valeur in statistiques.items():
        if valeur is None:
            statistiques[cle] = 0

    paginator = Paginator(paiements.order_by("-date_creation"), 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "paiement_mobile/historique.html",
        {
            "page_obj": page_obj,
            "paiements": page_obj.object_list,
            "statistiques": statistiques,
            "filtres": {
                "statut": statut,
                "operateur": operateur,
                "type_operation": type_operation,
            },
            "statuts": PaiementMobile.Statut.choices,
            "operateurs": PaiementMobile.Operateur.choices,
            "types_operation": PaiementMobile.TypeOperation.choices,
        },
    )
