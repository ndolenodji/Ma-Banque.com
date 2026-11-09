from django.db import migrations
from django.utils import timezone


def initialiser_dossiers(apps, schema_editor):
    Utilisateur = apps.get_model("comptes", "Utilisateur")
    DossierKYC = apps.get_model("kyc", "DossierKYC")
    for utilisateur in Utilisateur.objects.all().iterator():
        est_personnel = utilisateur.is_superuser or utilisateur.role in ("ADMIN", "AGENT")
        if utilisateur.identite_verifiee or est_personnel:
            statut = "APPROUVE"
        else:
            statut = "NON_SOUMIS"
        DossierKYC.objects.get_or_create(
            utilisateur=utilisateur,
            defaults={
                "statut": statut,
                "type_piece": "CNI" if utilisateur.numero_piece_identite else "",
                "numero_piece": utilisateur.numero_piece_identite or "",
                "adresse": utilisateur.adresse or "",
                "date_soumission": timezone.now() if statut == "EN_ATTENTE" else None,
                "date_traitement": timezone.now() if statut == "APPROUVE" else None,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("kyc", "0001_initial"),
        ("comptes", "0002_codesecours_notification_sessionconnexion_smssimule_and_more"),
    ]
    operations = [migrations.RunPython(initialiser_dossiers, migrations.RunPython.noop)]
