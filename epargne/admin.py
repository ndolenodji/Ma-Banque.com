"""Administration de l'épargne et des budgets."""

from django.contrib import admin

from .models import Budget, ConfigArrondi, ContributionEpargne, ObjectifEpargne


class ContributionInline(admin.TabularInline):
    model = ContributionEpargne
    extra = 0
    readonly_fields = ("transaction", "montant", "type_contribution", "date_creation")
    can_delete = False


@admin.register(ObjectifEpargne)
class ObjectifEpargneAdmin(admin.ModelAdmin):
    list_display = ("nom", "proprietaire", "montant_actuel", "montant_cible", "progression", "statut", "date_cible")
    list_filter = ("statut",)
    search_fields = ("nom", "proprietaire__username")
    readonly_fields = ("montant_actuel", "date_creation", "date_mise_a_jour")
    inlines = [ContributionInline]


@admin.register(ContributionEpargne)
class ContributionEpargneAdmin(admin.ModelAdmin):
    list_display = ("objectif", "montant", "type_contribution", "date_creation")
    list_filter = ("type_contribution",)
    search_fields = ("objectif__nom", "objectif__proprietaire__username")
    readonly_fields = ("objectif", "transaction", "montant", "type_contribution", "date_creation")

    def has_add_permission(self, request):
        return False


@admin.register(ConfigArrondi)
class ConfigArrondiAdmin(admin.ModelAdmin):
    list_display = ("proprietaire", "actif", "compte_source", "compte_epargne", "multiple", "objectif")
    list_filter = ("actif", "multiple")
    search_fields = ("proprietaire__username",)


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("proprietaire", "categorie", "plafond_mensuel", "seuil_alerte", "actif")
    list_filter = ("categorie", "actif")
    search_fields = ("proprietaire__username",)
