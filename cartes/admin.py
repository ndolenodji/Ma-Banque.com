"""Administration des cartes de démonstration.

Les identifiants sont en lecture seule : aucun PAN, CVV ou PIN réel n'existe.
Les actions d'administration sont tracées dans le journal d'audit.
"""

from django.contrib import admin

from comptes.models import JournalAudit

from .models import CarteBancaire


@admin.register(CarteBancaire)
class CarteBancaireAdmin(admin.ModelAdmin):
    list_display = (
        "numero_masque",
        "identifiant_demo",
        "utilisateur",
        "compte",
        "type_carte",
        "statut",
        "date_expiration",
        "date_creation",
    )
    list_filter = ("statut", "type_carte", "paiements_en_ligne", "paiements_internationaux")
    search_fields = ("identifiant_demo", "nom_titulaire", "utilisateur__username", "compte__numero_compte")
    readonly_fields = (
        "identifiant_demo",
        "quatre_derniers",
        "numero_masque",
        "reseau",
        "date_creation",
        "date_mise_a_jour",
    )
    fieldsets = (
        ("Titulaire et compte", {"fields": ("utilisateur", "compte", "nom_titulaire", "type_carte")}),
        ("Identifiant de démonstration", {"fields": ("identifiant_demo", "quatre_derniers", "numero_masque", "reseau")}),
        ("Cycle de vie", {"fields": ("statut", "date_expiration", "motif_annulation")}),
        ("Contrôles", {"fields": ("paiements_en_ligne", "paiements_internationaux", "retraits_autorises")}),
        ("Plafonds", {"fields": ("plafond_journalier", "plafond_mensuel")}),
        ("Horodatage", {"fields": ("date_creation", "date_mise_a_jour")}),
    )
    actions = ["activer_cartes", "suspendre_cartes", "annuler_cartes"]

    @admin.action(description="Activer les cartes sélectionnées")
    def activer_cartes(self, request, queryset):
        for carte in queryset.exclude(statut=CarteBancaire.Statut.ANNULEE):
            if carte.statut != CarteBancaire.Statut.ACTIVE:
                carte.statut = CarteBancaire.Statut.ACTIVE
                carte.save(update_fields=["statut", "date_mise_a_jour"])
                JournalAudit.journaliser(
                    request.user, "Activation de carte (admin)", f"{carte.numero_masque} — {carte.utilisateur}"
                )

    @admin.action(description="Geler les cartes sélectionnées")
    def suspendre_cartes(self, request, queryset):
        for carte in queryset.filter(statut=CarteBancaire.Statut.ACTIVE):
            carte.geler()
            JournalAudit.journaliser(
                request.user, "Gel de carte (admin)", f"{carte.numero_masque} — {carte.utilisateur}"
            )

    @admin.action(description="Annuler les cartes sélectionnées")
    def annuler_cartes(self, request, queryset):
        for carte in queryset.exclude(statut=CarteBancaire.Statut.ANNULEE):
            carte.annuler("Annulation prononcée par l'administration")
            JournalAudit.journaliser(
                request.user, "Annulation de carte (admin)", f"{carte.numero_masque} — {carte.utilisateur}"
            )
