"""Routes du paiement de factures simulé."""

from django.urls import path

from . import views

app_name = "factures"

urlpatterns = [
    path("", views.tableau_factures, name="tableau_factures"),
    path("ajouter/", views.ajouter_facture, name="ajouter_facture"),
    path("<int:facture_id>/", views.detail_facture, name="detail_facture"),
    path("<int:facture_id>/annuler/", views.annuler_facture, name="annuler_facture"),
]
