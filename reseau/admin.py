"""Administration du réseau d'agents et de marchands."""

from django.contrib import admin, messages

from comptes.models import JournalAudit

from .models import DemandePaiementMarchand, Marchand, OperationAgent, ProfilAgent


@admin.register(ProfilAgent)
class ProfilAgentAdmin(admin.ModelAdmin):
    list_display = ("code_agent", "nom_agence", "utilisateur", "ville", "float_caisse", "actif", "date_creation")
    list_filter = ("actif", "ville")
    search_fields = ("code_agent", "nom_agence", "utilisateur__username", "utilisateur__first_name", "utilisateur__last_name")
    readonly_fields = ("code_agent", "date_creation")
    actions = ("activer_agents", "desactiver_agents")

    @admin.action(description="Activer les agents sélectionnés")
    def activer_agents(self, request, queryset):
        nombre = queryset.update(actif=True)
        JournalAudit.journaliser(request.user, "Activation d'agents", f"{nombre} profil(s) activé(s)")
        messages.success(request, f"{nombre} profil(s) agent activé(s).")

    @admin.action(description="Désactiver les agents sélectionnés")
    def desactiver_agents(self, request, queryset):
        nombre = queryset.update(actif=False)
        JournalAudit.journaliser(request.user, "Désactivation d'agents", f"{nombre} profil(s) désactivé(s)")
        messages.success(request, f"{nombre} profil(s) agent désactivé(s).")


@admin.register(OperationAgent)
class OperationAgentAdmin(admin.ModelAdmin):
    list_display = ("reference", "date_operation", "agent", "client", "compte", "type_operation", "montant", "statut")
    list_filter = ("type_operation", "statut", "agent__nom_agence")
    search_fields = ("reference", "agent__code_agent", "client__username", "compte__numero_compte")
    readonly_fields = ("reference", "agent", "client", "compte", "type_operation", "montant", "statut", "transaction", "date_operation")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class DemandePaiementInline(admin.TabularInline):
    model = DemandePaiementMarchand
    extra = 0
    fields = ("montant", "description", "statut", "date_creation", "date_expiration")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Marchand)
class MarchandAdmin(admin.ModelAdmin):
    list_display = ("code_marchand", "nom", "categorie", "ville", "proprietaire", "compte_reglement", "statut", "date_creation")
    list_filter = ("statut", "categorie", "ville")
    search_fields = ("code_marchand", "nom", "ville", "proprietaire__username", "compte_reglement__numero_compte")
    readonly_fields = ("code_marchand", "date_creation")
    inlines = (DemandePaiementInline,)
    actions = ("activer_marchands", "suspendre_marchands")

    @admin.action(description="Activer les marchands sélectionnés")
    def activer_marchands(self, request, queryset):
        nombre = queryset.update(statut=Marchand.Statut.ACTIF)
        JournalAudit.journaliser(request.user, "Activation de marchands", f"{nombre} marchand(s) activé(s)")
        messages.success(request, f"{nombre} marchand(s) activé(s).")

    @admin.action(description="Suspendre les marchands sélectionnés")
    def suspendre_marchands(self, request, queryset):
        nombre = queryset.update(statut=Marchand.Statut.SUSPENDU)
        JournalAudit.journaliser(request.user, "Suspension de marchands", f"{nombre} marchand(s) suspendu(s)")
        messages.success(request, f"{nombre} marchand(s) suspendu(s).")


@admin.register(DemandePaiementMarchand)
class DemandePaiementMarchandAdmin(admin.ModelAdmin):
    list_display = ("id", "marchand", "montant", "description", "statut", "date_creation", "date_expiration", "payeur")
    list_filter = ("statut", "marchand")
    search_fields = ("marchand__nom", "marchand__code_marchand", "payeur__username")
    readonly_fields = ("marchand", "jeton", "montant", "description", "statut", "date_creation", "date_expiration", "date_paiement", "payeur", "transaction")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
