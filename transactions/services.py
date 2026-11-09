"""
Services transactionnels : frais, virements programmés, conversions de
devises et arrondis d'épargne.

Les écritures financières restent dans les méthodes verrouillées de
CompteBancaire ; ce module orchestre les cas d'usage et les traces.
"""

import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from comptes.services_notifications import notifier
from .models import (
    CompteBancaire,
    ConversionDevise,
    RegleFrais,
    Transaction,
    VirementProgramme,
    decimal_fiable,
)

logger = logging.getLogger(__name__)


def calculer_frais(type_operation, montant, *, arrondi=True):
    """Retourne les frais applicables selon la grille active (0 par défaut)."""
    regle = RegleFrais.objects.filter(type_operation=type_operation, actif=True).first()
    if regle is None:
        return Decimal("0.00")
    frais = regle.calculer(decimal_fiable(montant))
    return frais.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if arrondi else frais


def nouvelle_cle_idempotence(prefixe=""):
    valeur = str(uuid.uuid4())
    return f"{prefixe}{valeur}" if prefixe else valeur


# ---------------------------------------------------------------------------
# Virements programmés
# ---------------------------------------------------------------------------
def executer_virement_programme(virement_id, *, forcer=False):
    """Exécute une échéance due et renvoie la transaction ou None.

    ``forcer=True`` permet à un client d'exécuter immédiatement une échéance
    future ; le calendrier est alors recalculé à partir d'aujourd'hui. Une
    même échéance ne peut jamais être exécutée deux fois le même jour.
    """
    with transaction.atomic():
        virement = VirementProgramme.objects.select_for_update().get(pk=virement_id)
        if virement.statut != VirementProgramme.Statut.ACTIF:
            return None
        aujourdhui = timezone.localdate()
        if not forcer and virement.prochaine_execution > aujourdhui:
            return None
        if (
            forcer
            and virement.derniere_execution
            and timezone.localtime(virement.derniere_execution).date() == aujourdhui
        ):
            return None
        if virement.date_fin and virement.date_fin < aujourdhui:
            virement.statut = VirementProgramme.Statut.TERMINE
            virement.save(update_fields=["statut"])
            return None
        if forcer and virement.prochaine_execution > aujourdhui:
            # L'exécution anticipée devient le nouveau point de départ du calendrier.
            virement.prochaine_execution = aujourdhui

        compte_cible = virement.compte_destination
        if compte_cible is None:
            compte_cible = CompteBancaire.objects.filter(numero_compte=virement.numero_destination).first()
        if compte_cible is None:
            virement.derniere_erreur = "Compte destinataire introuvable."
            virement.statut = VirementProgramme.Statut.PAUSE
            virement.save(update_fields=["derniere_erreur", "statut"])
            return None

        frais = calculer_frais(RegleFrais.Operation.VIREMENT, virement.montant)
        cle = f"virement-programme-{virement.pk}-{virement.prochaine_execution.isoformat()}"
        try:
            operation = virement.compte_source.virer_vers(
                compte_cible,
                virement.montant,
                description=virement.description or f"Virement programmé #{virement.pk}",
                frais=frais,
                canal=Transaction.Canal.SYSTEME,
                cree_par=virement.proprietaire,
                cle_idempotence=cle,
            )
        except ValidationError as erreur:
            message = erreur.messages[0] if getattr(erreur, "messages", None) else str(erreur)
            virement.derniere_erreur = message[:255]
            virement.statut = VirementProgramme.Statut.PAUSE
            virement.save(update_fields=["derniere_erreur", "statut"])
            virement.compte_destination = compte_cible
            virement.save(update_fields=["compte_destination"])
            transaction.on_commit(
                lambda: notifier(
                    virement.proprietaire,
                    "Virement programmé mis en pause",
                    f"L'échéance du {virement.prochaine_execution:%d/%m/%Y} a échoué : {message}",
                    niveau="ATTENTION",
                    lien="/operations/virements-programmes/",
                )
            )
            return None

        virement.compte_destination = compte_cible
        virement.derniere_execution = timezone.now()
        virement.nombre_executions += 1
        virement.derniere_erreur = ""
        virement.avancer_echeance()
        virement.save(
            update_fields=[
                "compte_destination",
                "derniere_execution",
                "nombre_executions",
                "derniere_erreur",
                "prochaine_execution",
                "statut",
            ]
        )
        transaction.on_commit(
            lambda: notifier(
                virement.proprietaire,
                "Virement programmé exécuté",
                f"{virement.montant} {virement.compte_source.code_devise} transférés vers {virement.numero_destination}.",
                type_notification="TRANSACTION",
                niveau="SUCCES",
                lien=f"/operations/historique/?recherche={operation.reference}",
            )
        )
        return operation


def executer_echeances():
    """Exécute toutes les échéances dues ; retourne (succès, échecs)."""
    aujourdhui = timezone.localdate()
    ids = list(
        VirementProgramme.objects.filter(
            statut=VirementProgramme.Statut.ACTIF, prochaine_execution__lte=aujourdhui
        ).values_list("id", flat=True)
    )
    succes = 0
    for identifiant in ids:
        if executer_virement_programme(identifiant):
            succes += 1
    return succes, len(ids) - succes


# ---------------------------------------------------------------------------
# Conversion de devises
# ---------------------------------------------------------------------------
@transaction.atomic
def convertir_devises(
    compte_source, compte_destination, montant, utilisateur, *, frais=None, cle_idempotence=None
):
    """
    Convertit un montant entre deux comptes de devises différentes, au taux
    administré et sourcé. Aucun taux n'est inventé : si le taux manque, la
    conversion est refusée.
    """
    montant = decimal_fiable(montant)
    if montant <= 0:
        raise ValidationError("Le montant de la conversion doit être positif.")
    if compte_source.pk == compte_destination.pk:
        raise ValidationError("Les comptes source et destination doivent être différents.")
    if compte_source.proprietaire_id != utilisateur.id or compte_destination.proprietaire_id != utilisateur.id:
        raise ValidationError("Les deux comptes doivent vous appartenir.")
    if compte_source.devise_id == compte_destination.devise_id:
        raise ValidationError("Choisissez deux devises différentes pour une conversion.")

    cle = cle_idempotence or f"conversion-{uuid.uuid4()}"
    deja = Transaction.objects.filter(cle_idempotence=cle).first()
    if deja is not None:
        conversion = ConversionDevise.objects.filter(
            operation_id=deja.operation_id,
            compte_source=compte_source,
            compte_destination=compte_destination,
            montant_source=montant,
        ).first()
        if conversion is not None:
            return conversion
        raise ValidationError("Cette clé d'idempotence est déjà utilisée pour une autre conversion.")

    frais = decimal_fiable(frais if frais is not None else calculer_frais(RegleFrais.Operation.CONVERSION, montant))
    comptes = {
        c.pk: c
        for c in CompteBancaire.objects.select_for_update()
        .filter(pk__in=[compte_source.pk, compte_destination.pk])
        .order_by("pk")
        .select_related("devise")
    }
    source = comptes[compte_source.pk]
    destination = comptes[compte_destination.pk]
    if not source.est_actif() or not destination.est_actif():
        raise ValidationError("Les deux comptes doivent être actifs.")
    total = montant + frais
    source._controler_debit(total)
    source.verifier_plafonds(total)

    montant_xaf = source.devise.vers_xaf(montant)
    montant_destination = destination.devise.depuis_xaf(montant_xaf)
    if montant_destination <= 0:
        raise ValidationError("Le montant converti est trop faible pour la devise de destination.")
    taux_effectif = (montant_destination / montant).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    operation_id = uuid.uuid4()
    source.solde = source.solde - total
    destination.solde = destination.solde + montant_destination
    source.save(update_fields=["solde", "date_mise_a_jour"])
    destination.save(update_fields=["solde", "date_mise_a_jour"])

    description = f"Conversion {source.code_devise} → {destination.code_devise}"
    tx_source = Transaction.objects.create(
        type_transaction=Transaction.Type.CONVERSION,
        montant=montant,
        frais=frais,
        compte_source=source,
        statut=Transaction.Statut.REUSSIE,
        description=description,
        canal=Transaction.Canal.APPLICATION,
        cree_par=utilisateur,
        cle_idempotence=cle,
        operation_id=operation_id,
        devise=source.code_devise,
        taux_change=taux_effectif,
        contre_valeur=montant_destination,
        solde_apres=source.solde,
    )
    tx_destination = Transaction.objects.create(
        type_transaction=Transaction.Type.CONVERSION,
        montant=montant_destination,
        compte_destination=destination,
        statut=Transaction.Statut.REUSSIE,
        description=description,
        canal=Transaction.Canal.APPLICATION,
        cree_par=utilisateur,
        operation_id=operation_id,
        devise=destination.code_devise,
        taux_change=taux_effectif,
        contre_valeur=montant,
        solde_apres_destination=destination.solde,
    )
    conversion = ConversionDevise.objects.create(
        utilisateur=utilisateur,
        compte_source=source,
        compte_destination=destination,
        montant_source=montant,
        montant_destination=montant_destination,
        taux=taux_effectif,
        source_taux=" | ".join(
            filtre
            for filtre in [source.devise.source_taux, destination.devise.source_taux]
            if filtre
        )[:120],
        frais=frais,
        reference=f"CNV-{timezone.now():%Y%m%d}-{operation_id.hex[:8].upper()}",
        operation_id=operation_id,
        transaction_source=tx_source,
        transaction_destination=tx_destination,
    )
    source.solde = source.solde
    transaction.on_commit(
        lambda: notifier(
            utilisateur,
            "Conversion de devises effectuée",
            f"{montant} {source.code_devise} convertis en {montant_destination} {destination.code_devise}.",
            type_notification="TRANSACTION",
            niveau="SUCCES",
            lien=f"/devises/conversions/",
        )
    )
    return conversion


# ---------------------------------------------------------------------------
# Arrondi d'épargne
# ---------------------------------------------------------------------------
def appliquer_arrondi(utilisateur, compte_source, montant_operation, transaction_origine=None):
    """Transfère l'arrondi vers le compte d'épargne configuré, si possible."""
    from epargne.models import ConfigArrondi, ContributionEpargne

    config = ConfigArrondi.objects.filter(
        proprietaire=utilisateur, actif=True, compte_source=compte_source
    ).select_related("compte_epargne", "objectif").first()
    if config is None:
        return None
    multiple = Decimal(config.multiple)
    montant = decimal_fiable(montant_operation)
    # Complément au multiple supérieur. Le modulo de Decimal conserve le signe
    # du dividende : ``(-4300) % 1000`` vaut -300, d'où ce calcul explicite.
    reste = (multiple - (montant % multiple)) % multiple
    if reste <= 0:
        return None
    if transaction_origine and transaction_origine.categorie == Transaction.Categorie.EPARGNE:
        return None

    try:
        with transaction.atomic():
            operation = compte_source.virer_vers(
                config.compte_epargne,
                reste,
                description=f"Arrondi épargne ({multiple} FCFA)",
                categorie=Transaction.Categorie.EPARGNE,
                canal=Transaction.Canal.SYSTEME,
                cle_idempotence=(
                    f"arrondi-{transaction_origine.pk}" if transaction_origine else f"arrondi-{uuid.uuid4()}"
                ),
            )
            if config.objectif_id:
                objectif = config.objectif
                objectif = type(objectif).objects.select_for_update().get(pk=objectif.pk)
                if objectif.statut == objectif.Statut.ACTIF:
                    objectif.montant_actuel = min(
                        objectif.montant_cible, objectif.montant_actuel + reste
                    )
                    if objectif.montant_actuel >= objectif.montant_cible:
                        objectif.statut = objectif.Statut.ATTEINT
                        transaction.on_commit(
                            lambda: notifier(
                                utilisateur,
                                "Objectif d'épargne atteint",
                                f"Félicitations, votre objectif « {objectif.nom} » est atteint !",
                                type_notification="EPARGNE",
                                niveau="SUCCES",
                                lien="/epargne/",
                            )
                        )
                    objectif.save(update_fields=["montant_actuel", "statut", "date_mise_a_jour"])
                    ContributionEpargne.objects.create(
                        objectif=objectif,
                        transaction=operation,
                        montant=reste,
                        type_contribution=ContributionEpargne.Type.ARRONDI,
                    )
        return operation
    except ValidationError:
        # Solde insuffisant ou plafond : l'arrondi est simplement ignoré,
        # l'opération principale ne doit jamais échouer pour cette raison.
        logger.info("Arrondi épargne ignoré pour l'utilisateur %s.", utilisateur.pk)
        return None
    except Exception:  # noqa: BLE001 - l'épargne ne doit jamais bloquer un paiement
        logger.exception("Erreur inattendue pendant l'arrondi épargne.")
        return None
