from django.urls import path

from . import views

app_name = "reseau"

urlpatterns = [
    path("agents/", views.annuaire_agents, name="annuaire_agents"),
    path("marchands/", views.annuaire_marchands, name="annuaire_marchands"),
    path("marchand/", views.espace_marchand, name="espace_marchand"),
    path("marchand/inscription/", views.inscription_marchand, name="inscription_marchand"),
    path("marchand/demandes/creer/", views.creer_demande, name="creer_demande"),
    path("marchand/demandes/<int:demande_id>/", views.detail_demande, name="detail_demande"),
    path(
        "marchand/demandes/<int:demande_id>/annuler/",
        views.annuler_demande,
        name="annuler_demande",
    ),
    path("marchand/demandes/<int:demande_id>/qr.png", views.qr_demande, name="qr_demande"),
    path("payer/<uuid:jeton>/", views.payer_demande, name="payer_demande"),
    path("agent/", views.espace_agent, name="espace_agent"),
]
