from django.urls import path

from . import views

app_name = "transactions"

urlpatterns = [
    path("comptes/", views.liste_comptes, name="liste_comptes"),
    path("comptes/ouvrir/", views.ouvrir_compte, name="ouvrir_compte"),
    path("comptes/<int:compte_id>/", views.detail_compte, name="detail_compte"),
    path("comptes/<int:compte_id>/releve.pdf", views.telecharger_releve, name="telecharger_releve"),
    path("depots-retraits/", views.depots_retraits, name="depots_retraits"),
    path("releves/", views.releves, name="releves"),
    path("releves/exporter/<str:format_export>/", views.exporter_releve, name="exporter_releve"),
    path("deposer/", views.deposer, name="deposer"),
    path("retirer/", views.retirer, name="retirer"),
    path("virement/", views.virement, name="virement"),
    path("virements-programmes/", views.virements_programmes, name="virements_programmes"),
    path(
        "virements-programmes/<int:virement_id>/basculer/",
        views.basculer_virement_programme,
        name="basculer_virement_programme",
    ),
    path(
        "virements-programmes/<int:virement_id>/annuler/",
        views.annuler_virement_programme,
        name="annuler_virement_programme",
    ),
    path(
        "virements-programmes/<int:virement_id>/executer/",
        views.executer_virement_maintenant,
        name="executer_virement_maintenant",
    ),
    path("transactions/<int:transaction_id>/recu.pdf", views.telecharger_recu, name="telecharger_recu"),
    path("historique/", views.historique, name="historique"),
]
