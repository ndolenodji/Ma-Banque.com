"""Administration des alertes de fraude.

Le contenu est en lecture seule : toute décision passe par un service dédié
qui contrôle les transitions, journalise l'action et notifie le client.
"""

from django.contrib import admin, messages

from .models import AlerteFraude
from .services import traiter_alerte


@admin.register(AlerteFraude)
class AlerteFraudeAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "date_creation",
        "utilisateur",
        "score",
        "niveau",
        "statut",
        "traite_par",
    )
    list_filter = ("statut", "niveau")
    search_fields = (
        "reference",
        "utilisateur__username",
        "utilisateur__last_name",
        "transaction__reference",
    )
    readonly_fields = (
        "reference",
        "utilisateur",
        "transaction",
        "paiement_mobile",
        "score",
        "niveau",
        "regles",
        "explication",
        "date_creation",
        "traite_par",
        "commentaire",
        "date_traitement",
    )
    list_select_related = ("utilisateur", "traite_par")
    actions = ("prendre_en_revue",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Prendre en revue les alertes sélectionnées")
    def prendre_en_revue(self, request, queryset):
        traitees = 0
        ignorees = 0
        for alerte in queryset.filter(statut=AlerteFraude.Statut.NOUVELLE):
            try:
                traiter_alerte(
                    alerte,
                    request.user,
                    AlerteFraude.Statut.EN_REVUE,
                    "Prise en revue depuis l'administration Django.",
                )
            except ValueError:
                ignorees += 1
            else:
                traitees += 1
        if traitees:
            messages.success(request, f"{traitees} alerte(s) prise(s) en revue.")
        if ignorees:
            messages.warning(request, f"{ignorees} alerte(s) ignorée(s).")
