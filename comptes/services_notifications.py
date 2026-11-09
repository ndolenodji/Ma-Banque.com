"""Création centralisée des notifications in-app et SMS simulés."""

from django.db.models import Q
from django.utils import timezone

from .models import Notification, SmsSimule, Utilisateur


def notifier(
    destinataire,
    titre,
    message,
    *,
    type_notification=Notification.Type.SYSTEME,
    niveau=Notification.Niveau.INFO,
    lien="",
):
    if destinataire is None or not getattr(destinataire, "pk", None):
        return None
    if lien and not lien.startswith("/"):
        # Les liens de notification restent des chemins internes : jamais
        # d'URL externe (protection contre les redirections ouvertes).
        lien = ""
    return Notification.objects.create(
        destinataire=destinataire,
        type_notification=type_notification,
        niveau=niveau,
        titre=titre[:160],
        message=message,
        lien=lien,
    )


def notifier_personnel(titre, message, *, niveau=Notification.Niveau.ATTENTION, lien="", roles=("ADMIN",)):
    destinataires = Utilisateur.objects.filter(
        Q(role__in=roles) | Q(is_superuser=True), est_actif_bancaire=True
    ).distinct()
    notifications = []
    for destinataire in destinataires.iterator():
        notifications.append(
            notifier(destinataire, titre, message, type_notification=Notification.Type.SYSTEME, niveau=niveau, lien=lien)
        )
    return notifications


def envoyer_sms_simule(destinataire, message):
    """Trace un SMS simulé ; aucune passerelle SMS réelle n'est appelée."""
    if destinataire is None or not getattr(destinataire, "telephone", ""):
        return None
    return SmsSimule.objects.create(
        destinataire=destinataire,
        numero_telephone=destinataire.telephone,
        message=message,
        statut="SIMULE",
    )


def marquer_toutes_lues(utilisateur):
    return Notification.objects.filter(destinataire=utilisateur, lue=False).update(
        lue=True, date_lecture=timezone.now()
    )
