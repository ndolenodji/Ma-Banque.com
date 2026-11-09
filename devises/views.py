"""Vues des devises : taux administrés et conversion entre comptes.

Aucun taux n'est inventé : l'administrateur saisit chaque taux et sa source.
La devise XAF (FCFA) est la référence. Une conversion est refusée tant que le
taux nécessaire n'est pas configuré.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from comptes.models import JournalAudit
from transactions.models import CompteBancaire, Transaction
from transactions.services import calculer_frais, convertir_devises

from .forms import FormulaireConversion, _comptes_actifs
from .models import Devise


def _message_validation(erreur):
    if hasattr(erreur, "messages"):
        return " ".join(erreur.messages)
    return str(erreur)


def _devises_disponibles():
    return Devise.objects.filter(est_active=True).order_by("code")


def _paires_disponibles(utilisateur):
    comptes = list(_comptes_actifs(utilisateur))
    eligibles = [
        compte
        for compte in comptes
        if compte.devise.code == "XAF" or compte.devise.taux_configure
    ]
    return len({compte.devise_id for compte in eligibles}) >= 2


@login_required
def taux_et_conversion(request):
    formulaire = FormulaireConversion(utilisateur=request.user)
    if request.method == "POST":
        formulaire = FormulaireConversion(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            source = formulaire.cleaned_data["compte_source"]
            destination = formulaire.cleaned_data["compte_destination"]
            montant = formulaire.cleaned_data["montant"]
            try:
                conversion = convertir_devises(
                    source,
                    destination,
                    montant,
                    request.user,
                    cle_idempotence=str(formulaire.cleaned_data.get("cle_idempotence") or "") or None,
                )
            except ValidationError as erreur:
                messages.error(request, f"Conversion impossible : {_message_validation(erreur)}")
            else:
                JournalAudit.journaliser(
                    request.user,
                    "Conversion de devises",
                    f"{conversion.montant_source} {conversion.compte_source.code_devise} → "
                    f"{conversion.montant_destination} {conversion.compte_destination.code_devise} "
                    f"({conversion.reference})",
                )
                messages.success(
                    request,
                    f"Conversion effectuée : {conversion.montant_source} "
                    f"{conversion.compte_source.code_devise} → {conversion.montant_destination} "
                    f"{conversion.compte_destination.code_devise}.",
                )
                return redirect("devises:historique_conversions")
    elif request.GET.get("compte_source"):
        formulaire = FormulaireConversion(
            initial={
                "compte_source": request.GET.get("compte_source"),
                "compte_destination": request.GET.get("compte_destination"),
            },
            utilisateur=request.user,
        )

    frais_estimes = None
    if formulaire.is_bound and formulaire.is_valid():
        frais_estimes = calculer_frais(Transaction.Type.CONVERSION, formulaire.cleaned_data["montant"])

    return render(
        request,
        "devises/taux.html",
        {
            "formulaire": formulaire,
            "devises": _devises_disponibles(),
            "comptes": _comptes_actifs(request.user),
            "paires_disponibles": _paires_disponibles(request.user),
            "frais_estimes": frais_estimes,
        },
    )


@login_required
@require_GET
def apercu_conversion(request):
    """Aperçu JSON en lecture seule, calculé avec les taux administrés."""
    try:
        montant = Decimal((request.GET.get("montant") or "").replace(",", "."))
    except (InvalidOperation, TypeError):
        return JsonResponse({"erreur": "Montant invalide."}, status=400)
    if montant <= 0:
        return JsonResponse({"erreur": "Le montant doit être positif."}, status=400)

    comptes = _comptes_actifs(request.user)
    source = comptes.filter(pk=request.GET.get("compte_source")).first()
    destination = comptes.filter(pk=request.GET.get("compte_destination")).first()
    if source is None or destination is None:
        return JsonResponse({"erreur": "Compte introuvable."}, status=400)
    if source.pk == destination.pk:
        return JsonResponse({"erreur": "Les comptes doivent être différents."}, status=400)
    if source.devise_id == destination.devise_id:
        return JsonResponse({"erreur": "Choisissez deux devises différentes."}, status=400)
    try:
        montant_xaf = source.devise.vers_xaf(montant)
        montant_destination = destination.devise.depuis_xaf(montant_xaf)
    except ValidationError as erreur:
        return JsonResponse({"erreur": _message_validation(erreur)}, status=400)

    frais = calculer_frais(Transaction.Type.CONVERSION, montant)
    taux = (montant_destination / montant).quantize(Decimal("0.000001"))
    return JsonResponse(
        {
            "montant_source": str(montant),
            "montant_destination": str(montant_destination),
            "code_source": source.code_devise,
            "code_destination": destination.code_devise,
            "taux": str(taux),
            "frais": str(frais),
            "total_debit": str(montant + frais),
            "source_taux": source.devise.source_taux,
            "destination_taux": destination.devise.source_taux,
            "avertissement": (
                "Aperçu indicatif calculé avec les taux administrés. "
                "Aucun taux de marché en temps réel n'est utilisé."
            ),
        }
    )


@login_required
def historique_conversions(request):
    conversions = request.user.conversions.select_related(
        "compte_source", "compte_destination", "transaction_source"
    ).all()
    return render(
        request,
        "devises/historique.html",
        {"conversions": conversions},
    )
