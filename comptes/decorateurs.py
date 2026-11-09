"""
Décorateurs de gestion des permissions par rôle.

Utilisés en complément de @login_required pour restreindre certaines vues
aux agents et/ou administrateurs (principe du moindre privilège).
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def role_requis(*roles_autorises):
    """
    Autorise l'accès à la vue uniquement si request.user.role est dans
    roles_autorises (ou si l'utilisateur est superutilisateur).

    Exemple : @role_requis("ADMIN", "AGENT")
    """

    def decorateur(vue):
        @wraps(vue)
        @login_required
        def _vue_enveloppee(request, *args, **kwargs):
            utilisateur = request.user
            if utilisateur.is_superuser or utilisateur.role in roles_autorises:
                return vue(request, *args, **kwargs)
            messages.error(request, "Vous n'avez pas les droits nécessaires pour accéder à cette page.")
            return redirect("comptes:tableau_de_bord")

        return _vue_enveloppee

    return decorateur


def admin_requis(vue):
    return role_requis("ADMIN")(vue)


def agent_ou_admin_requis(vue):
    return role_requis("AGENT", "ADMIN")(vue)
