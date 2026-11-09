from django.urls import path

from . import views

app_name = "risques"

urlpatterns = [
    path("alertes/", views.alertes, name="alertes"),
    path("alertes/non-lues.json", views.alertes_non_lues_json, name="alertes_non_lues_json"),
    path("alertes/<int:alerte_id>/", views.detail_alerte, name="detail_alerte"),
    path("alertes/<int:alerte_id>/traiter/", views.traiter, name="traiter"),
]
