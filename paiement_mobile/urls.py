from django.urls import path

from . import views

app_name = "paiement_mobile"

urlpatterns = [
    path("recharger/", views.recharger_compte, name="recharger"),
    path("retrait/", views.retrait_mobile, name="retrait_mobile"),
    path("statut/<int:paiement_id>/", views.statut_paiement, name="statut"),
    path("mon-qr/", views.mon_code_qr, name="mon_code_qr"),
    path("mon-qr.png", views.telecharger_mon_qr, name="telecharger_mon_qr"),
    path("paiement-marchand/", views.paiement_marchand, name="paiement_marchand"),
    path("historique/", views.historique_paiements, name="historique"),
]
