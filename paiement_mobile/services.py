"""
Cycle de vie fiable d'un paiement mobile.

Règles appliquées :
- la base Django est la seule source de vérité des soldes ;
- un retrait est débité immédiatement mais tracé EN_ATTENTE, puis remboursé
  de façon idempotente si l'opérateur échoue ;
- une recharge n'est créditée qu'après confirmation REUSSI ;
- les transitions de statut sont verrouillées et ne peuvent se produire
  qu'une seule fois, même en cas de polling concurrent ;
- les montants sont des Decimal, jamais des floats.
"""

import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import F
from django.utils import timezone

from comptes.services_notifications import envoyer_sms_simule, notifier
from transactions.models import CompteBancaire, Transaction
from transactions.services import calculer_frais, nouvelle_cle_idempotence

from . import client_api
from .models import PaiementMobile
from .providers import get_operateur, valider_numero

logger = logging.getLogger(__name__)


def _notifier_resultat(paiement):
    """Notifie sans jamais faire échouer une opération financière déjà validée."""
    try:
        _envoyer_notifications(paiement)
    except Exception:  # noqa: BLE001 - la notification ne doit pas annuler un mouvement d'argent
        logger.exception("Notification de paiement mobile non envoyée (paiement %s).", paiement.pk)


def _envoyer_notifications(paiement):
    if paiement.statut == PaiementMobile.Statut.REUSSI:
        if paiement.type_operation == PaiementMobile.TypeOperation.RECHARGE:
            message = (
                f"Recharge de {paiement.montant} FCFA confirmée sur le compte "
                f"{paiement.compte_bancaire.numero_compte}."
            )
        else:
            message = (
                f"Retrait mobile de {paiement.montant} FCFA vers {paiement.get_operateur_display()} "
                f"({paiement.numero_telephone}) confirmé."
            )
        notifier(
            paiement.client,
            "Paiement mobile réussi",
            message,
            type_notification="PAIEMENT",
            niveau="SUCCES",
            lien=f"/paiement-mobile/statut/{paiement.pk}/",
        )
        envoyer_sms_simule(paiement.client, message)
    elif paiement.statut in (PaiementMobile.Statut.ECHOUE, PaiementMobile.Statut.ANNULE):
        message = (
            f"L'opération mobile {paiement.reference_transaction} a échoué"
            + (f" : {paiement.message_erreur}" if paiement.message_erreur else ".")
        )
        notifier(
            paiement.client,
            "Paiement mobile échoué",
            message
            + (
                " Le montant débité a été remboursé."
                if paiement.transaction_remboursement_id
                else ""
            ),
            type_notification="PAIEMENT",
            niveau="ATTENTION",
            lien=f"/paiement-mobile/statut/{paiement.pk}/",
        )
        envoyer_sms_simule(paiement.client, message)


@db_transaction.atomic
def rembourser_paiement(paiement_id, motif="Opération annulée"):
    """Rembourse un retrait mobile non abouti, une seule fois."""
    paiement = PaiementMobile.objects.select_for_update().get(pk=paiement_id)
    if paiement.transaction_remboursement_id:
        return paiement
    if paiement.type_operation != PaiementMobile.TypeOperation.RETRAIT_MOBILE:
        return paiement
    if paiement.transaction_bancaire_id is None:
        return paiement

    debit = Transaction.objects.select_for_update().filter(pk=paiement.transaction_bancaire_id).first()
    if debit is None or debit.statut in (Transaction.Statut.REMBOURSEE, Transaction.Statut.ANNULEE):
        return paiement
    montant_rembourse = debit.montant + (debit.frais or Decimal("0"))
    remboursement = paiement.compte_bancaire.crediter_systeme(
        montant_rembourse,
        f"Remboursement paiement mobile {paiement.reference_transaction} — {motif}",
        cle_idempotence=f"paiement-remboursement-{paiement.pk}",
        type_transaction=Transaction.Type.REMBOURSEMENT,
        reference_externe=paiement.reference_externe,
    )
    debit.statut = Transaction.Statut.REMBOURSEE
    debit.save(update_fields=["statut"])
    paiement.transaction_remboursement = remboursement
    paiement.message_erreur = motif[:255]
    paiement.save(update_fields=["transaction_remboursement", "message_erreur", "date_mise_a_jour"])
    return paiement


def _marquer_echec(paiement_id, message, rembourser=True):
    with db_transaction.atomic():
        paiement = PaiementMobile.objects.select_for_update().get(pk=paiement_id)
        if paiement.statut in (PaiementMobile.Statut.REUSSI, PaiementMobile.Statut.ECHOUE, PaiementMobile.Statut.ANNULE):
            return paiement
        if rembourser and paiement.type_operation == PaiementMobile.TypeOperation.RETRAIT_MOBILE:
            rembourser_paiement(paiement.pk, motif=message)
            paiement.refresh_from_db()
        paiement.statut = PaiementMobile.Statut.ECHOUE
        paiement.message_erreur = message[:255]
        paiement.date_finalisation = timezone.now()
        paiement.save(update_fields=["statut", "message_erreur", "date_finalisation", "date_mise_a_jour"])
    _notifier_resultat(paiement)
    return paiement


def _paiement_idempotent(cle, client, type_operation):
    """Rejoue une demande identique sans jamais renvoyer l'opération d'un tiers."""
    existant = PaiementMobile.objects.filter(cle_idempotence=cle).first()
    if existant is None:
        return None
    if existant.client_id != client.id or existant.type_operation != type_operation:
        raise ValidationError(
            "Cette clé d'idempotence est déjà utilisée pour une autre opération."
        )
    return existant


def initier_recharge(client, compte, montant, operateur, numero_telephone, *, cle_idempotence=None):
    montant = Decimal(montant).quantize(Decimal("0.01"))
    if montant <= 0:
        raise ValidationError("Le montant doit être positif.")
    if compte.proprietaire_id != client.id:
        raise ValidationError("Ce compte ne vous appartient pas.")
    if not compte.est_actif():
        raise ValidationError("Ce compte est bloqué.")
    numero_telephone = valider_numero(numero_telephone, operateur)
    cle = cle_idempotence or nouvelle_cle_idempotence("mobile-recharge-")

    existant = _paiement_idempotent(cle, client, PaiementMobile.TypeOperation.RECHARGE)
    if existant:
        return existant
    paiement = PaiementMobile.objects.create(
        client=client,
        compte_bancaire=compte,
        reference_transaction=client_api.generer_reference(),
        type_operation=PaiementMobile.TypeOperation.RECHARGE,
        operateur=operateur,
        numero_telephone=numero_telephone,
        montant=montant,
        cle_idempotence=cle,
    )
    try:
        resultat = client_api.initier_demande_paiement(
            paiement.reference_transaction,
            montant,
            numero_telephone,
            operateur,
            type_operation=PaiementMobile.TypeOperation.RECHARGE,
        )
    except client_api.ErreurServicePaiementMobile as erreur:
        return _marquer_echec(paiement.pk, f"Service opérateur indisponible : {erreur}", rembourser=False)

    paiement.reference_externe = resultat.get("id_transaction", "") or resultat.get("reference", "")
    paiement.save(update_fields=["reference_externe", "date_mise_a_jour"])
    statut_immediat = resultat.get("statut")
    if statut_immediat in ("REUSSI", "ECHOUE"):
        return finaliser_paiement(paiement.pk, statut_immediat, resultat.get("message", ""))
    return paiement


def initier_retrait(client, compte, montant, operateur, numero_telephone, *, cle_idempotence=None):
    montant = Decimal(montant).quantize(Decimal("0.01"))
    if montant <= 0:
        raise ValidationError("Le montant doit être positif.")
    if compte.proprietaire_id != client.id:
        raise ValidationError("Ce compte ne vous appartient pas.")
    if not compte.est_actif():
        raise ValidationError("Ce compte est bloqué.")
    numero_telephone = valider_numero(numero_telephone, operateur)
    frais = calculer_frais("PAIEMENT_MOBILE", montant)
    cle = cle_idempotence or nouvelle_cle_idempotence("mobile-retrait-")
    existant = _paiement_idempotent(cle, client, PaiementMobile.TypeOperation.RETRAIT_MOBILE)
    if existant:
        return existant

    with db_transaction.atomic():
        paiement = PaiementMobile.objects.create(
            client=client,
            compte_bancaire=compte,
            reference_transaction=client_api.generer_reference(),
            type_operation=PaiementMobile.TypeOperation.RETRAIT_MOBILE,
            operateur=operateur,
            numero_telephone=numero_telephone,
            montant=montant,
            frais=frais,
            cle_idempotence=cle,
        )
        # Un échec de débit (solde/plafond) annule toute la transaction :
        # aucune trace orpheline et surtout aucun argent débité.
        debit = compte.retirer(
            montant,
            description=f"Retrait mobile {get_operateur(operateur).libelle} ({numero_telephone})",
            frais=frais,
            type_transaction=Transaction.Type.PAIEMENT_MOBILE,
            categorie=Transaction.Categorie.AUTRE,
            canal=Transaction.Canal.MOBILE,
            cree_par=client,
            cle_idempotence=f"paiement-debit-{paiement.pk}",
            reference_externe=paiement.reference_transaction,
            statut=Transaction.Statut.EN_ATTENTE,
        )
        paiement.transaction_bancaire = debit
        paiement.save(update_fields=["transaction_bancaire", "date_mise_a_jour"])

    try:
        resultat = client_api.initier_demande_paiement(
            paiement.reference_transaction,
            montant,
            numero_telephone,
            operateur,
            type_operation=PaiementMobile.TypeOperation.RETRAIT_MOBILE,
        )
    except client_api.ErreurServicePaiementMobile as erreur:
        return _marquer_echec(paiement.pk, f"Service opérateur indisponible : {erreur}")

    paiement.reference_externe = resultat.get("id_transaction", "") or resultat.get("reference", "")
    paiement.save(update_fields=["reference_externe", "date_mise_a_jour"])
    statut_immediat = resultat.get("statut")
    if statut_immediat in ("REUSSI", "ECHOUE"):
        return finaliser_paiement(paiement.pk, statut_immediat, resultat.get("message", ""))
    return paiement


@db_transaction.atomic
def finaliser_paiement(paiement_id, nouveau_statut, message=""):
    """Applique une transition finale, avec crédit/remboursement idempotent."""
    paiement = PaiementMobile.objects.select_for_update().get(pk=paiement_id)
    if paiement.statut in (PaiementMobile.Statut.REUSSI, PaiementMobile.Statut.ECHOUE, PaiementMobile.Statut.ANNULE):
        return paiement

    if nouveau_statut == "REUSSI":
        if paiement.type_operation == PaiementMobile.TypeOperation.RECHARGE:
            credit = paiement.compte_bancaire.crediter_systeme(
                paiement.montant,
                f"Recharge mobile {paiement.get_operateur_display()} ({paiement.numero_telephone})",
                cle_idempotence=f"paiement-credit-{paiement.pk}",
                type_transaction=Transaction.Type.PAIEMENT_MOBILE,
                reference_externe=paiement.reference_externe,
            )
            paiement.transaction_bancaire = credit
        elif paiement.transaction_bancaire_id:
            debit = Transaction.objects.select_for_update().get(pk=paiement.transaction_bancaire_id)
            if debit.statut == Transaction.Statut.EN_ATTENTE:
                debit.statut = Transaction.Statut.REUSSIE
                debit.save(update_fields=["statut"])
        paiement.statut = PaiementMobile.Statut.REUSSI
        paiement.message_erreur = ""
    elif nouveau_statut == "ECHOUE":
        if paiement.type_operation == PaiementMobile.TypeOperation.RETRAIT_MOBILE:
            rembourser_paiement(paiement.pk, motif=message or "Échec côté opérateur")
            paiement.refresh_from_db()
        paiement.statut = PaiementMobile.Statut.ECHOUE
        paiement.message_erreur = (message or "Échec côté opérateur.")[:255]
    else:
        return paiement

    paiement.date_finalisation = timezone.now()
    paiement.save(
        update_fields=[
            "statut",
            "message_erreur",
            "transaction_bancaire",
            "date_finalisation",
            "date_mise_a_jour",
        ]
    )
    _notifier_resultat(paiement)
    return paiement


def synchroniser_paiement(paiement, utilisateur=None):
    """Interroge l'opérateur puis applique la transition finale si connue."""
    if paiement.statut != PaiementMobile.Statut.EN_ATTENTE:
        return paiement
    if not paiement.reference_externe:
        return paiement
    try:
        resultat = client_api.consulter_statut(paiement.reference_externe)
    except client_api.ErreurServicePaiementMobile:
        return paiement

    statut = resultat.get("statut")
    if statut not in ("REUSSI", "ECHOUE"):
        return paiement
    with db_transaction.atomic():
        PaiementMobile.objects.filter(pk=paiement.pk).update(
            tentatives_verification=F("tentatives_verification") + 1
        )
    return finaliser_paiement(paiement.pk, statut, resultat.get("message", ""))

