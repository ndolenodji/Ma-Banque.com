"""Routes des cartes bancaires de démonstration."""

from django.urls import path

from . import views

app_name = "cartes"

urlpatterns = [
    path("", views.mes_cartes, name="mes_cartes"),
    path("demander/", views.demander_carte, name="demander_carte"),
    path("<int:carte_id>/", views.detail_carte, name="detail_carte"),
    path("<int:carte_id>/basculer/", views.basculer_carte, name="basculer_carte"),
    path("<int:carte_id>/annuler/", views.annuler_carte, name="annuler_carte"),
]
