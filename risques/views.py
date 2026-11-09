"""Interface d'instruction des alertes de fraude (réservée aux administrateurs)."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from comptes.decorateurs import admin_requis

from .models import AlerteFraude
from .services import traiter_alerte


@admin_requis
def alertes(request):
    requete = AlerteFraude.objects.select_related(
        "utilisateur", "transaction", "paiement_mobile", "traite_par"
    )

    statut = request.GET.get("statut", "").strip()
    niveau = request.GET.get("niveau", "").strip()
    recherche = request.GET.get("q", "").strip()
    if statut:
        requete = requete.filter(statut=statut)
    if niveau:
        requete = requete.filter(niveau=niveau)
    if recherche:
        requete = requete.filter(
            models_q(recherche)
        )

    pagination = Paginator(requete, 20)
    page = pagination.get_page(request.GET.get("page"))

    statistiques = {
        "total": AlerteFraude.objects.count(),
        "nouvelles": AlerteFraude.objects.filter(statut=AlerteFraude.Statut.NOUVELLE).count(),
        "en_revue": AlerteFraude.objects.filter(statut=AlerteFraude.Statut.EN_REVUE).count(),
        "critiques": AlerteFraude.objects.filter(
            niveau=AlerteFraude.Niveau.CRITIQUE,
            statut__in=[AlerteFraude.Statut.NOUVELLE, AlerteFraude.Statut.EN_REVUE],
        ).count(),
    }

    return render(
        request,
        "risques/alertes.html",
        {
            "page_obj": page,
            "alertes": page.object_list,
            "statut": statut,
            "niveau": niveau,
            "recherche": recherche,
            "statuts": AlerteFraude.Statut.choices,
            "niveaux": AlerteFraude.Niveau.choices,
            "statistiques": statistiques,
        },
    )


def models_q(recherche):
    from django.db.models import Q

    return (
        Q(reference__icontains=recherche)
        | Q(utilisateur__username__icontains=recherche)
        | Q(utilisateur__first_name__icontains=recherche)
        | Q(utilisateur__last_name__icontains=recherche)
        | Q(transaction__reference__icontains=recherche)
    )


@admin_requis
def detail_alerte(request, alerte_id):
    alerte = get_object_or_404(
        AlerteFraude.objects.select_related(
            "utilisateur", "transaction", "paiement_mobile", "traite_par", "transaction__compte_source"
        ),
        pk=alerte_id,
    )
    historique_client = (
        AlerteFraude.objects.filter(utilisateur=alerte.utilisateur)
        .exclude(pk=alerte.pk)
        .select_related("transaction")[:8]
    )
    return render(
        request,
        "risques/detail_alerte.html",
        {"alerte": alerte, "historique_client": historique_client},
    )


@admin_requis
@require_POST
def traiter(request, alerte_id):
    alerte = get_object_or_404(AlerteFraude, pk=alerte_id)
    decision = request.POST.get("decision", "")
    commentaire = request.POST.get("commentaire", "")
    try:
        traiter_alerte(alerte, request.user, decision, commentaire)
    except ValueError as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, f"L'alerte {alerte.reference} a été mise à jour.")
    return redirect("risques:detail_alerte", alerte_id=alerte.pk)


@admin_requis
def alertes_non_lues_json(request):
    nouvelles = AlerteFraude.objects.filter(statut=AlerteFraude.Statut.NOUVELLE)
    derniere = nouvelles.order_by("-date_creation").values_list("date_creation", flat=True).first()
    return JsonResponse(
        {
            "nouvelles": nouvelles.count(),
            "critiques": nouvelles.filter(niveau=AlerteFraude.Niveau.CRITIQUE).count(),
            "derniere": timezone.localtime(derniere).isoformat() if derniere else None,
        }
    )
