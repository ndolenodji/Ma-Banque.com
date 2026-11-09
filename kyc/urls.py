from django.urls import path

from . import views

app_name = "kyc"

urlpatterns = [
    path("", views.mon_dossier, name="mon_dossier"),
    path("informations/", views.enregistrer_informations, name="enregistrer_informations"),
    path("documents/ajouter/", views.ajouter_document, name="ajouter_document"),
    path("documents/<int:document_id>/supprimer/", views.supprimer_document, name="supprimer_document"),
    path("documents/<int:document_id>/telecharger/", views.telecharger_document, name="telecharger_document"),
    path("soumettre/", views.soumettre_dossier, name="soumettre"),
]
