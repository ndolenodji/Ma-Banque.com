"""Crée les fournisseurs de factures de démonstration.

Aucun fournisseur réel n'est connecté : ces entrées servent uniquement à
illustrer le parcours de paiement. Le code est idempotent et n'écrase aucune
donnée existante.
"""

from django.db import migrations

FOURNISSEURS = [
    ("DEMO-EAU", "Eau — fournisseur de démonstration", "EAU", "Facture d'eau simulée"),
    ("DEMO-ELEC", "Électricité — fournisseur de démonstration", "ELECTRICITE", "Facture d'électricité simulée"),
    ("DEMO-NET", "Internet — fournisseur de démonstration", "INTERNET", "Abonnement internet simulé"),
    ("DEMO-TEL", "Télécom — fournisseur de démonstration", "TELECOM", "Forfait télécom simulé"),
    ("DEMO-TV", "Télévision — fournisseur de démonstration", "TELEVISION", "Abonnement TV simulé"),
]


def creer_fournisseurs(apps, schema_editor):
    FournisseurFacture = apps.get_model("factures", "FournisseurFacture")
    for code, nom, categorie, description in FOURNISSEURS:
        FournisseurFacture.objects.get_or_create(
            code=code,
            defaults={
                "nom": nom,
                "categorie": categorie,
                "description": description,
                "est_simule": True,
                "actif": True,
            },
        )


def supprimer_fournisseurs(apps, schema_editor):
    """Laisser les fournisseurs en place : aucune donnée métier n'est supprimée."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("factures", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(creer_fournisseurs, supprimer_fournisseurs),
    ]
