"""
Configuration principale des URLs du projet Django « Ma Banque ».
"""

from django.conf import settings
from django.contrib import admin
from django.http import Http404
from django.urls import include, path, re_path
from django.views.static import serve

from comptes import views as vues_comptes

urlpatterns = [
    path("admin/", admin.site.urls),

    # Page d'accueil = redirige vers le tableau de bord (ou la connexion)
    path("", vues_comptes.tableau_de_bord, name="accueil"),

    path("comptes/", include("comptes.urls")),
    path("kyc/", include("kyc.urls")),
    path("operations/", include("transactions.urls")),
    path("paiement-mobile/", include("paiement_mobile.urls")),
    path("cartes/", include("cartes.urls")),
    path("epargne/", include("epargne.urls")),
    path("factures/", include("factures.urls")),
    path("devises/", include("devises.urls")),
    path("administration/", include("administration.urls")),
    path("reseau/", include("reseau.urls")),
    path("support/", include("support.urls")),
    path("risques/", include("risques.urls")),
]


def media_protege(request, path):
    """
    En développement, /media/ ne doit jamais exposer les pièces d'identité
    historiques ni les photos de profil. Les documents KYC actuels sont
    stockés hors MEDIA_ROOT et servis par une vue contrôlée (kyc.views).
    En production, le serveur web doit appliquer la même interdiction.
    """
    import posixpath

    normalise = posixpath.normpath(path)
    chemins_sensibles = ("kyc/", "profils/")
    if normalise.startswith("..") or normalise.startswith(chemins_sensibles):
        raise Http404("Média privé.")
    return serve(request, normalise, document_root=settings.MEDIA_ROOT)


if settings.DEBUG:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", media_protege)]
