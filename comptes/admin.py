from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Beneficiaire, JournalAudit, TentativeConnexion, Utilisateur


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "telephone", "identite_verifiee", "est_actif_bancaire", "is_active")
    list_filter = ("role", "identite_verifiee", "est_actif_bancaire")
    search_fields = ("username", "first_name", "last_name", "email", "telephone")
    fieldsets = UserAdmin.fieldsets + (
        ("Informations bancaires", {
            "fields": ("role", "telephone", "adresse", "numero_piece_identite", "photo_piece_identite",
                       "identite_verifiee", "est_actif_bancaire"),
        }),
    )
    # Le formulaire d'ajout doit contenir le téléphone : champ obligatoire et
    # unique. Sans lui, l'enregistrement insérait telephone="" et la deuxième
    # création échouait sur la contrainte d'unicité (erreur 500).
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "usable_password", "password1", "password2"),
        }),
        ("Informations bancaires", {
            "classes": ("wide",),
            "fields": ("role", "telephone", "first_name", "last_name", "email"),
        }),
    )
    actions = ["bloquer_comptes", "debloquer_comptes", "verifier_identite"]

    @admin.action(description="Bloquer les comptes sélectionnés (fraude suspectée)")
    def bloquer_comptes(self, request, queryset):
        queryset.update(est_actif_bancaire=False)
        for utilisateur in queryset:
            JournalAudit.journaliser(request.user, "Blocage utilisateur (admin)", str(utilisateur))

    @admin.action(description="Débloquer les comptes sélectionnés")
    def debloquer_comptes(self, request, queryset):
        queryset.update(est_actif_bancaire=True)
        for utilisateur in queryset:
            JournalAudit.journaliser(request.user, "Déblocage utilisateur (admin)", str(utilisateur))

    @admin.action(description="Marquer l'identité comme vérifiée (KYC)")
    def verifier_identite(self, request, queryset):
        queryset.update(identite_verifiee=True)


@admin.register(Beneficiaire)
class BeneficiaireAdmin(admin.ModelAdmin):
    list_display = ("nom_complet", "numero_compte", "proprietaire", "date_ajout")
    search_fields = ("nom_complet", "numero_compte")


@admin.register(JournalAudit)
class JournalAuditAdmin(admin.ModelAdmin):
    list_display = ("date_action", "utilisateur", "action", "adresse_ip")
    list_filter = ("action",)
    search_fields = ("action", "details", "utilisateur__username")
    ordering = ("-date_action",)

    def has_add_permission(self, request):
        return False  # le journal ne doit être créé que par le code, jamais manuellement

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TentativeConnexion)
class TentativeConnexionAdmin(admin.ModelAdmin):
    list_display = ("identifiant", "reussie", "adresse_ip", "horodatage")
    list_filter = ("reussie",)
    search_fields = ("identifiant",)
