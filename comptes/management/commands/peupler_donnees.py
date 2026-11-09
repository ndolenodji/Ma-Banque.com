"""
Commande de peuplement des données de test.

Usage : python manage.py peupler_donnees

Crée, comme demandé au cahier des charges (section 7) :
- 1 administrateur + 1 agent bancaire
- au moins 5 clients
- au moins 10 comptes bancaires
- au moins 30 transactions
- au moins 10 paiements mobiles simulés
"""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.utils import timezone

from comptes.models import Beneficiaire, Utilisateur
from paiement_mobile.models import PaiementMobile
from transactions.models import CompteBancaire, Transaction


class Command(BaseCommand):
    help = "Peuple la base de données avec un jeu de données de démonstration."

    @db_transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Suppression des anciennes données de démonstration...")
        Utilisateur.objects.filter(is_superuser=False).delete()

        self.stdout.write("Création des utilisateurs internes...")
        admin = Utilisateur.objects.create_superuser(
            username="admin", email="admin@mabanque.td", password="AdminBanque2026!",
            first_name="Awada", last_name="Directrice", telephone="66000001", role=Utilisateur.Role.ADMIN,
        )
        agent = Utilisateur.objects.create_user(
            username="agent1", email="agent1@mabanque.td", password="AgentBanque2026!",
            first_name="Moussa", last_name="Agent", telephone="66000002", role=Utilisateur.Role.AGENT,
            identite_verifiee=True,
        )

        self.stdout.write("Création de 5 clients...")
        donnees_clients = [
            ("Boukar", "Micheal", "Ministre", "66111111"),
            ("mmathurin", "Ndolenodji", "Mathurin", "66222222"),
            ("aissa", "Aïssa", "Brahim", "66333333"),
            ("koumra", "Koumra", "Delphine", "66444444"),
            ("hassan", "Hassan", "Idriss", "66555555"),
        ]
        clients = []
        for identifiant, prenom, nom, telephone in donnees_clients:
            client = Utilisateur.objects.create_user(
                username=identifiant, email=f"{identifiant}@example.com", password="ClientBanque2026!",
                first_name=prenom, last_name=nom, telephone=telephone,
                role=Utilisateur.Role.CLIENT, identite_verifiee=True,
                numero_piece_identite=f"CNI-{random.randint(100000, 999999)}",
            )
            clients.append(client)

        self.stdout.write("Création de 10 comptes bancaires (2 par client)...")
        comptes = []
        for client in clients:
            compte_courant = CompteBancaire.objects.create(
                proprietaire=client, type_compte=CompteBancaire.TypeCompte.COURANT,
                solde=Decimal(random.randint(50000, 2000000)),
            )
            compte_epargne = CompteBancaire.objects.create(
                proprietaire=client, type_compte=CompteBancaire.TypeCompte.EPARGNE,
                solde=Decimal(random.randint(20000, 1500000)),
            )
            comptes.extend([compte_courant, compte_epargne])

        self.stdout.write("Création de bénéficiaires favoris...")
        for client in clients:
            autres_comptes = [c for c in comptes if c.proprietaire_id != client.id]
            for compte_beneficiaire in random.sample(autres_comptes, k=min(2, len(autres_comptes))):
                Beneficiaire.objects.get_or_create(
                    proprietaire=client,
                    numero_compte=compte_beneficiaire.numero_compte,
                    defaults={"nom_complet": str(compte_beneficiaire.proprietaire.get_full_name()), "surnom": ""},
                )

        self.stdout.write("Création de 30 transactions de démonstration...")
        descriptions_depot = ["Dépôt guichet", "Dépôt espèces", "Versement salaire"]
        descriptions_retrait = ["Retrait DAB", "Retrait guichet", "Frais divers"]
        for _ in range(14):
            compte = random.choice(comptes)
            compte.deposer(Decimal(random.randint(5000, 300000)), description=random.choice(descriptions_depot))
        for _ in range(10):
            compte = random.choice(comptes)
            try:
                compte.retirer(Decimal(random.randint(2000, 100000)), description=random.choice(descriptions_retrait))
            except Exception:
                pass
        for _ in range(6):
            compte_source, compte_destination = random.sample(comptes, 2)
            try:
                compte_source.virer_vers(
                    compte_destination, Decimal(random.randint(1000, 80000)), description="Virement de démonstration"
                )
            except Exception:
                pass

        self.stdout.write("Création de 10 paiements mobiles simulés...")
        operateurs = [c[0] for c in PaiementMobile.Operateur.choices]
        for i in range(10):
            client = random.choice(clients)
            compte = random.choice([c for c in comptes if c.proprietaire_id == client.id])
            type_operation = random.choice(
                [PaiementMobile.TypeOperation.RECHARGE, PaiementMobile.TypeOperation.RETRAIT_MOBILE]
            )
            PaiementMobile.objects.create(
                client=client, compte_bancaire=compte,
                reference_transaction=f"TXN-DEMO-{i:04d}",
                type_operation=type_operation,
                operateur=random.choice(operateurs),
                numero_telephone=client.telephone,
                montant=Decimal(random.randint(1000, 150000)),
                statut=random.choice([PaiementMobile.Statut.REUSSI, PaiementMobile.Statut.EN_ATTENTE]),
            )

        self.stdout.write(self.style.SUCCESS(
            "\nJeu de données créé avec succès !\n"
            "----------------------------------------\n"
            "Administrateur : admin / AdminBanque2026!\n"
            "Agent bancaire : agent1 / AgentBanque2026!\n"
            "Clients        : jdupont, mmathurin, aissa, koumra, hassan / ClientBanque2026!\n"
        ))
