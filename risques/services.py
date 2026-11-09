"""
Moteur de détection des opérations à risque.

Le score est explicable : chaque règle déclenchée est enregistrée avec son
libellé et ses points. Aucun blocage automatique de fonds n'est effectué :
l'alerte est ensuite instruite par un administrateur, avec traçabilité.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from comptes.models import JournalAudit, Utilisateur
from comptes.services_notifications import notifier
from transactions.models import Transaction

from .models import AlerteFraude

logger = logging.getLogger(__name__)

SEUIL_ALERTE = 25
TYPES_SORTANTS = {
    Transaction.Type.VIREMENT,
    Transaction.Type.RETRAIT,
    Transaction.Type.PAIEMENT_MOBILE,
    Transaction.Type.PAIEMENT_FACTURE,
    Transaction.Type.PAIEMENT_MARCHAND,
    Transaction.Type.CONVERSION,
}


def _niveau_pour_score(score):
    if score >= 75:
        return AlerteFraude.Niveau.CRITIQUE
    if score >= 50:
        return AlerteFraude.Niveau.ELEVE
    if score >= 25:
        return AlerteFraude.Niveau.MOYEN
    return AlerteFraude.Niveau.FAIBLE


def _regle(code, libelle, points):
    return {"code": code, "libelle": libelle, "points": points}


def evaluer_transaction(transaction_bancaire):
    """
    Calcule un score (0-100) et retourne la liste des règles déclenchées.

    Cette fonction ne crée aucune alerte : elle est utilisable pour un aperçu
    ou des tests sans effet de bord.
    """
    if transaction_bancaire.compte_source_id is None:
        return 0, []

    compte = transaction_bancaire.compte_source
    utilisateur = compte.proprietaire
    montant = transaction_bancaire.montant or Decimal("0")
    regles = []

    if montant >= Decimal("1000000"):
        regles.append(_regle("MONTANT_CRITIQUE", "Montant supérieur ou égal à 1 000 000 FCFA", 40))
    elif montant >= Decimal("500000"):
        regles.append(_regle("MONTANT_ELEVE", "Montant supérieur ou égal à 500 000 FCFA", 25))
    elif montant >= Decimal("200000"):
        regles.append(_regle("MONTANT_NOTABLE", "Montant supérieur ou égal à 200 000 FCFA", 10))

    # Vélocité : plusieurs opérations sortantes réussies sur une courte période.
    debut_fenetre = transaction_bancaire.date_transaction - timedelta(minutes=10)
    operations_recentes = (
        Transaction.objects.filter(
            compte_source__proprietaire=utilisateur,
            date_transaction__gte=debut_fenetre,
            date_transaction__lt=transaction_bancaire.date_transaction,
            statut=Transaction.Statut.REUSSIE,
        )
        .exclude(pk=transaction_bancaire.pk)
        .count()
    )
    if operations_recentes >= 4:
        regles.append(
            _regle("VELOCITE_ELEVEE", f"{operations_recentes} opérations en 10 minutes", 25)
        )
    elif operations_recentes >= 2:
        regles.append(
            _regle("VELOCITE_MODEREE", f"{operations_recentes} opérations en 10 minutes", 10)
        )

    heure_locale = timezone.localtime(transaction_bancaire.date_transaction).hour
    if heure_locale < 6:
        regles.append(_regle("HORAIRE_NOCTURNE", "Opération entre minuit et 6 h", 10))

    # Premier virement vers un compte tiers : situation à confirmer.
    if (
        transaction_bancaire.type_transaction == Transaction.Type.VIREMENT
        and transaction_bancaire.compte_destination_id
        and transaction_bancaire.compte_destination.proprietaire_id != utilisateur.id
    ):
        deja_effectue = (
            Transaction.objects.filter(
                compte_source__proprietaire=utilisateur,
                compte_destination=transaction_bancaire.compte_destination,
                type_transaction=Transaction.Type.VIREMENT,
                statut=Transaction.Statut.REUSSIE,
            )
            .exclude(pk=transaction_bancaire.pk)
            .exists()
        )
        if not deja_effectue:
            regles.append(_regle("NOUVEAU_DESTINATAIRE", "Premier virement vers ce compte", 10))

    age_compte = timezone.now() - compte.date_ouverture
    if age_compte < timedelta(days=7) and montant >= Decimal("100000"):
        regles.append(_regle("COMPTE_RECENT", "Compte ouvert depuis moins de 7 jours", 10))

    if transaction_bancaire.frais and transaction_bancaire.frais >= Decimal("50000"):
        regles.append(_regle("FRAIS_INHABITUELS", "Frais supérieurs ou égaux à 50 000 FCFA", 5))

    score = min(100, sum(regle["points"] for regle in regles))
    return score, regles


def _notifier_administrateurs(alerte):
    administrateurs = Utilisateur.objects.filter(
        Q(role=Utilisateur.Role.ADMIN) | Q(is_superuser=True),
        est_actif_bancaire=True,
    ).distinct()
    for administrateur in administrateurs:
        notifier(
            administrateur,
            f"Alerte fraude {alerte.get_niveau_display().lower()} — score {alerte.score}",
            f"L'opération {alerte.reference} doit être instruite. "
            f"Client : {alerte.utilisateur}. Aucun blocage automatique n'a été appliqué.",
            type_notification="FRAUDE",
            niveau="CRITIQUE" if alerte.niveau == AlerteFraude.Niveau.CRITIQUE else "ATTENTION",
            lien=f"/risques/alertes/{alerte.pk}/",
        )


def analyser_transaction(transaction_bancaire):
    """
    Crée une alerte pour une transaction sortante réussie dont le score atteint
    le seuil. Retourne l'alerte créée ou ``None``.
    """
    if transaction_bancaire.statut != Transaction.Statut.REUSSIE:
        return None
    if transaction_bancaire.type_transaction not in TYPES_SORTANTS:
        return None

    score, regles = evaluer_transaction(transaction_bancaire)
    if score < SEUIL_ALERTE:
        return None

    niveau = _niveau_pour_score(score)
    details = " ".join(f"{regle['libelle']} (+{regle['points']})." for regle in regles)
    alerte = AlerteFraude.objects.create(
        utilisateur=transaction_bancaire.compte_source.proprietaire,
        transaction=transaction_bancaire,
        paiement_mobile=transaction_bancaire.paiements_mobiles.first(),
        score=score,
        niveau=niveau,
        regles=regles,
        explication=(
            f"Score de risque {score}/100 ({alerte_niveau_libelle(niveau)}). "
            f"{details} Aucun blocage automatique : une revue humaine est requise."
        ),
    )
    if score >= 50:
        _notifier_administrateurs(alerte)
    return alerte


def alerte_niveau_libelle(niveau):
    for valeur, libelle in AlerteFraude.Niveau.choices:
        if valeur == niveau:
            return libelle
    return niveau


@db_transaction.atomic
def traiter_alerte(alerte, acteur, decision, commentaire=""):
    """
    Instruit une alerte. Décisions autorisées : EN_REVUE, CONFIRMEE, REJETEE.

    Une confirmation ou un rejet exige un commentaire. Aucun mouvement de
    fonds n'est déclenché par cette fonction.
    """
    commentaire = (commentaire or "").strip()
    alerte = AlerteFraude.objects.select_for_update().select_related("utilisateur", "transaction").get(pk=alerte.pk)

    transitions = {
        AlerteFraude.Statut.NOUVELLE: {AlerteFraude.Statut.EN_REVUE},
        AlerteFraude.Statut.EN_REVUE: {
            AlerteFraude.Statut.CONFIRMEE,
            AlerteFraude.Statut.REJETEE,
        },
        AlerteFraude.Statut.CONFIRMEE: set(),
        AlerteFraude.Statut.REJETEE: set(),
    }
    if decision not in transitions.get(alerte.statut, set()):
        raise ValueError("Cette décision n'est pas autorisée pour le statut actuel de l'alerte.")
    if decision in (AlerteFraude.Statut.CONFIRMEE, AlerteFraude.Statut.REJETEE) and not commentaire:
        raise ValueError("Un commentaire est obligatoire pour confirmer ou rejeter une alerte.")

    ancien_statut = alerte.statut
    alerte.statut = decision
    alerte.commentaire = commentaire
    alerte.traite_par = acteur
    alerte.date_traitement = timezone.now()
    alerte.save(update_fields=["statut", "commentaire", "traite_par", "date_traitement"])

    JournalAudit.journaliser(
        acteur,
        "Fraude — alerte instruite",
        f"{alerte.reference} : {ancien_statut} → {decision}. {commentaire}".strip(),
    )

    if decision == AlerteFraude.Statut.CONFIRMEE:
        notifier(
            alerte.utilisateur,
            "Opération examinée par la sécurité",
            "Une opération de votre compte a été contrôlée par notre équipe sécurité. "
            "Aucun blocage automatique n'a été appliqué. Contactez votre agence pour toute précision.",
            type_notification="SECURITE",
            niveau="ATTENTION",
            lien="/comptes/",
        )
    return alerte
