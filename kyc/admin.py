"""Administration Django native des dossiers KYC (accès réservé aux admins métier)."""

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from comptes.models import JournalAudit
from comptes.services_notifications import notifier

from .models import DocumentKYC, DossierKYC, HistoriqueKYC


class DocumentKYCInline(admin.TabularInline):
    model = DocumentKYC
    extra = 0
    fields = ("type_document", "fichier", "statut", "taille_octets", "empreinte_sha256", "date_ajout")
    readonly_fields = ("taille_octets", "empreinte_sha256", "date_ajout")
    can_delete = False


class HistoriqueKYCInline(admin.TabularInline):
    model = HistoriqueKYC
    extra = 0
    fields = ("action", "ancien_statut", "nouveau_statut", "acteur", "commentaire", "date_action")
    readonly_fields = ("date_action",)
    can_delete = False


@admin.register(DossierKYC)
class DossierKYCAdmin(admin.ModelAdmin):
    list_display = ("utilisateur", "statut", "type_piece", "numero_piece", "date_soumission", "traite_par")
    list_filter = ("statut", "type_piece")
    search_fields = ("utilisateur__username", "utilisateur__last_name", "numero_piece")
    readonly_fields = ("date_creation", "date_mise_a_jour", "date_soumission", "date_traitement")
    inlines = [DocumentKYCInline, HistoriqueKYCInline]
    actions = ["approuver_dossiers"]

    @admin.action(description="Approuver les dossiers sélectionnés (contrôle documents requis)")
    def approuver_dossiers(self, request, queryset):
        approuves = 0
        refuses = 0
        for dossier in queryset:
            if dossier.statut not in (DossierKYC.Statut.EN_ATTENTE, DossierKYC.Statut.REJETE):
                refuses += 1
                continue
            if dossier.documents_obligatoires_presents():
                ancien_statut = dossier.statut
                dossier.statut = DossierKYC.Statut.APPROUVE
                dossier.traite_par = request.user
                dossier.date_traitement = timezone.now()
                dossier.save(
                    update_fields=[
                        "statut",
                        "traite_par",
                        "date_traitement",
                        "date_mise_a_jour",
                    ]
                )
                dossier.documents.filter(est_actif=True).update(
                    statut=DocumentKYC.Statut.VALIDE, motif_rejet=""
                )
                dossier.utilisateur.identite_verifiee = True
                dossier.utilisateur.save(update_fields=["identite_verifiee"])
                HistoriqueKYC.objects.create(
                    dossier=dossier,
                    acteur=request.user,
                    action=HistoriqueKYC.Action.APPROBATION,
                    ancien_statut=ancien_statut,
                    nouveau_statut=dossier.statut,
                    commentaire="Approbation par action groupée depuis l'administration Django.",
                )
                notifier(
                    dossier.utilisateur,
                    "Identité vérifiée",
                    "Votre dossier d'identité a été approuvé. Vous pouvez désormais ouvrir de nouveaux comptes.",
                    type_notification="KYC",
                    niveau="SUCCES",
                    lien="/kyc/",
                )
                JournalAudit.journaliser(
                    request.user,
                    "KYC — dossier approuvé (admin)",
                    f"Client {dossier.utilisateur} — dossier #{dossier.pk}",
                )
                approuves += 1
            else:
                refuses += 1
        self.message_user(request, f"{approuves} dossier(s) approuvé(s), {refuses} ignoré(s) (statut ou pièces incomplètes).")


@admin.register(DocumentKYC)
class DocumentKYCAdmin(admin.ModelAdmin):
    list_display = ("dossier", "type_document", "statut", "taille_octets", "date_ajout")
    list_filter = ("statut", "type_document")
    search_fields = ("dossier__utilisateur__username",)
    readonly_fields = ("empreinte_sha256", "taille_octets", "date_ajout", "apercu")

    def apercu(self, obj):
        if obj.pk:
            return format_html('<a href="/kyc/documents/{}/telecharger/">Télécharger (contrôlé)</a>', obj.pk)
        return "—"

    apercu.short_description = "Document"
