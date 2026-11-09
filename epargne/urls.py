"""Routes de l'épargne et des budgets."""

from django.urls import path

from . import views

app_name = "epargne"

urlpatterns = [
    path("", views.tableau_epargne, name="tableau_epargne"),
    path("objectifs/nouveau/", views.creer_objectif, name="creer_objectif"),
    path("objectifs/<int:objectif_id>/", views.detail_objectif, name="detail_objectif"),
    path("objectifs/<int:objectif_id>/modifier/", views.modifier_objectif, name="modifier_objectif"),
    path("objectifs/<int:objectif_id>/contribuer/", views.contribuer_objectif, name="contribuer_objectif"),
    path("objectifs/<int:objectif_id>/annuler/", views.annuler_objectif, name="annuler_objectif"),
    path("arrondis/", views.configurer_arrondi, name="configurer_arrondi"),
    path("budgets/", views.budgets, name="budgets"),
    path("budgets/<int:budget_id>/modifier/", views.modifier_budget, name="modifier_budget"),
    path("budgets/<int:budget_id>/supprimer/", views.supprimer_budget, name="supprimer_budget"),
]
