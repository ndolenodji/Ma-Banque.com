"""Vues de l'épargne : objectifs, contributions, arrondis et budgets.

Les contributions sont de véritables virements internes entre comptes du
client, tracés dans la table Transaction. Aucun intérêt n'est simulé.
"""

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
from transactions.models import CompteBancaire

from .forms import FormulaireArrondi, FormulaireBudget, FormulaireContribution, FormulaireObjectif
from .models import Budget, ConfigArrondi, ContributionEpargne, ObjectifEpargne


def _comptes_epargne(utilisateur):
    return utilisateur.comptes_bancaires.filter(
        type_compte=CompteBancaire.TypeCompte.EPARGNE,
        statut=CompteBancaire.Statut.ACTIF,
    )


def _notifier(utilisateur, titre, message, *, niveau="INFO", lien=None):
    try:
        notifier(
            utilisateur,
            titre,
            message,
            type_notification="EPARGNE",
            niveau=niveau,
            lien=lien or reverse("epargne:tableau_epargne"),
        )
    except Exception:  # noqa: BLE001 - l'épargne est déjà enregistrée
        pass


def _message_validation(erreur):
    if hasattr(erreur, "messages"):
        return " ".join(erreur.messages)
    return str(erreur)


@login_required
def tableau_epargne(request):
    objectifs = request.user.objectifs_epargne.select_related("compte_epargne").all()
    objectifs_actifs = [objectif for objectif in objectifs if objectif.statut == ObjectifEpargne.Statut.ACTIF]
    comptes_epargne = _comptes_epargne(request.user)
    total_epargne = sum((compte.solde for compte in comptes_epargne), Decimal("0"))
    total_objectifs = sum(
        (objectif.montant_actuel for objectif in objectifs if objectif.statut != ObjectifEpargne.Statut.ANNULE),
        Decimal("0"),
    )
    contributions = (
        ContributionEpargne.objects.filter(objectif__proprietaire=request.user)
        .select_related("objectif", "transaction")
        .order_by("-date_creation")[:10]
    )
    config_arrondi = ConfigArrondi.objects.filter(proprietaire=request.user).first()
    return render(
        request,
        "epargne/tableau_epargne.html",
        {
            "objectifs": objectifs,
            "objectifs_actifs": objectifs_actifs,
            "comptes_epargne": comptes_epargne,
            "total_epargne": total_epargne,
            "total_objectifs": total_objectifs,
            "contributions": contributions,
            "config_arrondi": config_arrondi,
        },
    )


@login_required
def creer_objectif(request):
    if not _comptes_epargne(request.user).exists():
        messages.warning(
            request,
            "Ouvrez d'abord un compte de type épargne pour créer un objectif.",
        )
        return redirect("transactions:ouvrir_compte")
    if request.method == "POST":
        formulaire = FormulaireObjectif(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            objectif = formulaire.save()
            JournalAudit.journaliser(
                request.user,
                "Création d'un objectif d'épargne",
                f"{objectif.nom} — cible {objectif.montant_cible} FCFA",
            )
            messages.success(request, "Objectif d'épargne créé.")
            return redirect("epargne:detail_objectif", objectif_id=objectif.pk)
    else:
        formulaire = FormulaireObjectif(utilisateur=request.user)
    return render(
        request,
        "epargne/objectif_form.html",
        {"formulaire": formulaire, "comptes_epargne": _comptes_epargne(request.user)},
    )


@login_required
def modifier_objectif(request, objectif_id):
    objectif = get_object_or_404(ObjectifEpargne, pk=objectif_id, proprietaire=request.user)
    if request.method == "POST":
        formulaire = FormulaireObjectif(request.POST, instance=objectif, utilisateur=request.user)
        if formulaire.is_valid():
            objectif = formulaire.save()
            JournalAudit.journaliser(
                request.user,
                "Modification d'un objectif d'épargne",
                f"{objectif.nom} — cible {objectif.montant_cible} FCFA",
            )
            messages.success(request, "Objectif mis à jour.")
            return redirect("epargne:detail_objectif", objectif_id=objectif.pk)
    else:
        formulaire = FormulaireObjectif(instance=objectif, utilisateur=request.user)
    return render(
        request,
        "epargne/objectif_form.html",
        {"formulaire": formulaire, "objectif": objectif, "comptes_epargne": _comptes_epargne(request.user)},
    )


@login_required
def detail_objectif(request, objectif_id):
    objectif = get_object_or_404(
        ObjectifEpargne.objects.select_related("compte_epargne"),
        pk=objectif_id,
        proprietaire=request.user,
    )
    contributions = objectif.contributions.select_related("transaction").all()
    formulaire_contribution = FormulaireContribution(objectif=objectif, utilisateur=request.user)
    return render(
        request,
        "epargne/detail_objectif.html",
        {
            "objectif": objectif,
            "contributions": contributions,
            "formulaire_contribution": formulaire_contribution,
        },
    )


@login_required
@require_POST
def contribuer_objectif(request, objectif_id):
    objectif = get_object_or_404(
        ObjectifEpargne.objects.select_related("compte_epargne"),
        pk=objectif_id,
        proprietaire=request.user,
    )
    formulaire = FormulaireContribution(request.POST, objectif=objectif, utilisateur=request.user)
    if not formulaire.is_valid():
        for erreurs in formulaire.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
        return redirect("epargne:detail_objectif", objectif_id=objectif.pk)
    compte_source = formulaire.cleaned_data["compte_source"]
    montant = formulaire.cleaned_data["montant"]
    cle = formulaire.cleaned_data.get("cle_idempotence")
    try:
        transaction_effectuee = objectif.contribuer(
            compte_source,
            montant,
            cle_idempotence=str(cle) if cle else None,
        )
    except ValidationError as erreur:
        messages.error(request, f"Contribution impossible : {_message_validation(erreur)}")
        return redirect("epargne:detail_objectif", objectif_id=objectif.pk)
    JournalAudit.journaliser(
        request.user,
        "Contribution d'épargne",
        f"{objectif.nom} — {montant} FCFA depuis {compte_source.numero_compte}",
    )
    _notifier(
        request.user,
        "Épargne enregistrée",
        f"{montant} FCFA versés sur l'objectif « {objectif.nom} ». "
        f"Progression : {objectif.montant_actuel}/{objectif.montant_cible} FCFA.",
        niveau="SUCCES",
        lien=reverse("epargne:detail_objectif", args=[objectif.pk]),
    )
    if objectif.statut == ObjectifEpargne.Statut.ATTEINT:
        messages.success(request, "Contribution enregistrée : objectif atteint, félicitations !")
    else:
        messages.success(request, "Contribution enregistrée.")
    return redirect("epargne:detail_objectif", objectif_id=objectif.pk)


@login_required
@require_POST
def annuler_objectif(request, objectif_id):
    objectif = get_object_or_404(ObjectifEpargne, pk=objectif_id, proprietaire=request.user)
    if objectif.statut == ObjectifEpargne.Statut.ATTEINT:
        messages.info(
            request,
            "Cet objectif est déjà atteint ; il ne peut plus être annulé. Les fonds restent sur votre compte d'épargne.",
        )
        return redirect("epargne:detail_objectif", objectif_id=objectif.pk)
    if objectif.statut == ObjectifEpargne.Statut.ANNULE:
        messages.info(request, "Cet objectif est déjà annulé.")
        return redirect("epargne:detail_objectif", objectif_id=objectif.pk)
    objectif.statut = ObjectifEpargne.Statut.ANNULE
    objectif.save(update_fields=["statut", "date_mise_a_jour"])
    JournalAudit.journaliser(
        request.user,
        "Annulation d'un objectif d'épargne",
        f"{objectif.nom} — {objectif.montant_actuel} FCFA déjà épargnés",
    )
    messages.success(
        request,
        "Objectif annulé. Les sommes déjà épargnées restent disponibles sur le compte d'épargne.",
    )
    return redirect("epargne:tableau_epargne")


@login_required
def configurer_arrondi(request):
    comptes_epargne = _comptes_epargne(request.user)
    if not comptes_epargne.exists():
        messages.warning(
            request, "Un compte d'épargne est nécessaire pour activer les arrondis automatiques."
        )
        return redirect("transactions:ouvrir_compte")

    config = ConfigArrondi.objects.filter(proprietaire=request.user).first()
    if request.method == "POST":
        formulaire = FormulaireArrondi(
            request.POST, instance=config, utilisateur=request.user
        )
        if formulaire.is_valid():
            config = formulaire.save()
            JournalAudit.journaliser(
                request.user,
                "Configuration des arrondis d'épargne",
                f"{'Activés' if config.actif else 'Désactivés'} — multiple {config.multiple} FCFA",
            )
            messages.success(request, "Configuration des arrondis enregistrée.")
            return redirect("epargne:configurer_arrondi")
    else:
        formulaire = FormulaireArrondi(instance=config, utilisateur=request.user)
    return render(
        request,
        "epargne/arrondi.html",
        {"formulaire": formulaire, "config": config, "comptes_epargne": comptes_epargne},
    )


@login_required
def budgets(request):
    liste_budgets = list(request.user.budgets.all())
    for budget in liste_budgets:
        budget.depense_courante = budget.montant_depense()
        budget.pourcentage_courant = (
            min(999, int((budget.depense_courante / budget.plafond_mensuel) * 100))
            if budget.plafond_mensuel
            else 0
        )
    if request.method == "POST":
        formulaire = FormulaireBudget(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            budget = formulaire.save()
            JournalAudit.journaliser(
                request.user,
                "Création d'un budget",
                f"{budget.get_categorie_display()} — {budget.plafond_mensuel} FCFA / mois",
            )
            messages.success(request, "Budget créé.")
            return redirect("epargne:budgets")
    else:
        formulaire = FormulaireBudget(utilisateur=request.user)
    return render(
        request,
        "epargne/budgets.html",
        {"budgets": liste_budgets, "formulaire": formulaire, "mois_courant": timezone.localdate()},
    )


@login_required
def modifier_budget(request, budget_id):
    budget = get_object_or_404(Budget, pk=budget_id, proprietaire=request.user)
    if request.method == "POST":
        formulaire = FormulaireBudget(request.POST, instance=budget, utilisateur=request.user)
        if formulaire.is_valid():
            formulaire.save()
            JournalAudit.journaliser(
                request.user,
                "Modification d'un budget",
                f"{budget.get_categorie_display()} — {budget.plafond_mensuel} FCFA / mois",
            )
            messages.success(request, "Budget mis à jour.")
            return redirect("epargne:budgets")
    else:
        formulaire = FormulaireBudget(instance=budget, utilisateur=request.user)
    return render(
        request,
        "epargne/budget_form.html",
        {"formulaire": formulaire, "budget": budget},
    )


@login_required
@require_POST
def supprimer_budget(request, budget_id):
    budget = get_object_or_404(Budget, pk=budget_id, proprietaire=request.user)
    libelle = budget.get_categorie_display()
    budget.delete()
    JournalAudit.journaliser(request.user, "Suppression d'un budget", libelle)
    messages.success(request, "Budget supprimé.")
    return redirect("epargne:budgets")
