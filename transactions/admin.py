from django.contrib import admin

from comptes.models import JournalAudit

from .models import CompteBancaire, Transaction


@admin.register(CompteBancaire)
class CompteBancaireAdmin(admin.ModelAdmin):
    list_display = ("numero_compte", "proprietaire", "type_compte", "solde", "statut", "date_ouverture")
    list_filter = ("type_compte", "statut")
    search_fields = ("numero_compte", "proprietaire__username", "proprietaire__last_name")
    actions = ["bloquer", "debloquer"]

    @admin.action(description="Bloquer les comptes sélectionnés (fraude suspectée)")
    def bloquer(self, request, queryset):
        queryset.update(statut=CompteBancaire.Statut.BLOQUE)
        for compte in queryset:
            JournalAudit.journaliser(request.user, "Blocage de compte (admin)", str(compte))

    @admin.action(description="Débloquer les comptes sélectionnés")
    def debloquer(self, request, queryset):
        queryset.update(statut=CompteBancaire.Statut.ACTIF)
        for compte in queryset:
            JournalAudit.journaliser(request.user, "Déblocage de compte (admin)", str(compte))


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "type_transaction", "montant", "compte_source", "compte_destination", "statut", "date_transaction")
    list_filter = ("type_transaction", "statut")
    search_fields = ("compte_source__numero_compte", "compte_destination__numero_compte", "reference")
    # Pas de date_hierarchy : MySQL sans tables de fuseaux ne peut pas
    # convertir les dates de cette colonne (erreur de rendu de l'admin).
    list_select_related = ("compte_source", "compte_destination")
    readonly_fields = ("reference", "operation_id", "date_transaction")
