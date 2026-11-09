"""
Vues du réseau d'agents et de marchands.

Les paiements marchands et les opérations de caisse déplacent réellement des
fonds entre comptes internes de la maquette. Aucun acquéreur, terminal ou
règlement interbancaire externe n'est utilisé : le caractère simulé est
affiché sur chaque écran.
"""

import io
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

try:
    # Dépendance optionnelle : l'application démarre même si elle est absente.
    import qrcode
except ImportError:  # pragma: no cover - environnement Python incomplet
    qrcode = None

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from comptes.decorateurs import role_requis
from comptes.models import JournalAudit
from comptes.services_notifications import notifier
from transactions.models import CompteBancaire, RegleFrais
from transactions.services import calculer_frais

from .forms import (
    FormulaireDemandePaiement,
    FormulaireInscriptionMarchand,
    FormulaireOperationAgent,
    FormulairePaiementDemande,
)
from .models import DemandePaiementMarchand, Marchand, OperationAgent, ProfilAgent


def _cle_idempotence(request):
    vide = uuid.uuid4()
    valeur = (request.POST.get("cle_idempotence") or "").strip()
    try:
        return str(uuid.UUID(valeur))
    except (ValueError, TypeError, AttributeError):
        return str(vide)


def _frais_marchand():
    return RegleFrais.objects.filter(
        type_operation=RegleFrais.Operation.PAIEMENT_MARCHAND, actif=True
    ).first()


def _expirer_si_necessaire(demande):
    if demande.est_expiree:
        demande.statut = DemandePaiementMarchand.Statut.EXPIREE
        demande.save(update_fields=["statut"])
    return demande


@login_required
def annuaire_agents(request):
    recherche = (request.GET.get("q") or "").strip()
    ville = (request.GET.get("ville") or "").strip()
    agents = ProfilAgent.objects.filter(actif=True).select_related("utilisateur")
    if recherche:
        agents = agents.filter(
            Q(nom_agence__icontains=recherche)
            | Q(code_agent__icontains=recherche)
            | Q(ville__icontains=recherche)
            | Q(utilisateur__first_name__icontains=recherche)
            | Q(utilisateur__last_name__icontains=recherche)
        )
    if ville:
        agents = agents.filter(ville__iexact=ville)

    villes = (
        ProfilAgent.objects.filter(actif=True)
        .exclude(ville="")
        .values_list("ville", flat=True)
        .distinct()
        .order_by("ville")
    )
    return render(
        request,
        "reseau/annuaire_agents.html",
        {"agents": agents, "villes": villes, "recherche": recherche, "ville": ville},
    )


@login_required
def annuaire_marchands(request):
    recherche = (request.GET.get("q") or "").strip()
    categorie = (request.GET.get("categorie") or "").strip()
    marchands = Marchand.objects.filter(statut=Marchand.Statut.ACTIF).select_related("compte_reglement")
    if recherche:
        marchands = marchands.filter(
            Q(nom__icontains=recherche)
            | Q(code_marchand__icontains=recherche)
            | Q(ville__icontains=recherche)
            | Q(categorie__icontains=recherche)
        )
    if categorie:
        marchands = marchands.filter(categorie__iexact=categorie)
    categories = (
        Marchand.objects.filter(statut=Marchand.Statut.ACTIF)
        .exclude(categorie="")
        .values_list("categorie", flat=True)
        .distinct()
        .order_by("categorie")
    )
    return render(
        request,
        "reseau/annuaire_marchands.html",
        {
            "marchands": marchands,
            "categories": categories,
            "recherche": recherche,
            "categorie": categorie,
        },
    )


@login_required
def espace_marchand(request):
    marchand = Marchand.objects.filter(proprietaire=request.user).select_related("compte_reglement").first()
    if marchand is None:
        return render(request, "reseau/espace_marchand.html", {"marchand": None})

    demandes = marchand.demandes.select_related("transaction", "payeur").order_by("-date_creation")
    for demande in demandes[:20]:
        _expirer_si_necessaire(demande)
    demandes = demandes[:30]

    encaisse = marchand.demandes.filter(statut=DemandePaiementMarchand.Statut.PAYEE).aggregate(
        total=Sum("montant")
    )["total"] or Decimal("0")
    statistiques = {
        "nb_payees": marchand.demandes.filter(statut=DemandePaiementMarchand.Statut.PAYEE).count(),
        "nb_attente": marchand.demandes.filter(statut=DemandePaiementMarchand.Statut.EN_ATTENTE).count(),
        "encaisse": encaisse,
    }
    return render(
        request,
        "reseau/espace_marchand.html",
        {"marchand": marchand, "demandes": demandes, "statistiques": statistiques},
    )


@login_required
def inscription_marchand(request):
    marchand_existant = Marchand.objects.filter(proprietaire=request.user).first()
    if marchand_existant is not None:
        messages.info(request, "Vous avez déjà un compte marchand.")
        return redirect("reseau:espace_marchand")

    if request.method == "POST":
        formulaire = FormulaireInscriptionMarchand(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            marchand = formulaire.save()
            JournalAudit.journaliser(
                request.user, "Demande d'adhésion marchand", f"{marchand.nom} ({marchand.code_marchand})"
            )
            messages.success(
                request,
                "Votre demande de marchand a été créée. Elle doit être validée par la banque "
                "avant de pouvoir encaisser des paiements.",
            )
            return redirect("reseau:espace_marchand")
    else:
        formulaire = FormulaireInscriptionMarchand(utilisateur=request.user)

    return render(request, "reseau/inscription_marchand.html", {"formulaire": formulaire})


@login_required
def creer_demande(request):
    marchand = Marchand.objects.filter(proprietaire=request.user).first()
    if marchand is None:
        messages.info(request, "Créez d'abord votre compte marchand.")
        return redirect("reseau:inscription_marchand")
    if marchand.statut != Marchand.Statut.ACTIF:
        messages.error(request, "Votre compte marchand doit être actif pour créer une demande de paiement.")
        return redirect("reseau:espace_marchand")

    if request.method == "POST":
        formulaire = FormulaireDemandePaiement(request.POST)
        if formulaire.is_valid():
            montant = formulaire.cleaned_data["montant"]
            demande = DemandePaiementMarchand.objects.create(
                marchand=marchand,
                montant=montant,
                description=formulaire.cleaned_data["description"],
                date_expiration=timezone.now()
                + timedelta(minutes=formulaire.cleaned_data["duree"]),
            )
            JournalAudit.journaliser(
                request.user,
                "Création demande marchand",
                f"Demande {demande.pk} - {montant} FCFA - marchand {marchand.code_marchand}",
            )
            messages.success(request, "Demande de paiement créée. Partagez le QR code avec votre client.")
            return redirect("reseau:detail_demande", demande_id=demande.pk)
    else:
        formulaire = FormulaireDemandePaiement()

    return render(
        request,
        "reseau/creer_demande.html",
        {"formulaire": formulaire, "marchand": marchand},
    )


@login_required
def detail_demande(request, demande_id):
    demande = get_object_or_404(
        DemandePaiementMarchand.objects.select_related(
            "marchand", "marchand__proprietaire", "transaction", "payeur"
        ),
        pk=demande_id,
    )
    est_personnel = request.user.est_administrateur() or request.user.est_agent()
    if not est_personnel and demande.marchand.proprietaire_id != request.user.id:
        return HttpResponseForbidden("Cette demande ne vous appartient pas.")
    demande = _expirer_si_necessaire(demande)
    lien_paiement = request.build_absolute_uri(
        reverse("reseau:payer_demande", args=[demande.jeton])
    )
    return render(
        request,
        "reseau/detail_demande.html",
        {"demande": demande, "lien_paiement": lien_paiement},
    )


@login_required
@never_cache
def qr_demande(request, demande_id):
    demande = get_object_or_404(
        DemandePaiementMarchand.objects.select_related("marchand"), pk=demande_id
    )
    est_personnel = request.user.est_administrateur() or request.user.est_agent()
    if not est_personnel and demande.marchand.proprietaire_id != request.user.id:
        return HttpResponseForbidden("Ce QR code ne vous appartient pas.")
    demande = _expirer_si_necessaire(demande)
    if demande.statut != DemandePaiementMarchand.Statut.EN_ATTENTE:
        return HttpResponse("Cette demande n'est plus payable.", status=409, content_type="text/plain")

    lien = request.build_absolute_uri(reverse("reseau:payer_demande", args=[demande.jeton]))
    if qrcode is None:
        return HttpResponse(
            "Génération de QR indisponible : la bibliothèque « qrcode » est absente "
            "de l'environnement Python utilisé. Le lien de paiement reste utilisable.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    image = qrcode.make(lien, box_size=8, border=2)
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    reponse = HttpResponse(tampon.getvalue(), content_type="image/png")
    reponse["Cache-Control"] = "no-store"
    reponse["Content-Disposition"] = f'inline; filename="qr-demande-{demande.pk}.png"'
    return reponse


@login_required
def payer_demande(request, jeton):
    demande = get_object_or_404(
        DemandePaiementMarchand.objects.select_related(
            "marchand", "marchand__compte_reglement", "transaction"
        ),
        jeton=jeton,
    )
    demande = _expirer_si_necessaire(demande)
    est_marchand = demande.marchand.proprietaire_id == request.user.id
    regle = _frais_marchand()

    if demande.statut != DemandePaiementMarchand.Statut.EN_ATTENTE:
        return render(
            request,
            "reseau/payer_demande.html",
            {"demande": demande, "est_marchand": est_marchand, "regle": regle, "paiement_termine": True},
        )

    frais = calculer_frais(RegleFrais.Operation.PAIEMENT_MARCHAND, demande.montant)
    if request.method == "POST":
        if est_marchand:
            messages.error(request, "Vous ne pouvez pas payer votre propre demande marchand.")
            return redirect("reseau:payer_demande", jeton=demande.jeton)
        formulaire = FormulairePaiementDemande(request.POST, utilisateur=request.user)
        if formulaire.is_valid():
            compte = formulaire.cleaned_data["compte_bancaire"]
            try:
                transaction = demande.payer(compte, frais=frais, payeur=request.user)
            except ValidationError as erreur:
                messages.error(request, " ".join(erreur.messages))
            else:
                JournalAudit.journaliser(
                    request.user,
                    "Paiement marchand",
                    f"Demande {demande.pk} - {demande.montant} FCFA - marchand {demande.marchand.nom}",
                )
                try:
                    notifier(
                        demande.marchand.proprietaire,
                        "Paiement marchand reçu",
                        f"Un client a payé {demande.montant} FCFA via votre QR code "
                        f"(référence {transaction.reference}).",
                        type_notification="PAIEMENT",
                        niveau="SUCCES",
                        lien=f"/reseau/payer/{demande.jeton}/",
                    )
                    notifier(
                        request.user,
                        "Paiement marchand effectué",
                        f"Votre paiement de {demande.montant} FCFA à {demande.marchand.nom} a été accepté "
                        f"(frais : {frais} FCFA).",
                        type_notification="PAIEMENT",
                        niveau="SUCCES",
                        lien=f"/transactions/transactions/{transaction.pk}/recu.pdf",
                    )
                except Exception:  # noqa: BLE001
                    pass
                messages.success(request, "Paiement effectué. Le marchand a été crédité dans l'application.")
                return redirect("reseau:payer_demande", jeton=demande.jeton)
    else:
        formulaire = FormulairePaiementDemande(utilisateur=request.user)

    return render(
        request,
        "reseau/payer_demande.html",
        {
            "demande": demande,
            "formulaire": formulaire,
            "frais": frais,
            "total": demande.montant + frais,
            "regle": regle,
            "est_marchand": est_marchand,
            "paiement_termine": False,
        },
    )


@login_required
@require_POST
def annuler_demande(request, demande_id):
    marchand = get_object_or_404(Marchand, proprietaire=request.user)
    demande = get_object_or_404(DemandePaiementMarchand, pk=demande_id, marchand=marchand)
    if demande.statut != DemandePaiementMarchand.Statut.EN_ATTENTE:
        messages.error(request, "Seule une demande en attente peut être annulée.")
    else:
        demande.statut = DemandePaiementMarchand.Statut.ANNULEE
        demande.save(update_fields=["statut"])
        JournalAudit.journaliser(
            request.user, "Annulation demande marchand", f"Demande {demande.pk} - {demande.montant} FCFA"
        )
        messages.success(request, "La demande de paiement a été annulée.")
    return redirect("reseau:detail_demande", demande_id=demande.pk)


@role_requis("AGENT")
def espace_agent(request):
    profil = ProfilAgent.objects.filter(utilisateur=request.user, actif=True).first()
    if profil is None:
        messages.error(
            request,
            "Aucun profil agent actif n'est associé à votre compte. Contactez un administrateur.",
        )
        return redirect("comptes:tableau_de_bord")

    aujourdhui = timezone.localdate()
    debut_jour = timezone.make_aware(datetime.combine(aujourdhui, time.min))
    fin_jour = timezone.make_aware(datetime.combine(aujourdhui + timedelta(days=1), time.min))
    operations_jour = OperationAgent.objects.filter(
        agent=profil,
        date_operation__gte=debut_jour,
        date_operation__lt=fin_jour,
    )
    total_jour = operations_jour.filter(statut=OperationAgent.Statut.REUSSIE).aggregate(
        total=Sum("montant")
    )["total"] or Decimal("0")
    cle = _cle_idempotence(request)

    if request.method == "POST":
        formulaire = FormulaireOperationAgent(request.POST)
        if formulaire.is_valid():
            try:
                operation = OperationAgent.effectuer(
                    profil,
                    formulaire.compte,
                    formulaire.cleaned_data["montant"],
                    formulaire.cleaned_data["type_operation"],
                    note=formulaire.cleaned_data["note"],
                    cree_par=request.user,
                    cle_idempotence=cle,
                )
            except ValidationError as erreur:
                messages.error(request, " ".join(erreur.messages))
            else:
                JournalAudit.journaliser(
                    request.user,
                    "Opération de caisse agent",
                    f"{operation.reference} - {operation.get_type_operation_display()} "
                    f"{operation.montant} FCFA - compte {operation.compte.numero_compte}",
                )
                try:
                    notifier(
                        operation.client,
                        "Opération de caisse effectuée",
                        f"{operation.get_type_operation_display()} de {operation.montant} FCFA "
                        f"auprès de l'agence {profil.nom_agence}.",
                        type_notification="TRANSACTION",
                        niveau="SUCCES",
                        lien=f"/transactions/transactions/{operation.transaction_id}/recu.pdf"
                        if operation.transaction_id
                        else "",
                    )
                except Exception:  # noqa: BLE001
                    pass
                messages.success(request, f"Opération {operation.reference} enregistrée.")
                return redirect("reseau:espace_agent")
    else:
        formulaire = FormulaireOperationAgent()

    operations = profil.operations.select_related("client", "compte", "transaction").order_by(
        "-date_operation"
    )[:25]
    return render(
        request,
        "reseau/espace_agent.html",
        {
            "profil": profil,
            "formulaire": formulaire,
            "cle_idempotence": cle,
            "operations": operations,
            "operations_jour": operations_jour,
            "total_jour": total_jour,
            "reste_jour": max(profil.plafond_journalier - total_jour, Decimal("0")),
        },
    )
