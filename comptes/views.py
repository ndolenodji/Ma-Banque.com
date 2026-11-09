"""
Vues de l'app comptes : inscription, connexion sécurisée (2FA, limitation
des tentatives), déconnexion, profil, bénéficiaires, tableau de bord,
notifications, sessions/appareils et réinitialisation de mot de passe.

Les opérations sensibles sont journalisées (JournalAudit) et les
notifications in-app sont centralisées dans Notification.
"""

import logging
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetConfirmView
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from paiement_mobile.models import PaiementMobile
from transactions.models import Transaction

from .forms import (
    FormulaireBeneficiaire,
    FormulaireConnexion,
    FormulaireDemandeReinitialisation,
    FormulaireInscription,
    FormulaireProfil,
)
from .models import (
    Beneficiaire,
    JournalAudit,
    Notification,
    SessionConnexion,
    TentativeConnexion,
    Utilisateur,
)
from .securite import (
    chiffrer_secret,
    dechiffrer_secret,
    generer_codes_secours,
    generer_secret_totp,
    image_qr_data_uri,
    libelle_appareil,
    nombre_codes_secours_restants,
    uri_provisionnement,
    verifier_code_totp,
    verifier_et_consommer_code_secours,
)
from .services_notifications import marquer_toutes_lues, notifier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protection contre le brute-force
# ---------------------------------------------------------------------------
def verifier_blocage(identifiant):
    """
    Retourne True si l'identifiant est actuellement bloqué (trop de
    tentatives échouées sur la fenêtre de temps configurée).
    """
    seuil_temps = timezone.now() - timedelta(minutes=settings.LOGIN_BLOCAGE_MINUTES)
    echecs_recents = TentativeConnexion.objects.filter(
        identifiant=identifiant, reussie=False, horodatage__gte=seuil_temps
    ).count()
    return echecs_recents >= settings.LOGIN_MAX_TENTATIVES


def _trouver_utilisateur(identifiant):
    identifiant = (identifiant or "").strip()
    if "@" in identifiant:
        return Utilisateur.objects.filter(email__iexact=identifiant).order_by("id").first()
    return Utilisateur.objects.filter(username__iexact=identifiant).first()


def _enregistrer_tentative(request, identifiant, reussie, motif=""):
    return TentativeConnexion.objects.create(
        identifiant=identifiant[:150],
        adresse_ip=getattr(request, "adresse_ip_client", None),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        appareil=libelle_appareil(request.META.get("HTTP_USER_AGENT", ""))[:80],
        reussie=reussie,
        motif=motif[:120],
    )


def _finaliser_connexion(request, utilisateur, identifiant):
    if not utilisateur.est_actif_bancaire:
        _enregistrer_tentative(request, identifiant, False, "Compte utilisateur bloqué")
        messages.error(request, "Votre compte a été bloqué par la banque. Contactez votre agence.")
        return redirect("comptes:connexion")
    _enregistrer_tentative(request, identifiant, True)
    login(request, utilisateur)
    # La session pré-2FA est volontairement courte (300 s) : une fois le second
    # facteur validé, on restaure la durée de session normale.
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    JournalAudit.journaliser(
        utilisateur,
        "Connexion réussie",
        f"Appareil : {libelle_appareil(request.META.get('HTTP_USER_AGENT', ''))}",
        getattr(request, "adresse_ip_client", None),
    )
    messages.success(request, f"Bienvenue, {utilisateur.get_full_name() or utilisateur.username} !")
    if utilisateur.est_administrateur() or utilisateur.est_agent():
        return redirect("administration:tableau_de_bord")
    return redirect("comptes:tableau_de_bord")


def inscription(request):
    if request.user.is_authenticated:
        return redirect("comptes:tableau_de_bord")

    if request.method == "POST":
        formulaire = FormulaireInscription(request.POST, request.FILES)
        if formulaire.is_valid():
            utilisateur = formulaire.save()
            JournalAudit.journaliser(
                utilisateur, "Inscription", f"Nouveau client : {utilisateur.username}",
                getattr(request, "adresse_ip_client", None),
            )
            messages.success(request, "Votre compte a été créé avec succès. Vous pouvez maintenant vous connecter.")
            return redirect("comptes:connexion")
    else:
        formulaire = FormulaireInscription()

    return render(request, "comptes/inscription.html", {"formulaire": formulaire})


def connexion(request):
    if request.user.is_authenticated:
        return redirect("comptes:tableau_de_bord")

    if request.method == "POST":
        formulaire = FormulaireConnexion(request.POST)
        if formulaire.is_valid():
            identifiant = formulaire.cleaned_data["identifiant"].strip()
            mot_de_passe = formulaire.cleaned_data["mot_de_passe"]

            if verifier_blocage(identifiant):
                messages.error(
                    request,
                    f"Trop de tentatives échouées. Compte temporairement bloqué "
                    f"({settings.LOGIN_BLOCAGE_MINUTES} minutes). Réessayez plus tard.",
                )
                return render(request, "comptes/connexion.html", {"formulaire": formulaire})

            compte_existe = _trouver_utilisateur(identifiant)
            nom_authentification = compte_existe.username if compte_existe else identifiant
            utilisateur = authenticate(request, username=nom_authentification, password=mot_de_passe)

            if utilisateur is not None:
                if utilisateur.double_authentification_active:
                    request.session["pre_2fa_utilisateur"] = utilisateur.pk
                    request.session["pre_2fa_identifiant"] = identifiant
                    request.session["pre_2fa_essais"] = 0
                    request.session.set_expiry(300)
                    return redirect("comptes:verification_2fa")
                return _finaliser_connexion(request, utilisateur, identifiant)

            _enregistrer_tentative(
                request,
                identifiant,
                False,
                "Identifiants incorrects" if compte_existe is None else "Mot de passe incorrect",
            )
            if compte_existe is not None and not compte_existe.est_actif_bancaire:
                messages.error(request, "Votre compte a été bloqué par la banque. Contactez votre agence.")
            else:
                messages.error(request, "Identifiant ou mot de passe incorrect.")
    else:
        formulaire = FormulaireConnexion()

    return render(request, "comptes/connexion.html", {"formulaire": formulaire})


def verification_2fa(request):
    """Deuxième facteur : code TOTP ou code de secours à usage unique."""
    utilisateur_id = request.session.get("pre_2fa_utilisateur")
    if not utilisateur_id:
        messages.info(request, "Veuillez d'abord saisir vos identifiants.")
        return redirect("comptes:connexion")
    utilisateur = Utilisateur.objects.filter(pk=utilisateur_id, est_actif_bancaire=True).first()
    if utilisateur is None:
        request.session.pop("pre_2fa_utilisateur", None)
        return redirect("comptes:connexion")

    if request.method == "POST":
        essais = int(request.session.get("pre_2fa_essais", 0)) + 1
        request.session["pre_2fa_essais"] = essais
        code = (request.POST.get("code") or "").strip()
        secret = dechiffrer_secret(utilisateur.totp_secret_chiffre)

        valide = bool(secret) and verifier_code_totp(secret, code)
        if not valide:
            valide = verifier_et_consommer_code_secours(utilisateur, code)

        if valide:
            identifiant = request.session.pop("pre_2fa_identifiant", utilisateur.username)
            request.session.pop("pre_2fa_utilisateur", None)
            request.session.pop("pre_2fa_essais", None)
            return _finaliser_connexion(request, utilisateur, identifiant)

        _enregistrer_tentative(
            request,
            utilisateur.username,
            False,
            "Code de double authentification invalide",
        )
        if essais >= 5:
            request.session.pop("pre_2fa_utilisateur", None)
            request.session.pop("pre_2fa_essais", None)
            JournalAudit.journaliser(utilisateur, "Échec 2FA — session interrompue")
            messages.error(request, "Trop de codes invalides. Reconnectez-vous.")
            return redirect("comptes:connexion")
        messages.error(request, f"Code invalide. Tentative {essais}/5.")

    return render(
        request,
        "comptes/verification_2fa.html",
        {
            "utilisateur_masque": utilisateur.email or utilisateur.username,
            "essais": request.session.get("pre_2fa_essais", 0),
        },
    )


@login_required
def deconnexion(request):
    JournalAudit.journaliser(request.user, "Déconnexion", adresse_ip=getattr(request, "adresse_ip_client", None))
    logout(request)
    messages.info(request, "Vous avez été déconnecté(e).")
    return redirect("comptes:connexion")


@login_required
def tableau_de_bord(request):
    from decimal import Decimal

    from transactions.models import CompteBancaire

    utilisateur = request.user

    if utilisateur.est_administrateur():
        return redirect("administration:tableau_de_bord")
    if utilisateur.est_agent():
        return redirect("administration:tableau_de_bord")

    comptes = utilisateur.comptes_bancaires.select_related("devise").all()
    solde_total = sum(c.solde for c in comptes) or Decimal("0")
    solde_courants = sum(c.solde for c in comptes if c.type_compte == CompteBancaire.TypeCompte.COURANT) or Decimal("0")
    solde_epargne = sum(c.solde for c in comptes if c.type_compte == CompteBancaire.TypeCompte.EPARGNE) or Decimal("0")
    solde_bloque = sum(c.solde_bloque for c in comptes) or Decimal("0")
    solde_disponible = sum(c.solde_disponible for c in comptes) or Decimal("0")

    dernieres_transactions = Transaction.objects.filter(
        Q(compte_source__proprietaire=utilisateur) | Q(compte_destination__proprietaire=utilisateur)
    ).select_related("compte_source", "compte_destination").distinct().order_by("-date_transaction")[:6]
    derniers_paiements_mobile = PaiementMobile.objects.filter(client=utilisateur).order_by("-date_creation")[:5]

    # --- Données pour le graphique "dépenses" des 7 derniers jours (dépôts / retraits) ---
    aujourdhui = timezone.localdate()
    jours = [aujourdhui - timedelta(days=i) for i in range(6, -1, -1)]
    depots_par_jour, retraits_par_jour = [], []
    toutes_transactions_utilisateur = Transaction.objects.filter(
        Q(compte_source__proprietaire=utilisateur) | Q(compte_destination__proprietaire=utilisateur)
    ).distinct()
    for jour in jours:
        # Bornes datetime plutôt que ``__date`` : les tables de fuseaux
        # horaires MySQL peuvent ne pas être chargées (CONVERT_TZ → NULL).
        debut_jour = timezone.make_aware(datetime.combine(jour, time.min))
        fin_jour = timezone.make_aware(datetime.combine(jour + timedelta(days=1), time.min))
        depots_par_jour.append(
            float(
                toutes_transactions_utilisateur.filter(
                    type_transaction__in=[Transaction.Type.DEPOT, Transaction.Type.REMBOURSEMENT],
                    date_transaction__gte=debut_jour,
                    date_transaction__lt=fin_jour,
                    statut=Transaction.Statut.REUSSIE,
                ).aggregate(total=Sum("montant"))["total"] or 0
            )
        )
        retraits_par_jour.append(
            float(
                toutes_transactions_utilisateur.filter(
                    type_transaction__in=[
                        Transaction.Type.RETRAIT,
                        Transaction.Type.VIREMENT,
                        Transaction.Type.PAIEMENT_MOBILE,
                        Transaction.Type.PAIEMENT_FACTURE,
                        Transaction.Type.PAIEMENT_MARCHAND,
                    ],
                    compte_source__proprietaire=utilisateur,
                    date_transaction__gte=debut_jour,
                    date_transaction__lt=fin_jour,
                    statut=Transaction.Statut.REUSSIE,
                ).aggregate(total=Sum("montant"))["total"] or 0
            )
        )

    contexte = {
        "comptes": comptes,
        "solde_total": solde_total,
        "solde_courants": solde_courants,
        "solde_epargne": solde_epargne,
        "solde_bloque": solde_bloque,
        "solde_disponible": solde_disponible,
        "dernieres_transactions": dernieres_transactions,
        "derniers_paiements_mobile": derniers_paiements_mobile,
        "labels_jours": [jour.strftime("%d/%m") for jour in jours],
        "depots_par_jour": depots_par_jour,
        "retraits_par_jour": retraits_par_jour,
        "notifications_recentes": utilisateur.notifications.filter(lue=False)[:4],
        "nombre_notifications": utilisateur.notifications.filter(lue=False).count(),
    }
    return render(request, "comptes/tableau_de_bord.html", contexte)


def _fenetre_temps_reel(minutes=60, pas_minutes=5):
    """Fenêtre glissante alignée sur des tranches stables de 5 minutes."""
    maintenant_local = timezone.localtime()
    fin = maintenant_local.replace(second=0, microsecond=0)
    fin -= timedelta(minutes=fin.minute % pas_minutes)
    fin += timedelta(minutes=pas_minutes)
    debut = fin - timedelta(minutes=minutes)
    libelles = [
        timezone.localtime(debut + timedelta(minutes=index * pas_minutes)).strftime("%H:%M")
        for index in range(minutes // pas_minutes)
    ]
    return debut, fin, libelles


def _index_tranche(horodatage, debut, pas_minutes, nombre_tranches):
    index = int((horodatage - debut).total_seconds() // (pas_minutes * 60))
    if 0 <= index < nombre_tranches:
        return index
    return None


@login_required
@never_cache
def activite_temps_reel(request):
    """JSON de l'activité récente du client, interrogé par son tableau de bord."""
    pas_minutes = 5
    debut, fin, libelles = _fenetre_temps_reel(minutes=60, pas_minutes=pas_minutes)
    nombre_tranches = len(libelles)
    depots_par_tranche = [0.0] * nombre_tranches
    retraits_par_tranche = [0.0] * nombre_tranches
    comptes_ids = set(request.user.comptes_bancaires.values_list("id", flat=True))

    if comptes_ids:
        mouvements = Transaction.objects.filter(
            Q(compte_source_id__in=comptes_ids) | Q(compte_destination_id__in=comptes_ids),
            date_transaction__gte=debut,
            date_transaction__lt=fin,
            statut=Transaction.Statut.REUSSIE,
        ).values_list(
            "date_transaction",
            "montant",
            "frais",
            "compte_source_id",
            "compte_destination_id",
        )
        for date_operation, montant, frais, source_id, destination_id in mouvements:
            index = _index_tranche(date_operation, debut, pas_minutes, nombre_tranches)
            if index is None:
                continue
            if destination_id in comptes_ids:
                depots_par_tranche[index] += float(montant or 0)
            if source_id in comptes_ids:
                retraits_par_tranche[index] += float(montant or 0) + float(frais or 0)

    totaux = request.user.comptes_bancaires.aggregate(
        solde=Sum("solde"), bloque=Sum("solde_bloque")
    )
    disponible = (totaux["solde"] or Decimal("0")) - (totaux["bloque"] or Decimal("0"))

    reponse = JsonResponse(
        {
            "labels": libelles,
            "depots": [round(valeur, 2) for valeur in depots_par_tranche],
            "retraits": [round(valeur, 2) for valeur in retraits_par_tranche],
            "total_depots": round(sum(depots_par_tranche), 2),
            "total_retraits": round(sum(retraits_par_tranche), 2),
            "solde_disponible": str(disponible.quantize(Decimal("0.01"))),
            "fenetre_minutes": 60,
            "pas_minutes": pas_minutes,
            "horodatage": timezone.localtime().strftime("%H:%M:%S"),
        }
    )
    reponse["Cache-Control"] = "no-store"
    return reponse


@login_required
def profil(request):
    if request.method == "POST":
        formulaire = FormulaireProfil(request.POST, request.FILES, instance=request.user)
        if formulaire.is_valid():
            formulaire.save()
            JournalAudit.journaliser(request.user, "Modification du profil")
            messages.success(request, "Votre profil a été mis à jour.")
            return redirect("comptes:profil")
    else:
        formulaire = FormulaireProfil(instance=request.user)

    return render(
        request,
        "comptes/profil.html",
        {"formulaire": formulaire, "dossier_kyc": getattr(request.user, "dossier_kyc", None)},
    )


@login_required
def liste_beneficiaires(request):
    beneficiaires = request.user.beneficiaires.all()
    from paiement_mobile.providers import operateurs_pour_formulaire

    if request.method == "POST":
        formulaire = FormulaireBeneficiaire(request.POST)
        if formulaire.is_valid():
            beneficiaire = formulaire.save(commit=False)
            beneficiaire.proprietaire = request.user
            beneficiaire.save()
            messages.success(request, "Bénéficiaire ajouté à vos favoris.")
            return redirect("comptes:beneficiaires")
    else:
        formulaire = FormulaireBeneficiaire()

    return render(
        request,
        "comptes/beneficiaires.html",
        {
            "beneficiaires": beneficiaires,
            "formulaire": formulaire,
            "operateurs": operateurs_pour_formulaire(),
        },
    )


@login_required
@require_POST
def supprimer_beneficiaire(request, beneficiaire_id):
    beneficiaire = get_object_or_404(Beneficiaire, id=beneficiaire_id, proprietaire=request.user)
    beneficiaire.delete()
    JournalAudit.journaliser(request.user, "Suppression de bénéficiaire", beneficiaire.nom_complet)
    messages.success(request, "Bénéficiaire supprimé.")
    return redirect("comptes:beneficiaires")


# ---------------------------------------------------------------------------
# Réinitialisation du mot de passe par e-mail
# ---------------------------------------------------------------------------
def demande_reinitialisation(request):
    """
    Envoie un lien de réinitialisation par e-mail. La réponse reste
    identique que l'adresse existe ou non (pas d'énumération de comptes).
    L'e-mail est réellement remis au backend configuré : fichier local par
    défaut, console, ou SMTP si l'administrateur l'a configuré.
    """
    if request.method == "POST":
        formulaire = FormulaireDemandeReinitialisation(request.POST)
        if formulaire.is_valid():
            email = formulaire.cleaned_data["email"].strip()
            cle_throttle = f"reinit-mdp:{request.adresse_ip_client or 'inconnu'}"
            nombre = cache.get(cle_throttle, 0)
            if nombre >= 5:
                messages.warning(request, "Trop de demandes. Réessayez dans une heure.")
                return render(request, "comptes/reinitialisation_demande.html", {"formulaire": formulaire})
            cache.set(cle_throttle, nombre + 1, 3600)

            utilisateur = Utilisateur.objects.filter(email__iexact=email, est_actif_bancaire=True).first()
            if utilisateur and utilisateur.email:
                uid = urlsafe_base64_encode(force_bytes(utilisateur.pk))
                token = default_token_generator.make_token(utilisateur)
                lien = request.build_absolute_uri(
                    reverse("comptes:reinitialisation_confirmation", kwargs={"uidb64": uid, "token": token})
                )
                try:
                    contenu = render_to_string(
                        "comptes/emails/reinitialisation.txt",
                        {"utilisateur": utilisateur, "lien": lien, "validite_minutes": 60},
                    )
                    send_mail(
                        "Réinitialisation de votre mot de passe — Ma Banque",
                        contenu,
                        settings.DEFAULT_FROM_EMAIL,
                        [utilisateur.email],
                        fail_silently=False,
                    )
                    JournalAudit.journaliser(utilisateur, "Demande de réinitialisation de mot de passe")
                except Exception:  # noqa: BLE001 - ne jamais révéler l'échec technique au visiteur
                    logger.exception("Échec d'envoi de l'e-mail de réinitialisation.")
            messages.info(
                request,
                "Si un compte correspond à cet e-mail, un message contenant le lien de réinitialisation "
                "vient d'être envoyé. Pensez à vérifier vos courriers indésirables.",
            )
            return redirect("comptes:reinitialisation_demande")
    else:
        formulaire = FormulaireDemandeReinitialisation()

    return render(request, "comptes/reinitialisation_demande.html", {"formulaire": formulaire})


def _revoquer_sessions(utilisateur, garder_cle=None):
    sessions = SessionConnexion.objects.filter(utilisateur=utilisateur, est_active=True)
    if garder_cle:
        sessions = sessions.exclude(cle_session=garder_cle)
    cles = list(sessions.values_list("cle_session", flat=True))
    Session.objects.filter(session_key__in=cles).delete()
    sessions.update(est_active=False)
    return len(cles)


class ConfirmationReinitialisation(PasswordResetConfirmView):
    template_name = "comptes/reinitialisation_confirmation.html"
    success_url = reverse_lazy("comptes:connexion")

    def form_valid(self, form):
        response = super().form_valid(form)
        utilisateur = self.user
        _revoquer_sessions(utilisateur)
        JournalAudit.journaliser(utilisateur, "Mot de passe réinitialisé")
        notifier(
            utilisateur,
            "Mot de passe réinitialisé",
            "Votre mot de passe a été modifié et toutes vos sessions ont été déconnectées.",
            type_notification="SECURITE",
            niveau="ATTENTION",
            lien="/comptes/parametres/",
        )
        messages.success(self.request, "Votre mot de passe a été réinitialisé. Connectez-vous avec le nouveau.")
        return response


# ---------------------------------------------------------------------------
# Notifications in-app
# ---------------------------------------------------------------------------
@login_required
def notifications(request):
    filtre = request.GET.get("filtre", "")
    elements = request.user.notifications.all()
    if filtre == "non-lues":
        elements = elements.filter(lue=False)
    return render(
        request,
        "comptes/notifications.html",
        {
            "notifications": elements[:100],
            "filtre": filtre,
            "nb_non_lues": request.user.notifications.filter(lue=False).count(),
        },
    )


@login_required
@require_POST
def marquer_notification_lue(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, destinataire=request.user)
    if not notification.lue:
        notification.lue = True
        notification.date_lecture = timezone.now()
        notification.save(update_fields=["lue", "date_lecture"])
    if notification.lien:
        return redirect(notification.lien)
    return redirect("comptes:notifications")


@login_required
@require_POST
def marquer_toutes_notifications_lues(request):
    marquer_toutes_lues(request.user)
    messages.success(request, "Toutes vos notifications sont marquées comme lues.")
    return redirect("comptes:notifications")


@login_required
def notifications_non_lues_json(request):
    non_lues = list(
        request.user.notifications.filter(lue=False).values("id", "titre", "niveau", "lien", "date_creation")[:5]
    )
    for element in non_lues:
        element["date_creation"] = timezone.localtime(element["date_creation"]).strftime("%d/%m %H:%M")
    return JsonResponse(
        {
            "nombre": request.user.notifications.filter(lue=False).count(),
            "dernieres": non_lues,
        }
    )


# ---------------------------------------------------------------------------
# Paramètres, 2FA et sessions
# ---------------------------------------------------------------------------
@login_required
def parametres(request):
    formulaire = PasswordChangeForm(user=request.user)

    if request.method == "POST":
        formulaire = PasswordChangeForm(user=request.user, data=request.POST)
        if formulaire.is_valid():
            utilisateur = formulaire.save()
            update_session_auth_hash(request, utilisateur)  # évite la déconnexion automatique
            JournalAudit.journaliser(request.user, "Changement de mot de passe")
            notifier(
                utilisateur,
                "Mot de passe modifié",
                "Votre mot de passe vient d'être changé depuis vos paramètres de sécurité.",
                type_notification="SECURITE",
                niveau="INFO",
                lien="/comptes/parametres/",
            )
            messages.success(request, "Votre mot de passe a été mis à jour.")
            return redirect("comptes:parametres")

    return render(
        request,
        "comptes/parametres.html",
        {
            "formulaire": formulaire,
            "codes_restants": nombre_codes_secours_restants(request.user),
            "sessions_actives": SessionConnexion.objects.filter(utilisateur=request.user, est_active=True).count(),
        },
    )


@login_required
def activer_2fa(request):
    if request.user.double_authentification_active:
        messages.info(request, "La double authentification est déjà activée.")
        return redirect("comptes:parametres")

    secret = None
    if request.method == "POST":
        secret_chiffre = request.session.get("2fa_secret_temp")
        secret = dechiffrer_secret(secret_chiffre) if secret_chiffre else ""
        code = (request.POST.get("code") or "").strip()
        if secret and verifier_code_totp(secret, code):
            request.user.totp_secret_chiffre = chiffrer_secret(secret)
            request.user.double_authentification_active = True
            request.user.date_activation_2fa = timezone.now()
            request.user.save(update_fields=["totp_secret_chiffre", "double_authentification_active", "date_activation_2fa"])
            request.session.pop("2fa_secret_temp", None)
            codes = generer_codes_secours(request.user)
            request.session["codes_secours_a_afficher"] = codes
            JournalAudit.journaliser(request.user, "Activation de la double authentification")
            notifier(
                request.user,
                "Double authentification activée",
                "Votre compte est désormais protégé par un code à usage unique.",
                type_notification="SECURITE",
                niveau="SUCCES",
                lien="/comptes/parametres/",
            )
            messages.success(request, "Double authentification activée. Conservez vos codes de secours.")
            return redirect("comptes:codes_secours")
        messages.error(request, "Code invalide ou expiré. Vérifiez l'heure de votre téléphone et réessayez.")

    if not secret:
        secret = generer_secret_totp()
        request.session["2fa_secret_temp"] = chiffrer_secret(secret)
    uri = uri_provisionnement(request.user, secret)
    return render(
        request,
        "comptes/activer_2fa.html",
        {"secret": secret, "uri": uri, "qr_data_uri": image_qr_data_uri(uri)},
    )


@login_required
def codes_secours(request):
    codes = request.session.pop("codes_secours_a_afficher", None)
    if not codes:
        messages.info(request, "Les codes de secours ne sont affichés qu'une seule fois lors de leur génération.")
        return redirect("comptes:parametres")
    return render(request, "comptes/codes_secours.html", {"codes": codes})


@login_required
@require_POST
def regenerer_codes_secours(request):
    mot_de_passe = request.POST.get("mot_de_passe", "")
    code = request.POST.get("code", "")
    if not request.user.check_password(mot_de_passe):
        messages.error(request, "Mot de passe incorrect.")
        return redirect("comptes:parametres")
    secret = dechiffrer_secret(request.user.totp_secret_chiffre)
    if not secret or not verifier_code_totp(secret, code):
        messages.error(request, "Code d'authentification invalide.")
        return redirect("comptes:parametres")
    codes = generer_codes_secours(request.user)
    request.session["codes_secours_a_afficher"] = codes
    JournalAudit.journaliser(request.user, "Régénération des codes de secours")
    messages.success(request, "Nouveaux codes de secours générés. Les anciens ne sont plus valables.")
    return redirect("comptes:codes_secours")


@login_required
@require_POST
def desactiver_2fa(request):
    mot_de_passe = request.POST.get("mot_de_passe", "")
    code = request.POST.get("code", "")
    if not request.user.check_password(mot_de_passe):
        messages.error(request, "Mot de passe incorrect : désactivation refusée.")
        return redirect("comptes:parametres")
    secret = dechiffrer_secret(request.user.totp_secret_chiffre)
    valide = bool(secret) and verifier_code_totp(secret, code)
    if not valide:
        valide = verifier_et_consommer_code_secours(request.user, code)
    if not valide:
        messages.error(request, "Code d'authentification invalide : désactivation refusée.")
        return redirect("comptes:parametres")

    utilisateur = request.user
    utilisateur.double_authentification_active = False
    utilisateur.totp_secret_chiffre = ""
    utilisateur.date_activation_2fa = None
    utilisateur.save(update_fields=["double_authentification_active", "totp_secret_chiffre", "date_activation_2fa"])
    utilisateur.codes_secours.all().delete()
    JournalAudit.journaliser(utilisateur, "Désactivation de la double authentification")
    notifier(
        utilisateur,
        "Double authentification désactivée",
        "La protection par code à usage unique a été retirée de votre compte.",
        type_notification="SECURITE",
        niveau="CRITIQUE",
        lien="/comptes/parametres/",
    )
    messages.warning(request, "Double authentification désactivée. Pensez à la réactiver.")
    return redirect("comptes:parametres")


@login_required
def sessions_actives(request):
    maintenant = timezone.now()
    connexions = list(
        SessionConnexion.objects.filter(utilisateur=request.user, est_active=True).order_by("-derniere_activite")
    )
    for connexion in connexions:
        existe = Session.objects.filter(session_key=connexion.cle_session, expire_date__gt=maintenant).exists()
        if not existe:
            connexion.est_active = False
            connexion.save(update_fields=["est_active"])
    connexions = [c for c in connexions if c.est_active]
    return render(
        request,
        "comptes/sessions.html",
        {
            "sessions": connexions,
            "cle_courante": request.session.session_key,
            "historique": TentativeConnexion.objects.filter(
                Q(utilisateur=request.user) | Q(identifiant=request.user.username)
            )[:25],
        },
    )


@login_required
@require_POST
def revoquer_session(request, session_id):
    connexion = get_object_or_404(SessionConnexion, id=session_id, utilisateur=request.user)
    if connexion.cle_session == request.session.session_key:
        messages.error(request, "Utilisez le bouton de déconnexion pour terminer la session en cours.")
        return redirect("comptes:sessions")
    with transaction.atomic():
        Session.objects.filter(session_key=connexion.cle_session).delete()
        connexion.est_active = False
        connexion.save(update_fields=["est_active"])
    JournalAudit.journaliser(request.user, "Révocation d'une session", connexion.appareil or connexion.cle_session[:8])
    messages.success(request, "La session a été révoquée.")
    return redirect("comptes:sessions")


@login_required
@require_POST
def revoquer_autres_sessions(request):
    nombre = _revoquer_sessions(request.user, garder_cle=request.session.session_key)
    JournalAudit.journaliser(request.user, "Révocation des autres sessions", f"{nombre} session(s)")
    messages.success(request, f"{nombre} autre(s) session(s) ont été déconnectée(s).")
    return redirect("comptes:sessions")


@login_required
def fonctionnalite_a_venir(request, cle):
    """
    Certaines entrées de menu de démonstration mènent à une explication
    plutôt qu'à une fonctionnalité factice. Les modules réellement
    implémentés disposent de leurs propres pages.
    """
    intitules = {
        "carte": ("Gestion de carte bancaire", "La gestion des cartes est disponible dans le module Cartes."),
        "mandats-emission": ("Émission de mandats", "L'émission de mandats de paiement est une fonctionnalité bonus non couverte par le cahier des charges initial."),
        "mandats-encaissement": ("Encaissement des mandats", "L'encaissement de mandats (CNSS, CNAM...) est une fonctionnalité bonus non couverte par le cahier des charges initial."),
        "microcredit": ("Microcrédit", "L'octroi de microcrédits nécessiterait un module de scoring et d'engagement contractuel non prévu dans ce PFE."),
        "paiement-marchand": ("Paiement marchand par QR code", "Le paiement marchand par QR code est disponible dans le module Réseau marchand."),
    }
    titre, description = intitules.get(cle, ("Fonctionnalité", "Cette fonctionnalité n'est pas encore disponible."))
    return render(request, "comptes/bientot.html", {"titre": titre, "description": description})


# ---------------------------------------------------------------------------
# Simulateur USSD (canal téléphone, sans passerelle télécom réelle)
# ---------------------------------------------------------------------------
def _montant_ussd(valeur):
    return f"{valeur:,.0f}".replace(",", " ")


@login_required
def simulateur_ussd(request):
    """
    Simulateur de menu USSD accessible dans la session web authentifiée.

    Aucun code PIN, numéro de téléphone ni OTP n'est demandé : le vrai canal
    USSD exigerait une passerelle opérateur et une authentification dédiée.
    Seules les données du client connecté sont affichées, et rien n'est stocké
    en session hormis l'écran courant.
    """
    etats_valides = {"accueil", "menu", "solde", "releve", "cartes", "aide", "quitte"}
    etat = request.session.get("ussd_ecran", "accueil")
    if etat not in etats_valides:
        etat = "accueil"

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "composer":
            etat = "menu"
        elif action in {"retour", "menu"}:
            etat = "menu"
        elif action == "quitter":
            etat = "quitte"
        elif action == "choix":
            choix = (request.POST.get("choix") or "").strip()
            if etat == "menu":
                etat = {"1": "solde", "2": "releve", "3": "cartes", "4": "aide", "0": "quitte"}.get(
                    choix, "menu"
                )
            elif choix == "0":
                etat = "menu"
        else:
            etat = "menu"
        request.session["ussd_ecran"] = etat

    lignes = []
    if etat == "solde":
        comptes = request.user.comptes_bancaires.select_related("devise").order_by("date_ouverture")
        if not comptes:
            lignes = ["Aucun compte bancaire rattaché."]
        else:
            total = sum((compte.solde_disponible for compte in comptes), Decimal("0"))
            for compte in comptes[:4]:
                lignes.append(
                    f"{compte.get_type_compte_display()} {compte.numero_compte}\n"
                    f"  Disponible : {_montant_ussd(compte.solde_disponible)} {compte.code_devise}"
                )
            lignes.append(f"TOTAL DISPONIBLE : {_montant_ussd(total)} XAF")
    elif etat == "releve":
        transactions = (
            Transaction.objects.filter(
                Q(compte_source__proprietaire=request.user)
                | Q(compte_destination__proprietaire=request.user)
            )
            .select_related("compte_source", "compte_destination")
            .order_by("-date_transaction")[:5]
        )
        if not transactions:
            lignes = ["Aucune opération récente."]
        else:
            for operation in transactions:
                sens = "-"
                if operation.compte_destination_id and (
                    operation.compte_destination.proprietaire_id == request.user.id
                ):
                    sens = "+"
                lignes.append(
                    f"{timezone.localtime(operation.date_transaction):%d/%m} "
                    f"{operation.reference} {sens}{_montant_ussd(operation.montant)} FCFA"
                )
    elif etat == "cartes":
        from cartes.models import CarteBancaire

        cartes = CarteBancaire.objects.filter(utilisateur=request.user).order_by("-date_creation")[:4]
        if not cartes:
            lignes = ["Aucune carte bancaire. Consultez Mes cartes."]
        else:
            for carte in cartes:
                lignes.append(
                    f"{carte.get_type_carte_display()} •••• {carte.quatre_derniers}\n"
                    f"  {carte.get_statut_display()}"
                )
    elif etat == "aide":
        lignes = [
            "Support : Aide > FAQ ou Ouvrir un ticket.",
            "Urgence carte : gelez-la depuis Mes cartes.",
            "Ne communiquez jamais votre mot de passe, PIN ou code 2FA.",
        ]

    return render(
        request,
        "comptes/ussd.html",
        {"etat": etat, "lignes": lignes},
    )
