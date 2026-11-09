"""
Vues du back-office bancaire : tableau de bord statistique, supervision
des clients et des transactions, journal d'audit, export CSV.

Accessible uniquement aux agents et administrateurs (voir decorateurs).
"""

import csv
import logging
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import make_aware
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from comptes.decorateurs import admin_requis, agent_ou_admin_requis
from comptes.models import JournalAudit, Utilisateur
from comptes.services_notifications import notifier
from kyc.models import DocumentKYC, DossierKYC, HistoriqueKYC
from paiement_mobile.models import PaiementMobile
from risques.models import AlerteFraude
from transactions.forms import FormulaireOuvertureCompte
from transactions.models import CompteBancaire, Transaction

from .forms import FormulaireCreationClient

logger = logging.getLogger(__name__)


def _bornes_periode(nb_jours):
    """Bornes datetime conscientes, compatibles avec MySQL sans tables de fuseaux."""
    nb_jours = max(1, min(int(nb_jours), 365))
    aujourdhui = timezone.localdate()
    premier_jour = aujourdhui - timedelta(days=nb_jours - 1)
    debut = make_aware(datetime.combine(premier_jour, time.min))
    fin_exclue = make_aware(datetime.combine(aujourdhui + timedelta(days=1), time.min))
    jours = [premier_jour + timedelta(days=index) for index in range(nb_jours)]
    return debut, fin_exclue, jours


def _periode_demandee(request):
    valeurs = {"7": 7, "30": 30, "90": 90, "365": 365}
    cle = request.GET.get("periode", "30")
    nb_jours = valeurs.get(cle, 30)
    debut, fin_exclue, jours = _bornes_periode(nb_jours)
    return cle, nb_jours, debut, fin_exclue, jours


def _fenetre_temps_reel(minutes=60, pas_minutes=5):
    """Fenêtre glissante alignée sur des tranches stables de 5 minutes.

    L'alignement évite que les libellés bougent à chaque interrogation : le
    graphique se met à jour sans scintillement. La dernière tranche est
    partielle et contient les opérations les plus récentes.
    """
    maintenant_local = timezone.localtime()
    fin = maintenant_local.replace(second=0, microsecond=0)
    fin -= timedelta(minutes=fin.minute % pas_minutes)
    fin += timedelta(minutes=pas_minutes)
    debut = fin - timedelta(minutes=minutes)
    libelles = [
        timezone.localtime(debut + timedelta(minutes=index * pas_minutes)).strftime("%H:%M")
        for index in range(minutes // pas_minutes)
    ]
    return debut, fin, libelles


def _index_tranche(horodatage, debut, pas_minutes, nombre_tranches):
    index = int((horodatage - debut).total_seconds() // (pas_minutes * 60))
    if 0 <= index < nombre_tranches:
        return index
    return None


@agent_ou_admin_requis
@never_cache
def activite_temps_reel(request):
    """JSON de l'activité récente, interrogé par le tableau de bord admin."""
    pas_minutes = 5
    debut, fin, libelles = _fenetre_temps_reel(minutes=60, pas_minutes=pas_minutes)
    nombre_tranches = len(libelles)
    transactions_par_tranche = [0] * nombre_tranches
    paiements_par_tranche = [0] * nombre_tranches
    volume_transactions = 0.0

    for date_operation, montant in Transaction.objects.filter(
        date_transaction__gte=debut,
        date_transaction__lt=fin,
        statut=Transaction.Statut.REUSSIE,
    ).values_list("date_transaction", "montant"):
        index = _index_tranche(date_operation, debut, pas_minutes, nombre_tranches)
        if index is not None:
            transactions_par_tranche[index] += 1
            volume_transactions += float(montant or 0)

    for date_operation in PaiementMobile.objects.filter(
        date_creation__gte=debut,
        date_creation__lt=fin,
        statut=PaiementMobile.Statut.REUSSI,
    ).values_list("date_creation", flat=True):
        index = _index_tranche(date_operation, debut, pas_minutes, nombre_tranches)
        if index is not None:
            paiements_par_tranche[index] += 1

    reponse = JsonResponse(
        {
            "labels": libelles,
            "transactions": transactions_par_tranche,
            "paiements_mobiles": paiements_par_tranche,
            "total_transactions": sum(transactions_par_tranche),
            "total_paiements_mobiles": sum(paiements_par_tranche),
            "volume_transactions": round(volume_transactions, 2),
            "fenetre_minutes": 60,
            "pas_minutes": pas_minutes,
            "horodatage": timezone.localtime().strftime("%H:%M:%S"),
        }
    )
    reponse["Cache-Control"] = "no-store"
    return reponse


def _cellule_csv(valeur):
    """Neutralise les formules tableur dans les exports CSV."""
    texte = "" if valeur is None else str(valeur)
    if texte.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + texte
    return texte


@agent_ou_admin_requis
def tableau_de_bord(request):
    nb_clients = Utilisateur.objects.filter(role=Utilisateur.Role.CLIENT).count()
    nb_comptes = CompteBancaire.objects.count()
    volume_transactions = Transaction.objects.filter(statut=Transaction.Statut.REUSSIE).count()
    montant_total_circulation = CompteBancaire.objects.aggregate(total=Sum("solde"))["total"] or 0
    nb_comptes_bloques = CompteBancaire.objects.filter(statut=CompteBancaire.Statut.BLOQUE).count()
    nb_paiements_mobiles = PaiementMobile.objects.count()

    repartition_par_type = list(
        Transaction.objects.filter(statut=Transaction.Statut.REUSSIE)
        .values("type_transaction")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    libelles_type = dict(Transaction.Type.choices)
    for ligne in repartition_par_type:
        ligne["libelle"] = libelles_type.get(ligne["type_transaction"], ligne["type_transaction"])

    dernieres_transactions = Transaction.objects.select_related("compte_source", "compte_destination").order_by(
        "-date_transaction"
    )[:10]

    # Série des 14 derniers jours, calculée en Python du fait de l'absence
    # possible des tables de fuseaux horaires MySQL (CONVERT_TZ).
    debut, fin_exclue, jours = _bornes_periode(14)
    serie_transactions = Transaction.objects.filter(
        date_transaction__gte=debut,
        date_transaction__lt=fin_exclue,
        statut=Transaction.Statut.REUSSIE,
    ).values_list("date_transaction", "montant", "frais", "type_transaction")
    volumes_par_jour = [0.0 for _ in jours]
    nb_par_jour = [0 for _ in jours]
    volume_periode = 0
    frais_periode = 0
    for date_operation, montant, frais, _type in serie_transactions:
        index = (timezone.localtime(date_operation).date() - jours[0]).days
        if 0 <= index < len(jours):
            volumes_par_jour[index] += float(montant or 0)
            nb_par_jour[index] += 1
        volume_periode += montant or 0
        frais_periode += frais or 0

    debut_30, fin_30, _ = _bornes_periode(30)
    paiements_recents = PaiementMobile.objects.filter(date_creation__gte=debut_30, date_creation__lt=fin_30)
    nb_mobile_periode = paiements_recents.count()
    nb_mobile_reussi = paiements_recents.filter(statut=PaiementMobile.Statut.REUSSI).count()
    volume_mobile = (
        paiements_recents.filter(statut=PaiementMobile.Statut.REUSSI).aggregate(total=Sum("montant"))["total"]
        or 0
    )
    nb_nouveaux_clients = Utilisateur.objects.filter(
        role=Utilisateur.Role.CLIENT, date_creation__gte=debut_30
    ).count()

    contexte = {
        "nb_clients": nb_clients,
        "nb_comptes": nb_comptes,
        "volume_transactions": volume_transactions,
        "montant_total_circulation": montant_total_circulation,
        "nb_comptes_bloques": nb_comptes_bloques,
        "nb_paiements_mobiles": nb_paiements_mobiles,
        "repartition_par_type": repartition_par_type,
        "dernieres_transactions": dernieres_transactions,
        "labels_jours": [jour.strftime("%d/%m") for jour in jours],
        "volumes_par_jour": volumes_par_jour,
        "nb_par_jour": nb_par_jour,
        "volume_periode": volume_periode,
        "frais_periode": frais_periode,
        "nb_mobile_periode": nb_mobile_periode,
        "nb_mobile_reussi": nb_mobile_reussi,
        "volume_mobile": volume_mobile,
        "taux_mobile": round(100 * nb_mobile_reussi / nb_mobile_periode, 1) if nb_mobile_periode else 0,
        "nb_nouveaux_clients": nb_nouveaux_clients,
        "nb_alertes_ouvertes": AlerteFraude.objects.filter(
            statut__in=[AlerteFraude.Statut.NOUVELLE, AlerteFraude.Statut.EN_REVUE]
        ).count(),
        "nb_alertes_critiques": AlerteFraude.objects.filter(
            niveau=AlerteFraude.Niveau.CRITIQUE,
            statut__in=[AlerteFraude.Statut.NOUVELLE, AlerteFraude.Statut.EN_REVUE],
        ).count(),
        "nb_kyc_en_attente": DossierKYC.objects.filter(statut=DossierKYC.Statut.EN_ATTENTE).count(),
    }
    return render(request, "administration/tableau_de_bord.html", contexte)


@admin_requis
def creer_client(request):
    """Création d'un client et de son premier compte bancaire par l'administrateur."""
    if request.method == "POST":
        formulaire = FormulaireCreationClient(request.POST)
        if formulaire.is_valid():
            try:
                with transaction.atomic():
                    client = formulaire.save()
                    compte = CompteBancaire.objects.create(
                        proprietaire=client,
                        type_compte=formulaire.cleaned_data["type_compte"],
                        devise=formulaire.cleaned_data["devise"],
                    )
                    JournalAudit.journaliser(
                        request.user,
                        "Création de client",
                        f"{client.username} — compte n° {compte.numero_compte} ({compte.code_devise})",
                        getattr(request, "adresse_ip_client", None),
                    )
                    notifier(
                        client,
                        "Bienvenue à Ma Banque",
                        f"Votre compte client a été créé par la banque. Identifiant : {client.username}. "
                        f"Votre premier compte n° {compte.numero_compte} ({compte.code_devise}) est actif.",
                        type_notification="SYSTEME",
                        niveau="SUCCES",
                        lien=f"/operations/comptes/{compte.pk}/",
                    )
            except Exception:  # noqa: BLE001 - on informe l'admin sans exposer la trace
                logger.exception("Échec de la création d'un client par l'administrateur.")
                messages.error(
                    request,
                    "La création du client a échoué. Vérifiez les informations puis réessayez.",
                )
            else:
                messages.success(
                    request,
                    f"Client {client.username} créé avec le compte n° {compte.numero_compte}. "
                    "Communiquez-lui son mot de passe par un canal sécurisé.",
                )
                return redirect("administration:detail_client", client_id=client.id)
    else:
        formulaire = FormulaireCreationClient()

    return render(
        request,
        "administration/creer_client.html",
        {"formulaire": formulaire},
    )


@agent_ou_admin_requis
def supervision_clients(request):
    recherche = request.GET.get("q", "").strip()
    clients = Utilisateur.objects.filter(role=Utilisateur.Role.CLIENT).order_by("-date_creation")
    if recherche:
        clients = clients.filter(
            models_q_recherche(recherche)
        )
    return render(request, "administration/supervision_clients.html", {"clients": clients, "recherche": recherche})


def models_q_recherche(recherche):
    from django.db.models import Q

    return (
        Q(username__icontains=recherche)
        | Q(first_name__icontains=recherche)
        | Q(last_name__icontains=recherche)
        | Q(email__icontains=recherche)
        | Q(telephone__icontains=recherche)
    )


@agent_ou_admin_requis
def detail_client(request, client_id):
    client = get_object_or_404(Utilisateur, id=client_id, role=Utilisateur.Role.CLIENT)
    comptes = client.comptes_bancaires.all()
    return render(
        request,
        "administration/detail_client.html",
        {
            "client": client,
            "comptes": comptes,
            "formulaire_compte": FormulaireOuvertureCompte(),
        },
    )


@admin_requis
@require_POST
def ouvrir_compte_client(request, client_id):
    """Ouverture d'un compte supplémentaire pour un client existant."""
    client = get_object_or_404(Utilisateur, id=client_id, role=Utilisateur.Role.CLIENT)
    formulaire = FormulaireOuvertureCompte(request.POST)
    if not formulaire.is_valid():
        for erreurs in formulaire.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
        return redirect("administration:detail_client", client_id=client.id)
    compte = CompteBancaire.objects.create(
        proprietaire=client,
        type_compte=formulaire.cleaned_data["type_compte"],
        devise=formulaire.cleaned_data["devise"],
    )
    JournalAudit.journaliser(
        request.user,
        "Ouverture de compte pour un client",
        f"{client.username} — compte n° {compte.numero_compte} ({compte.code_devise})",
        getattr(request, "adresse_ip_client", None),
    )
    notifier(
        client,
        "Nouveau compte ouvert",
        f"La banque a ouvert un nouveau compte n° {compte.numero_compte} "
        f"({compte.get_type_compte_display()}, {compte.code_devise}) à votre nom.",
        type_notification="SYSTEME",
        niveau="SUCCES",
        lien=f"/operations/comptes/{compte.pk}/",
    )
    messages.success(request, f"Compte n° {compte.numero_compte} ouvert pour {client.username}.")
    return redirect("administration:detail_client", client_id=client.id)


@admin_requis
@require_POST
def bloquer_compte(request, compte_id):
    compte = get_object_or_404(CompteBancaire, id=compte_id)
    motif = request.POST.get("motif", "Fraude suspectée").strip() or "Fraude suspectée"
    compte.statut = CompteBancaire.Statut.BLOQUE
    compte.motif_blocage = motif[:255]
    compte.save(update_fields=["statut", "motif_blocage", "date_mise_a_jour"])
    JournalAudit.journaliser(request.user, "Blocage de compte", f"{compte.numero_compte} - motif : {motif}")
    notifier(
        compte.proprietaire,
        "Compte bancaire bloqué",
        f"Votre compte {compte.numero_compte} a été bloqué. Motif : {motif}. Contactez votre agence.",
        type_notification="SECURITE",
        niveau="CRITIQUE",
        lien="/comptes/",
    )
    messages.success(request, f"Le compte {compte.numero_compte} a été bloqué.")
    return redirect("administration:detail_client", client_id=compte.proprietaire_id)


@admin_requis
@require_POST
def debloquer_compte(request, compte_id):
    compte = get_object_or_404(CompteBancaire, id=compte_id)
    compte.statut = CompteBancaire.Statut.ACTIF
    compte.motif_blocage = ""
    compte.save(update_fields=["statut", "motif_blocage", "date_mise_a_jour"])
    JournalAudit.journaliser(request.user, "Déblocage de compte", compte.numero_compte)
    notifier(
        compte.proprietaire,
        "Compte bancaire réactivé",
        f"Votre compte {compte.numero_compte} est de nouveau actif.",
        type_notification="SECURITE",
        niveau="SUCCES",
        lien="/comptes/",
    )
    messages.success(request, f"Le compte {compte.numero_compte} a été débloqué.")
    return redirect("administration:detail_client", client_id=compte.proprietaire_id)


@agent_ou_admin_requis
def supervision_transactions(request):
    transactions = Transaction.objects.select_related("compte_source", "compte_destination").order_by(
        "-date_transaction"
    )

    type_filtre = request.GET.get("type_transaction")
    if type_filtre:
        transactions = transactions.filter(type_transaction=type_filtre)

    statut_filtre = request.GET.get("statut")
    if statut_filtre:
        transactions = transactions.filter(statut=statut_filtre)

    return render(
        request,
        "administration/supervision_transactions.html",
        {
            "transactions": transactions[:200],
            "types_transaction": Transaction.Type.choices,
            "statuts_transaction": Transaction.Statut.choices,
        },
    )


@agent_ou_admin_requis
def exporter_transactions_csv(request):
    reponse = HttpResponse(content_type="text/csv; charset=utf-8")
    reponse["Content-Disposition"] = f'attachment; filename="transactions_{timezone.now():%Y%m%d_%H%M}.csv"'
    reponse.write("\ufeff")

    ecrivain = csv.writer(reponse, delimiter=";")
    ecrivain.writerow(["ID", "Type", "Montant", "Frais", "Compte source", "Compte destination", "Statut", "Date"])
    for transaction_item in Transaction.objects.select_related("compte_source", "compte_destination").order_by(
        "-date_transaction"
    ):
        ecrivain.writerow([
            transaction_item.id,
            _cellule_csv(transaction_item.get_type_transaction_display()),
            transaction_item.montant,
            transaction_item.frais,
            _cellule_csv(transaction_item.compte_source.numero_compte if transaction_item.compte_source else ""),
            _cellule_csv(transaction_item.compte_destination.numero_compte if transaction_item.compte_destination else ""),
            _cellule_csv(transaction_item.get_statut_display()),
            transaction_item.date_transaction.strftime("%d/%m/%Y %H:%M"),
        ])
    JournalAudit.journaliser(request.user, "Export CSV — transactions", "Export complet de la supervision")
    return reponse


# ---------------------------------------------------------------------------
# Rapports consolidés et exports
# ---------------------------------------------------------------------------
@agent_ou_admin_requis
def rapports(request):
    cle_periode, nb_jours, debut, fin_exclue, jours = _periode_demandee(request)

    transactions = Transaction.objects.filter(
        date_transaction__gte=debut, date_transaction__lt=fin_exclue
    )
    reussies = transactions.filter(statut=Transaction.Statut.REUSSIE)
    nb_transactions = transactions.count()
    nb_reussies = reussies.count()
    nb_echecs = transactions.filter(
        statut__in=[Transaction.Statut.ECHOUEE, Transaction.Statut.ANNULEE]
    ).count()
    volumes = reussies.aggregate(volume=Sum("montant"), frais=Sum("frais"))
    volume_total = volumes["volume"] or 0
    frais_total = volumes["frais"] or 0

    volumes_par_jour = [0.0 for _ in jours]
    nb_par_jour = [0 for _ in jours]
    par_type = {}
    par_canal = {}
    libelles_type = dict(Transaction.Type.choices)
    libelles_canal = dict(Transaction.Canal.choices)
    for date_operation, montant, frais, type_transaction, canal in reussies.values_list(
        "date_transaction", "montant", "frais", "type_transaction", "canal"
    ):
        index = (timezone.localtime(date_operation).date() - jours[0]).days
        if 0 <= index < len(jours):
            volumes_par_jour[index] += float(montant or 0)
            nb_par_jour[index] += 1
        ligne_type = par_type.setdefault(
            type_transaction, {"libelle": libelles_type.get(type_transaction, type_transaction), "nb": 0, "volume": 0}
        )
        ligne_type["nb"] += 1
        ligne_type["volume"] += montant or 0
        ligne_canal = par_canal.setdefault(
            canal, {"libelle": libelles_canal.get(canal, canal), "nb": 0, "volume": 0}
        )
        ligne_canal["nb"] += 1
        ligne_canal["volume"] += montant or 0

    par_type = sorted(par_type.values(), key=lambda ligne: ligne["volume"], reverse=True)
    par_canal = sorted(par_canal.values(), key=lambda ligne: ligne["volume"], reverse=True)
    libelles_statut = dict(Transaction.Statut.choices)
    repartition_statuts = [
        {"libelle": libelles_statut.get(ligne["statut"], ligne["statut"]), "nb": ligne["nb"]}
        for ligne in transactions.values("statut").annotate(nb=Count("id")).order_by("-nb")
    ]

    # Paiements mobiles de la période (simulés et libellés comme tels).
    mobiles = PaiementMobile.objects.filter(date_creation__gte=debut, date_creation__lt=fin_exclue)
    mobiles_reussis = mobiles.filter(statut=PaiementMobile.Statut.REUSSI)
    volume_mobile = mobiles_reussis.aggregate(total=Sum("montant"))["total"] or 0
    frais_mobile = mobiles_reussis.aggregate(total=Sum("frais"))["total"] or 0
    nb_mobiles = mobiles.count()
    nb_mobiles_reussis = mobiles_reussis.count()
    par_operateur = {
        code: {"libelle": libelle, "nb": 0, "nb_reussi": 0, "volume": 0}
        for code, libelle in PaiementMobile.Operateur.choices
    }
    for ligne in mobiles.values("operateur", "statut").annotate(
        nb=Count("id"), volume=Sum("montant")
    ):
        operateur = par_operateur.setdefault(
            ligne["operateur"], {"libelle": ligne["operateur"], "nb": 0, "nb_reussi": 0, "volume": 0}
        )
        operateur["nb"] += ligne["nb"]
        if ligne["statut"] == PaiementMobile.Statut.REUSSI:
            operateur["nb_reussi"] += ligne["nb"]
            operateur["volume"] += ligne["volume"] or 0
    par_operateur = list(par_operateur.values())

    libelles_type_compte = dict(CompteBancaire.TypeCompte.choices)
    repartition_comptes = [
        {
            "libelle": libelles_type_compte.get(ligne["type_compte"], ligne["type_compte"]),
            "nb": ligne["nb"],
            "solde": ligne["solde"] or 0,
        }
        for ligne in CompteBancaire.objects.values("type_compte").annotate(nb=Count("id"), solde=Sum("solde"))
    ]

    alertes = AlerteFraude.objects.filter(date_creation__gte=debut, date_creation__lt=fin_exclue)
    libelles_niveau = dict(AlerteFraude.Niveau.choices)
    repartition_alertes = [
        {"libelle": libelles_niveau.get(ligne["niveau"], ligne["niveau"]), "nb": ligne["nb"]}
        for ligne in alertes.values("niveau").annotate(nb=Count("id")).order_by("-nb")
    ]

    kyc_par_statut = [
        {"libelle": libelle, "nb": DossierKYC.objects.filter(statut=code).count()}
        for code, libelle in DossierKYC.Statut.choices
    ]

    contexte = {
        "cle_periode": cle_periode,
        "nb_jours": nb_jours,
        "debut": debut,
        "fin": fin_exclue - timedelta(days=1),
        "labels_jours": [jour.strftime("%d/%m") for jour in jours],
        "volumes_par_jour": volumes_par_jour,
        "nb_par_jour": nb_par_jour,
        "nb_transactions": nb_transactions,
        "nb_reussies": nb_reussies,
        "nb_echecs": nb_echecs,
        "taux_reussite": round(100 * nb_reussies / nb_transactions, 1) if nb_transactions else 0,
        "volume_total": volume_total,
        "frais_total": frais_total,
        "par_type": par_type,
        "par_canal": par_canal,
        "repartition_statuts": repartition_statuts,
        "nb_mobiles": nb_mobiles,
        "nb_mobiles_reussis": nb_mobiles_reussis,
        "volume_mobile": volume_mobile,
        "frais_mobile": frais_mobile,
        "par_operateur": par_operateur,
        "repartition_comptes": repartition_comptes,
        "repartition_alertes": repartition_alertes,
        "kyc_par_statut": kyc_par_statut,
    }
    return render(request, "administration/rapports.html", contexte)


@agent_ou_admin_requis
def exporter_rapport_csv(request):
    cle_periode, nb_jours, debut, fin_exclue, _jours = _periode_demandee(request)
    reponse = HttpResponse(content_type="text/csv; charset=utf-8")
    reponse["Content-Disposition"] = (
        f'attachment; filename="rapport_activite_{cle_periode}j_{timezone.now():%Y%m%d_%H%M}.csv"'
    )
    reponse.write("\ufeff")

    ecrivain = csv.writer(reponse, delimiter=";")
    ecrivain.writerow(
        ["Référence", "Date", "Type", "Canal", "Montant", "Frais", "Source", "Destination", "Statut", "Description"]
    )
    transactions = (
        Transaction.objects.filter(date_transaction__gte=debut, date_transaction__lt=fin_exclue)
        .select_related("compte_source", "compte_destination")
        .order_by("-date_transaction")
    )
    for operation in transactions.iterator():
        ecrivain.writerow([
            _cellule_csv(operation.reference),
            timezone.localtime(operation.date_transaction).strftime("%d/%m/%Y %H:%M"),
            _cellule_csv(operation.get_type_transaction_display()),
            _cellule_csv(operation.get_canal_display()),
            operation.montant,
            operation.frais,
            _cellule_csv(operation.compte_source.numero_compte if operation.compte_source else ""),
            _cellule_csv(operation.compte_destination.numero_compte if operation.compte_destination else ""),
            _cellule_csv(operation.get_statut_display()),
            _cellule_csv(operation.description),
        ])
    JournalAudit.journaliser(
        request.user, "Export CSV — rapport d'activité", f"Période : {nb_jours} jours"
    )
    return reponse


@admin_requis
def journal_audit(request):
    recherche = request.GET.get("q", "").strip()
    entrees = JournalAudit.objects.select_related("utilisateur").order_by("-date_action")
    if recherche:
        entrees = entrees.filter(
            Q(action__icontains=recherche)
            | Q(details__icontains=recherche)
            | Q(utilisateur__username__icontains=recherche)
        )
    verification = JournalAudit.verifier_chaine()
    pagination = Paginator(entrees, 100)
    page = pagination.get_page(request.GET.get("page"))
    return render(
        request,
        "administration/journal_audit.html",
        {
            "page_obj": page,
            "entrees": page.object_list,
            "recherche": recherche,
            "total_entrees": pagination.count,
            "integrite_valide": verification[0],
            "integrite_total": verification[1],
            "integrite_anomalie": verification[2],
        },
    )


@admin_requis
def exporter_journal_audit_csv(request):
    reponse = HttpResponse(content_type="text/csv; charset=utf-8")
    reponse["Content-Disposition"] = f'attachment; filename="journal_audit_{timezone.now():%Y%m%d_%H%M}.csv"'
    reponse.write("\ufeff")
    ecrivain = csv.writer(reponse, delimiter=";")
    ecrivain.writerow(["Date", "Utilisateur", "Action", "Détails", "Adresse IP", "Empreinte"])
    for entree in JournalAudit.objects.select_related("utilisateur").order_by("-date_action").iterator():
        ecrivain.writerow([
            timezone.localtime(entree.date_action).strftime("%d/%m/%Y %H:%M:%S"),
            _cellule_csv(entree.utilisateur.username if entree.utilisateur else "anonyme"),
            _cellule_csv(entree.action),
            _cellule_csv(entree.details),
            entree.adresse_ip or "",
            entree.empreinte,
        ])
    JournalAudit.journaliser(request.user, "Export CSV — journal d'audit")
    return reponse


@admin_requis
def gestion_utilisateurs_internes(request):
    """Vue simple listant agents et administrateurs (la création se fait via /admin natif)."""
    utilisateurs_internes = Utilisateur.objects.exclude(role=Utilisateur.Role.CLIENT).order_by("role", "username")
    return render(
        request, "administration/utilisateurs_internes.html", {"utilisateurs_internes": utilisateurs_internes}
    )


# ---------------------------------------------------------------------------
# Instruction des dossiers KYC
# ---------------------------------------------------------------------------
@agent_ou_admin_requis
def kyc_dossiers(request):
    statut = request.GET.get("statut", "").strip()
    recherche = request.GET.get("q", "").strip()
    dossiers = DossierKYC.objects.select_related("utilisateur", "traite_par").order_by(
        "statut", "-date_soumission", "-date_creation"
    )
    if statut:
        dossiers = dossiers.filter(statut=statut)
    if recherche:
        dossiers = dossiers.filter(
            Q(utilisateur__username__icontains=recherche)
            | Q(utilisateur__first_name__icontains=recherche)
            | Q(utilisateur__last_name__icontains=recherche)
            | Q(utilisateur__telephone__icontains=recherche)
            | Q(numero_piece__icontains=recherche)
        )
    return render(
        request,
        "administration/kyc_dossiers.html",
        {
            "dossiers": dossiers[:300],
            "statut": statut,
            "recherche": recherche,
            "statuts": DossierKYC.Statut.choices,
            "nb_en_attente": DossierKYC.objects.filter(statut=DossierKYC.Statut.EN_ATTENTE).count(),
        },
    )


@agent_ou_admin_requis
def kyc_detail(request, dossier_id):
    dossier = get_object_or_404(
        DossierKYC.objects.select_related("utilisateur", "traite_par"), id=dossier_id
    )
    return render(
        request,
        "administration/kyc_detail.html",
        {
            "dossier": dossier,
            "documents": dossier.documents.filter(est_actif=True),
            "historique": dossier.historique.select_related("acteur")[:30],
        },
    )


@agent_ou_admin_requis
@require_POST
def kyc_decider(request, dossier_id):
    dossier = get_object_or_404(DossierKYC.objects.select_related("utilisateur"), id=dossier_id)
    decision = request.POST.get("decision")
    commentaire = (request.POST.get("commentaire") or "").strip()

    if dossier.statut not in (DossierKYC.Statut.EN_ATTENTE, DossierKYC.Statut.REJETE):
        messages.error(request, "Ce dossier n'est pas dans un état permettant une décision.")
        return redirect("administration:kyc_detail", dossier_id=dossier.id)

    if decision == "APPROUVER":
        if not dossier.documents_obligatoires_presents():
            messages.error(request, "Impossible d'approuver : les deux faces de la pièce d'identité sont absentes.")
            return redirect("administration:kyc_detail", dossier_id=dossier.id)
        ancien = dossier.statut
        dossier.statut = DossierKYC.Statut.APPROUVE
        dossier.motif_rejet = ""
        dossier.date_traitement = timezone.now()
        dossier.traite_par = request.user
        dossier.save(update_fields=["statut", "motif_rejet", "date_traitement", "traite_par", "date_mise_a_jour"])
        dossier.documents.filter(est_actif=True).update(statut=DocumentKYC.Statut.VALIDE, motif_rejet="")
        dossier.utilisateur.identite_verifiee = True
        dossier.utilisateur.save(update_fields=["identite_verifiee"])
        HistoriqueKYC.objects.create(
            dossier=dossier,
            acteur=request.user,
            action=HistoriqueKYC.Action.APPROBATION,
            ancien_statut=ancien,
            nouveau_statut=dossier.statut,
            commentaire=commentaire or "Dossier approuvé.",
        )
        notifier(
            dossier.utilisateur,
            "Identité vérifiée",
            "Votre dossier d'identité a été approuvé. Vous pouvez désormais ouvrir de nouveaux comptes.",
            type_notification="KYC",
            niveau="SUCCES",
            lien="/kyc/",
        )
        JournalAudit.journaliser(request.user, "KYC — dossier approuvé", f"Client {dossier.utilisateur}")
        messages.success(request, "Dossier approuvé.")
    elif decision == "REJETER":
        if not commentaire:
            messages.error(request, "Un motif de rejet est obligatoire.")
            return redirect("administration:kyc_detail", dossier_id=dossier.id)
        ancien = dossier.statut
        dossier.statut = DossierKYC.Statut.REJETE
        dossier.motif_rejet = commentaire
        dossier.date_traitement = timezone.now()
        dossier.traite_par = request.user
        dossier.save(update_fields=["statut", "motif_rejet", "date_traitement", "traite_par", "date_mise_a_jour"])
        dossier.documents.filter(est_actif=True).update(statut=DocumentKYC.Statut.REJETE, motif_rejet=commentaire[:255])
        dossier.utilisateur.identite_verifiee = False
        dossier.utilisateur.save(update_fields=["identite_verifiee"])
        HistoriqueKYC.objects.create(
            dossier=dossier,
            acteur=request.user,
            action=HistoriqueKYC.Action.REJET,
            ancien_statut=ancien,
            nouveau_statut=dossier.statut,
            commentaire=commentaire,
        )
        notifier(
            dossier.utilisateur,
            "Dossier d'identité rejeté",
            f"Votre dossier a été rejeté. Motif : {commentaire}. Vous pouvez le corriger et le soumettre à nouveau.",
            type_notification="KYC",
            niveau="ATTENTION",
            lien="/kyc/",
        )
        JournalAudit.journaliser(request.user, "KYC — dossier rejeté", f"Client {dossier.utilisateur} — {commentaire}")
        messages.success(request, "Dossier rejeté et client notifié.")
    else:
        messages.error(request, "Décision inconnue.")
    return redirect("administration:kyc_dossiers")
