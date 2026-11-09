"""Administration du support client."""

from django.contrib import admin, messages
from django.utils import timezone

from comptes.models import JournalAudit

from .models import ArticleFAQ, MessageSupport, TicketSupport


@admin.register(ArticleFAQ)
class ArticleFAQAdmin(admin.ModelAdmin):
    list_display = ("question", "categorie", "ordre", "publie", "date_creation")
    list_filter = ("categorie", "publie")
    search_fields = ("question", "reponse")
    list_editable = ("ordre", "publie")


class MessageSupportInline(admin.TabularInline):
    model = MessageSupport
    extra = 0
    fields = ("message", "est_note_interne", "date_creation")
    readonly_fields = ("date_creation",)
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TicketSupport)
class TicketSupportAdmin(admin.ModelAdmin):
    list_display = ("reference", "sujet", "client", "categorie", "priorite", "statut", "assigne_a", "date_mise_a_jour")
    list_filter = ("statut", "priorite", "categorie")
    search_fields = ("reference", "sujet", "client__username", "client__email")
    readonly_fields = ("reference", "client", "date_creation", "date_mise_a_jour", "date_cloture")
    inlines = (MessageSupportInline,)
    actions = ("marquer_en_cours", "marquer_resolus")

    @admin.action(description="Marquer les tickets sélectionnés en cours")
    def marquer_en_cours(self, request, queryset):
        nombre = queryset.update(statut=TicketSupport.Statut.EN_COURS, date_cloture=None)
        JournalAudit.journaliser(request.user, "Tickets support en cours", f"{nombre} ticket(s)")
        messages.success(request, f"{nombre} ticket(s) marqué(s) en cours.")

    @admin.action(description="Marquer les tickets sélectionnés comme résolus")
    def marquer_resolus(self, request, queryset):
        nombre = queryset.update(statut=TicketSupport.Statut.RESOLU, date_cloture=timezone.now())
        JournalAudit.journaliser(request.user, "Tickets support résolus", f"{nombre} ticket(s)")
        messages.success(request, f"{nombre} ticket(s) marqué(s) comme résolu(s).")


@admin.register(MessageSupport)
class MessageSupportAdmin(admin.ModelAdmin):
    list_display = ("ticket", "auteur", "est_note_interne", "date_creation")
    list_filter = ("est_note_interne",)
    search_fields = ("ticket__reference", "message", "auteur__username")
    readonly_fields = ("ticket", "auteur", "message", "est_note_interne", "date_creation")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
