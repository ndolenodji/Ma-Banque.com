from django.contrib import admin

from .models import PaiementMobile


@admin.register(PaiementMobile)
class PaiementMobileAdmin(admin.ModelAdmin):
    list_display = (
        "reference_transaction", "client", "type_operation", "operateur",
        "numero_telephone", "montant", "statut", "date_creation",
    )
    list_filter = ("type_operation", "operateur", "statut")
    search_fields = ("reference_transaction", "reference_externe", "numero_telephone", "client__username")
