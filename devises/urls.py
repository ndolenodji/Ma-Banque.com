"""Routes des devises et conversions."""

from django.urls import path

from . import views

app_name = "devises"

urlpatterns = [
    path("", views.taux_et_conversion, name="taux"),
    path("apercu/", views.apercu_conversion, name="apercu_conversion"),
    path("conversions/", views.historique_conversions, name="historique_conversions"),
]
