"""Vues du paiement de factures simulé.

Les fournisseurs sont explicitement simulés. Un paiement débite réellement le
compte du client et trace la transaction, mais ne règle aucune facture
externe réelle.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from comptes.models import JournalAudit
from comptes.services_notifications import notifier
from transactions.models import Transaction
from transactions.services import appliquer_arrondi, calculer_frais

from .forms import FormulaireFacture, FormulairePaiementFacture
from .models import CompteFacture, Facture, FournisseurFacture


def _notifier(utilisateur, titre, message, *, niveau="INFO", lien=None):
    try:
        notifier(
            utilisateur,
            titre,
            message,
            type_notification="FACTURE",
            niveau=niveau,
            lien=lien or reverse("factures:tableau_factures"),
        )
    except Exception:  # noqa: BLE001 - la facture est déjà enregistrée
        pass


def _message_validation(erreur):
    if hasattr(erreur, "messages"):
        return " ".join(erreur.messages)
    return str(erreur)


@login_required
def tableau_factures(request):
    factures_en_attente = (
        request.user.factures.filter(statut=Facture.Statut.EN_ATTENTE)
        .select_related("fournisseur")
        .order_by("date_echeance")
    )
    factures_payees = (
        request.user.factures.filter(statut=Facture.Statut.PAYEE)
        .select_related("fournisseur", "transaction")
        .order_by("-date_paiement")[:10]
    )
    aujourd_hui = timezone.localdate()
    total_a_payer = factures_en_attente.aggregate(total=Sum("montant"))["total"] or Decimal("0")
    # Bornes datetime calculées en Python : les lookups __year/__month de
    # MySQL dépendent des tables de fuseaux, absentes sur cette installation.
    debut_mois = timezone.make_aware(datetime.combine(aujourd_hui.replace(day=1), time.min))
    prochain_mois = (aujourd_hui.replace(day=28) + timedelta(days=4)).replace(day=1)
    fin_mois = timezone.make_aware(datetime.combine(prochain_mois, time.min))
    total_paye_mois = (
        request.user.factures.filter(
            statut=Facture.Statut.PAYEE,
            date_paiement__gte=debut_mois,
            date_paiement__lt=fin_mois,
        ).aggregate(total=Sum("montant"))["total"]
        or Decimal("0")
    )
    references = request.user.comptes_factures.select_related("fournisseur").all()[:10]
    return render(
        request,
        "factures/tableau_factures.html",
        {
            "factures_en_attente": factures_en_attente,
            "factures_payees": factures_payees,
            "total_a_payer": total_a_payer,
            "total_paye_mois": total_paye_mois,
            "nombre_en_retard": sum(1 for facture in factures_en_attente if facture.est_en_retard),
            "references": references,
            "fournisseurs": FournisseurFacture.objects.filter(actif=True),
        },
    )


@login_required
def ajouter_facture(request):
    if request.method == "POST":
        formulaire = FormulaireFacture(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            facture = formulaire.save()
            JournalAudit.journaliser(
                request.user,
                "Enregistrement d'une facture simulée",
                f"{facture.fournisseur.nom} — {facture.reference} — {facture.montant} FCFA",
            )
            _notifier(
                request.user,
                "Facture enregistrée",
                f"Votre facture {facture.reference} ({facture.montant} FCFA) est en attente de paiement.",
                niveau="INFO",
                lien=reverse("factures:detail_facture", args=[facture.pk]),
            )
            messages.success(
                request,
                "Facture enregistrée. Le fournisseur est simulé : le paiement débitera votre compte "
                "sans régler de facture externe réelle.",
            )
            return redirect("factures:detail_facture", facture_id=facture.pk)
    else:
        formulaire = FormulaireFacture(utilisateur=request.user)
    return render(
        request,
        "factures/ajouter_facture.html",
        {
            "formulaire": formulaire,
            "fournisseurs": FournisseurFacture.objects.filter(actif=True),
            "references": request.user.comptes_factures.select_related("fournisseur").all(),
        },
    )


@login_required
def detail_facture(request, facture_id):
    facture = get_object_or_404(
        Facture.objects.select_related("fournisseur", "transaction"),
        pk=facture_id,
        proprietaire=request.user,
    )
    formulaire_paiement = None
    frais = Decimal("0")
    if facture.statut == Facture.Statut.EN_ATTENTE:
        frais = calculer_frais(Transaction.Type.PAIEMENT_FACTURE, facture.montant)
        if request.method == "POST":
            message, redirection = _executer_paiement(request, facture, frais)
            messages.add_message(request, message[0], message[1])
            return redirect(redirection)
        formulaire_paiement = FormulairePaiementFacture(utilisateur=request.user)
    return render(
        request,
        "factures/detail_facture.html",
        {
            "facture": facture,
            "formulaire_paiement": formulaire_paiement,
            "frais": frais,
            "total": facture.montant + frais,
        },
    )


def _executer_paiement(request, facture, frais):
    """Valide et exécute le paiement ; retourne ((niveau, message), url)."""
    formulaire = FormulairePaiementFacture(request.POST, utilisateur=request.user)
    if not formulaire.is_valid():
        for erreurs in formulaire.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
        return (
            (messages.ERROR, "Le paiement n'a pas pu être effectué."),
            reverse("factures:detail_facture", args=[facture.pk]),
        )
    compte = formulaire.cleaned_data["compte"]
    cle = formulaire.cleaned_data.get("cle_idempotence")
    if cle and facture.transaction_id is None:
        deja = Transaction.objects.filter(cle_idempotence=str(cle), factures_payees=facture).exists()
        if deja:
            return (
                (messages.INFO, "Cette facture a déjà été payée avec cette opération."),
                reverse("factures:detail_facture", args=[facture.pk]),
            )
    try:
        transaction_effectuee = facture.payer(
            compte,
            frais=frais,
            cree_par=request.user,
            cle_idempotence=str(cle) if cle else None,
        )
    except ValidationError as erreur:
        return (
            (messages.ERROR, f"Paiement impossible : {_message_validation(erreur)}"),
            reverse("factures:detail_facture", args=[facture.pk]),
        )

    appliquer_arrondi(request.user, compte, facture.montant, transaction_effectuee)

    JournalAudit.journaliser(
        request.user,
        "Paiement de facture simulée",
        f"{facture.reference} — {facture.montant} FCFA — compte {compte.numero_compte}",
    )
    _notifier(
        request.user,
        "Facture payée",
        f"Votre facture {facture.reference} ({facture.montant} FCFA) a été payée. "
        "Fournisseur simulé : aucun règlement externe réel n'a été effectué.",
        niveau="SUCCES",
        lien=reverse("factures:detail_facture", args=[facture.pk]),
    )
    return (
        (messages.SUCCESS, "Paiement enregistré. La facture est marquée comme payée."),
        reverse("factures:detail_facture", args=[facture.pk]),
    )


@login_required
@require_POST
def annuler_facture(request, facture_id):
    facture = get_object_or_404(Facture, pk=facture_id, proprietaire=request.user)
    if facture.statut != Facture.Statut.EN_ATTENTE:
        messages.error(request, "Seule une facture en attente peut être annulée.")
        return redirect("factures:detail_facture", facture_id=facture.pk)
    facture.statut = Facture.Statut.ANNULEE
    facture.save(update_fields=["statut"])
    JournalAudit.journaliser(
        request.user, "Annulation d'une facture simulée", f"{facture.reference} — {facture.montant} FCFA"
    )
    messages.success(request, "Facture annulée.")
    return redirect("factures:tableau_factures")
