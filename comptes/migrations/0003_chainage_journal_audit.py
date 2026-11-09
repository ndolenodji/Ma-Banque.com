import hashlib

from django.db import migrations


def construire_chaine(apps, schema_editor):
    JournalAudit = apps.get_model("comptes", "JournalAudit")
    precedente = ""
    for entree in JournalAudit.objects.order_by("id").iterator():
        entree.empreinte_precedente = precedente
        charge = "|".join(
            [
                precedente,
                str(entree.utilisateur_id or ""),
                entree.action or "",
                entree.details or "",
                entree.adresse_ip or "",
                entree.date_action.isoformat() if entree.date_action else "",
            ]
        )
        entree.empreinte = hashlib.sha256(charge.encode("utf-8")).hexdigest()
        entree.save(update_fields=["empreinte_precedente", "empreinte"])
        precedente = entree.empreinte


def defaire(apps, schema_editor):
    JournalAudit = apps.get_model("comptes", "JournalAudit")
    JournalAudit.objects.update(empreinte="", empreinte_precedente="")


class Migration(migrations.Migration):
    dependencies = [("comptes", "0002_codesecours_notification_sessionconnexion_smssimule_and_more")]
    operations = [migrations.RunPython(construire_chaine, defaire)]
