from django.db import migrations


def creer_devise_reference(apps, schema_editor):
    Devise = apps.get_model("devises", "Devise")
    Devise.objects.update_or_create(
        code="XAF",
        defaults={
            "nom": "Franc CFA (BEAC)",
            "symbole": "FCFA",
            "est_active": True,
            "taux_vers_xaf": 1,
            "source_taux": "Monnaie de référence de la banque (XAF)",
            "date_maj_taux": None,
        },
    )


def supprimer_devise_reference(apps, schema_editor):
    Devise = apps.get_model("devises", "Devise")
    Devise.objects.filter(code="XAF").delete()


class Migration(migrations.Migration):
    dependencies = [("devises", "0001_initial")]
    operations = [migrations.RunPython(creer_devise_reference, supprimer_devise_reference)]
