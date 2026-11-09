"""Administration des factures simulées.

Aucun fournisseur réel n'est connecté. L'administration ne permet pas de
marquer une facture payée manuellement : seuls les paiements réels tracés
par l'application changent son statut.
"""

from django.contrib import admin

from .models import CompteFacture, Facture, FournisseurFacture


@admin.register(FournisseurFacture)
class FournisseurFactureAdmin(admin.ModelAdmin):
    list_display = ("nom", "code", "categorie", "est_simule", "actif", "date_creation")
    list_filter = ("categorie", "actif", "est_simule")
    search_fields = ("nom", "code")
    readonly_fields = ("est_simule", "date_creation")


@admin.register(CompteFacture)
class CompteFactureAdmin(admin.ModelAdmin):
    list_display = ("fournisseur", "reference_client", "proprietaire", "libelle", "est_favori", "date_creation")
    list_filter = ("fournisseur", "est_favori")
    search_fields = ("reference_client", "libelle", "proprietaire__username", "fournisseur__nom")


@admin.register(Facture)
class FactureAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "fournisseur",
        "proprietaire",
        "montant",
        "statut",
        "date_echeance",
        "date_paiement",
    )
    list_filter = ("statut", "fournisseur")
    search_fields = ("reference", "reference_externe", "libelle", "proprietaire__username")
    readonly_fields = (
        "reference",
        "transaction",
        "date_paiement",
        "date_creation",
    )
    actions = ["expirer_factures", "annuler_factures"]

    @admin.action(description="Marquer les factures en attente comme expirées")
    def expirer_factures(self, request, queryset):
        queryset.filter(statut=Facture.Statut.EN_ATTENTE).update(statut=Facture.Statut.EXPIREE)

    @admin.action(description="Annuler les factures en attente")
    def annuler_factures(self, request, queryset):
        queryset.filter(statut=Facture.Statut.EN_ATTENTE).update(statut=Facture.Statut.ANNULEE)
