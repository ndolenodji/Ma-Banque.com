"""
Middlewares maison :

- JournalisationMiddleware : expose l'adresse IP réelle (proxy de confiance
  uniquement) pour la journalisation ;
- RestrictionAdminMiddleware : réserve le back-office Django natif aux
  administrateurs métier (un client marqué is_staff ne doit pas y entrer) ;
- ActiviteSessionMiddleware : met à jour l'horodatage de dernière activité
  des sessions connues, pour l'écran « appareils et sessions ».
"""

from django.conf import settings
from django.http import HttpResponseForbidden
from django.utils import timezone

from .models import SessionConnexion


class JournalisationMiddleware:
    """
    Ajoute request.adresse_ip_client. L'en-tête X-Forwarded-For n'est pris
    en compte que si l'application est déclarée derrière un proxy de
    confiance (DJANGO_DERRIERE_PROXY=True), afin d'éviter l'usurpation.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.adresse_ip_client = self._extraire_ip(request)
        return self.get_response(request)

    @staticmethod
    def _extraire_ip(request):
        if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
            entete_forward = request.META.get("HTTP_X_FORWARDED_FOR")
            if entete_forward:
                return entete_forward.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


class RestrictionAdminMiddleware:
    """Interdit /admin/ à tout utilisateur authentifié non administrateur métier."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path.startswith("/admin/")
            and request.user.is_authenticated
            and not request.user.est_administrateur()
        ):
            return HttpResponseForbidden(
                "Accès refusé : le back-office est réservé aux administrateurs de la banque."
            )
        return self.get_response(request)


class ActiviteSessionMiddleware:
    """Met à jour au plus toutes les 5 minutes la session connue de l'utilisateur."""

    INTERVALLE_SECONDES = 300

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.session.session_key
            and not request.path.startswith(settings.STATIC_URL)
        ):
            dernier = request.session.get("session_activite_maj")
            maintenant = timezone.now()
            if dernier is None or (maintenant.timestamp() - dernier) > self.INTERVALLE_SECONDES:
                SessionConnexion.objects.filter(
                    cle_session=request.session.session_key, est_active=True
                ).update(derniere_activite=maintenant)
                request.session["session_activite_maj"] = maintenant.timestamp()
        return self.get_response(request)
