"""Vues client des cartes bancaires de démonstration.

Aucune carte réelle n'est émise, aucun PAN/CVV/PIN n'est généré ou stocké.
Les contrôles affichés (gel, plafonds, usages) sont réels dans l'application
mais ne pilotent aucun réseau carte.
"""

import calendar
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from comptes.models import JournalAudit
from comptes.services_notifications import notifier
from kyc.models import DossierKYC

from .forms import FormulaireDemandeCarte, FormulaireReglagesCarte
from .models import CarteBancaire


def _kyc_verifie(utilisateur):
    dossier = getattr(utilisateur, "dossier_kyc", None)
    return utilisateur.identite_verifiee or (
        dossier is not None and dossier.statut == DossierKYC.Statut.APPROUVE
    )


def _date_expiration():
    aujourd_hui = timezone.localdate()
    annee = aujourd_hui.year + 3
    jour = min(aujourd_hui.day, calendar.monthrange(annee, aujourd_hui.month)[1])
    return date(annee, aujourd_hui.month, jour)


def _notifier(utilisateur, titre, message, *, niveau="INFO", lien=None):
    try:
        notifier(
            utilisateur,
            titre,
            message,
            type_notification="SYSTEME",
            niveau=niveau,
            lien=lien or "/cartes/",
        )
    except Exception:  # noqa: BLE001 - la carte est déjà enregistrée
        pass


@login_required
def mes_cartes(request):
    cartes = request.user.cartes_bancaires.select_related("compte").all()
    return render(request, "cartes/mes_cartes.html", {"cartes": cartes})


@login_required
def demander_carte(request):
    if not _kyc_verifie(request.user):
        messages.warning(
            request, "Votre identité doit être vérifiée avant de demander une carte."
        )
        return redirect("kyc:mon_dossier")
    if not request.user.comptes_bancaires.filter(statut="ACTIF").exists():
        messages.warning(request, "Vous devez disposer d'un compte actif pour demander une carte.")
        return redirect("transactions:ouvrir_compte")

    if request.method == "POST":
        formulaire = FormulaireDemandeCarte(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            type_carte = formulaire.cleaned_data["type_carte"]
            carte = CarteBancaire(
                utilisateur=request.user,
                compte=formulaire.cleaned_data["compte"],
                type_carte=type_carte,
                nom_titulaire=formulaire.cleaned_data["nom_titulaire"],
                date_expiration=_date_expiration(),
                statut=(
                    CarteBancaire.Statut.ACTIVE
                    if type_carte == CarteBancaire.TypeCarte.VIRTUELLE
                    else CarteBancaire.Statut.DEMANDEE
                ),
            )
            carte.save()
            JournalAudit.journaliser(
                request.user,
                "Demande de carte de démonstration",
                f"{carte.get_type_carte_display()} {carte.identifiant_demo} — compte {carte.compte.numero_compte}",
                getattr(request, "adresse_ip_client", None),
            )
            _notifier(
                request.user,
                "Carte enregistrée",
                f"Votre {carte.get_type_carte_display().lower()} {carte.numero_masque} "
                f"a été enregistrée ({carte.get_statut_display().lower()}).",
                niveau="SUCCES",
                lien=f"/cartes/{carte.pk}/",
            )
            messages.success(
                request,
                "Carte virtuelle activée immédiatement (démonstration)."
                if carte.statut == CarteBancaire.Statut.ACTIVE
                else "Demande de carte physique enregistrée : elle doit être activée par la banque.",
            )
            return redirect("cartes:detail_carte", carte_id=carte.pk)
    else:
        formulaire = FormulaireDemandeCarte(utilisateur=request.user)

    return render(request, "cartes/demander_carte.html", {"formulaire": formulaire})


@login_required
def detail_carte(request, carte_id):
    carte = get_object_or_404(
        CarteBancaire.objects.select_related("compte"), pk=carte_id, utilisateur=request.user
    )
    peut_modifier = carte.statut in (CarteBancaire.Statut.ACTIVE, CarteBancaire.Statut.GELEE)
    if request.method == "POST":
        if not peut_modifier:
            messages.error(request, "Cette carte ne peut plus être modifiée.")
            return redirect("cartes:detail_carte", carte_id=carte.pk)
        formulaire = FormulaireReglagesCarte(request.POST, instance=carte)
        if formulaire.is_valid():
            formulaire.save()
            JournalAudit.journaliser(
                request.user,
                "Modification des réglages de carte",
                f"Carte {carte.numero_masque} — plafonds et contrôles mis à jour.",
            )
            _notifier(
                request.user,
                "Réglages de carte mis à jour",
                f"Les contrôles de votre carte {carte.numero_masque} ont été mis à jour.",
                niveau="INFO",
                lien=f"/cartes/{carte.pk}/",
            )
            messages.success(request, "Réglages de la carte enregistrés.")
            return redirect("cartes:detail_carte", carte_id=carte.pk)
    else:
        formulaire = FormulaireReglagesCarte(instance=carte)

    return render(
        request,
        "cartes/detail_carte.html",
        {"carte": carte, "formulaire": formulaire, "peut_modifier": peut_modifier},
    )


@login_required
@require_POST
def basculer_carte(request, carte_id):
    carte = get_object_or_404(CarteBancaire, pk=carte_id, utilisateur=request.user)
    try:
        if carte.statut == CarteBancaire.Statut.ACTIVE:
            carte.geler()
            action, libelle = "Gel de carte", "gelée"
        elif carte.statut == CarteBancaire.Statut.GELEE:
            carte.reactiver()
            action, libelle = "Réactivation de carte", "réactivée"
        else:
            messages.error(request, "Cette carte ne peut pas être gelée ou réactivée.")
            return redirect("cartes:detail_carte", carte_id=carte.pk)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect("cartes:detail_carte", carte_id=carte.pk)
    JournalAudit.journaliser(
        request.user, action, f"Carte de démonstration {carte.numero_masque} {libelle}."
    )
    _notifier(
        request.user,
        f"Carte {libelle}",
        f"Votre carte {carte.numero_masque} est désormais {libelle}.",
        niveau="ATTENTION" if libelle == "gelée" else "SUCCES",
        lien=f"/cartes/{carte.pk}/",
    )
    messages.success(request, f"Carte {libelle}.")
    return redirect("cartes:detail_carte", carte_id=carte.pk)


@login_required
@require_POST
def annuler_carte(request, carte_id):
    carte = get_object_or_404(CarteBancaire, pk=carte_id, utilisateur=request.user)
    if carte.statut == CarteBancaire.Statut.ANNULEE:
        messages.info(request, "Cette carte est déjà annulée.")
        return redirect("cartes:detail_carte", carte_id=carte.pk)
    carte.annuler("Annulation demandée par le titulaire")
    JournalAudit.journaliser(
        request.user, "Annulation de carte", f"Carte de démonstration {carte.numero_masque} annulée."
    )
    _notifier(
        request.user,
        "Carte annulée",
        f"Votre carte {carte.numero_masque} a été annulée. Aucun débit ne peut plus être effectué.",
        niveau="ATTENTION",
        lien=f"/cartes/{carte.pk}/",
    )
    messages.success(request, "Carte annulée.")
    return redirect("cartes:detail_carte", carte_id=carte.pk)
