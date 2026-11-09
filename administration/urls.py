from django.urls import path

from . import views

app_name = "administration"

urlpatterns = [
    path("", views.tableau_de_bord, name="tableau_de_bord"),
    path("activite-temps-reel.json", views.activite_temps_reel, name="activite_temps_reel"),
    path("clients/", views.supervision_clients, name="supervision_clients"),
    path("clients/creer/", views.creer_client, name="creer_client"),
    path("clients/<int:client_id>/", views.detail_client, name="detail_client"),
    path("clients/<int:client_id>/comptes/ouvrir/", views.ouvrir_compte_client, name="ouvrir_compte_client"),
    path("comptes/<int:compte_id>/bloquer/", views.bloquer_compte, name="bloquer_compte"),
    path("comptes/<int:compte_id>/debloquer/", views.debloquer_compte, name="debloquer_compte"),
    path("transactions/", views.supervision_transactions, name="supervision_transactions"),
    path("transactions/export.csv", views.exporter_transactions_csv, name="exporter_transactions_csv"),
    path("rapports/", views.rapports, name="rapports"),
    path("rapports/export.csv", views.exporter_rapport_csv, name="exporter_rapport_csv"),
    path("journal-audit/", views.journal_audit, name="journal_audit"),
    path("journal-audit/export.csv", views.exporter_journal_audit_csv, name="exporter_journal_audit_csv"),
    path("utilisateurs-internes/", views.gestion_utilisateurs_internes, name="utilisateurs_internes"),
    path("kyc/", views.kyc_dossiers, name="kyc_dossiers"),
    path("kyc/<int:dossier_id>/", views.kyc_detail, name="kyc_detail"),
    path("kyc/<int:dossier_id>/decision/", views.kyc_decider, name="kyc_decider"),
]
