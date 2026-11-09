from django.urls import path

from . import views

app_name = "comptes"

urlpatterns = [
    path("inscription/", views.inscription, name="inscription"),
    path("connexion/", views.connexion, name="connexion"),
    path("double-authentification/", views.verification_2fa, name="verification_2fa"),
    path("deconnexion/", views.deconnexion, name="deconnexion"),
    path("", views.tableau_de_bord, name="tableau_de_bord"),
    path("activite-temps-reel.json", views.activite_temps_reel, name="activite_temps_reel"),
    path("profil/", views.profil, name="profil"),
    path("beneficiaires/", views.liste_beneficiaires, name="beneficiaires"),
    path("beneficiaires/<int:beneficiaire_id>/supprimer/", views.supprimer_beneficiaire, name="supprimer_beneficiaire"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/non-lues.json", views.notifications_non_lues_json, name="notifications_non_lues_json"),
    path("notifications/<int:notification_id>/lue/", views.marquer_notification_lue, name="marquer_notification_lue"),
    path("notifications/tout-lire/", views.marquer_toutes_notifications_lues, name="tout_marquer_lu"),
    path("parametres/", views.parametres, name="parametres"),
    path("parametres/2fa/activer/", views.activer_2fa, name="activer_2fa"),
    path("parametres/2fa/desactiver/", views.desactiver_2fa, name="desactiver_2fa"),
    path("parametres/2fa/codes-secours/", views.codes_secours, name="codes_secours"),
    path("parametres/2fa/codes-secours/regenerer/", views.regenerer_codes_secours, name="regenerer_codes_secours"),
    path("parametres/sessions/", views.sessions_actives, name="sessions"),
    path("parametres/sessions/<int:session_id>/revoquer/", views.revoquer_session, name="revoquer_session"),
    path("parametres/sessions/revoquer-autres/", views.revoquer_autres_sessions, name="revoquer_autres_sessions"),
    path("bientot/<str:cle>/", views.fonctionnalite_a_venir, name="bientot"),
    path("ussd/", views.simulateur_ussd, name="ussd"),
    path("mot-de-passe/oublie/", views.demande_reinitialisation, name="reinitialisation_demande"),
    path(
        "mot-de-passe/confirmer/<uidb64>/<token>/",
        views.ConfirmationReinitialisation.as_view(),
        name="reinitialisation_confirmation",
    ),
]
