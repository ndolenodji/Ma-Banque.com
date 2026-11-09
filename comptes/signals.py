"""
Signaux d'authentification : enregistrement et clôture des sessions connues
(écran « appareils et sessions », historique de connexion).
"""

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .models import SessionConnexion
from .securite import libelle_appareil


@receiver(user_logged_in)
def enregistrer_session_connectee(sender, request, user, **kwargs):
    if request is None:
        return
    if not request.session.session_key:
        request.session.save()
    agent = request.META.get("HTTP_USER_AGENT", "")[:255]
    SessionConnexion.objects.update_or_create(
        cle_session=request.session.session_key,
        defaults={
            "utilisateur": user,
            "appareil": libelle_appareil(agent),
            "user_agent": agent,
            "adresse_ip": getattr(request, "adresse_ip_client", None),
            "est_active": True,
        },
    )


@receiver(user_logged_out)
def cloturer_session_connectee(sender, request, user, **kwargs):
    if request is None or not request.session.session_key:
        return
    SessionConnexion.objects.filter(cle_session=request.session.session_key).update(est_active=False)
