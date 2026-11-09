"""Administration des devises et des taux.

Aucun taux ne peut être créé automatiquement : l'administrateur saisit le
taux et sa source. La devise XAF reste la référence (taux = 1).
"""

from django.contrib import admin

from .models import Devise


@admin.register(Devise)
class DeviseAdmin(admin.ModelAdmin):
    list_display = ("code", "nom", "symbole", "est_active", "taux_vers_xaf", "source_taux", "date_maj_taux")
    list_filter = ("est_active",)
    search_fields = ("code", "nom")
    readonly_fields = ("date_maj_taux", "date_creation")
    fieldsets = (
        ("Identification", {"fields": ("code", "nom", "symbole", "est_active")}),
        (
            "Taux administré",
            {
                "fields": ("taux_vers_xaf", "source_taux", "date_maj_taux"),
                "description": (
                    "Saisissez le nombre de XAF pour 1 unité de cette devise, ainsi que la source "
                    "du taux (ex. « taux indicatif BEAC du 20/10/2026, saisi par l'administrateur »). "
                    "Aucun taux n'est généré automatiquement."
                ),
            },
        ),
        ("Horodatage", {"fields": ("date_creation",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        champs = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.code == "XAF":
            champs.append("code")
            champs.append("taux_vers_xaf")
        return champs
