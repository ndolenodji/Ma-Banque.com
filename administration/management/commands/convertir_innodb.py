"""
Convertit toutes les tables de la base courante en InnoDB puis vérifie
l'intégrité minimale (moteur, nombre de lignes, clés étrangères orphelines).

Commande idempotente, à exécuter après une sauvegarde vérifiée :

    python manage.py convertir_innodb
    python manage.py convertir_innodb --verifier-seulement
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Convertit les tables MySQL MyISAM en InnoDB (transactionnel) et vérifie le résultat."

    def add_arguments(self, parser):
        parser.add_argument(
            "--verifier-seulement",
            action="store_true",
            help="Ne réalise aucune conversion, affiche uniquement l'état des tables.",
        )

    def handle(self, *args, **options):
        verifier_seulement = options["verifier_seulement"]
        if connection.vendor != "mysql":
            self.stdout.write(self.style.WARNING("Base non MySQL : aucune conversion nécessaire."))
            return

        with connection.cursor() as curseur:
            curseur.execute(
                "SELECT table_name, engine, table_rows FROM information_schema.tables "
                "WHERE table_schema = DATABASE() ORDER BY table_name"
            )
            tables = curseur.fetchall()
            a_convertir = [(nom, moteur) for nom, moteur, _ in tables if moteur != "InnoDB"]

            self.stdout.write(f"Tables analysées : {len(tables)} — non InnoDB : {len(a_convertir)}")
            if verifier_seulement:
                for nom, moteur in a_convertir:
                    self.stdout.write(f"  - {nom} : {moteur}")
                return

            # Nombre de lignes avant conversion, pour contrôle.
            compteurs_avant = {}
            for nom, _, _ in tables:
                curseur.execute(f"SELECT COUNT(*) FROM `{nom}`")
                compteurs_avant[nom] = curseur.fetchone()[0]

            for nom, moteur in a_convertir:
                self.stdout.write(f"Conversion de {nom} ({moteur}) vers InnoDB…")
                curseur.execute(f"ALTER TABLE `{nom}` ENGINE=InnoDB")

            # Contrôle après conversion.
            curseur.execute(
                "SELECT table_name, engine FROM information_schema.tables "
                "WHERE table_schema = DATABASE() ORDER BY table_name"
            )
            etats = curseur.fetchall()
            non_innodb = [nom for nom, moteur in etats if moteur != "InnoDB"]
            if non_innodb:
                self.stderr.write(self.style.ERROR(f"Tables toujours non InnoDB : {non_innodb}"))
            else:
                self.stdout.write(self.style.SUCCESS("Toutes les tables sont désormais en InnoDB."))

            for nom, _ in etats:
                curseur.execute(f"SELECT COUNT(*) FROM `{nom}`")
                apres = curseur.fetchone()[0]
                if apres != compteurs_avant.get(nom):
                    self.stderr.write(
                        self.style.ERROR(
                            f"Écart de lignes sur {nom} : {compteurs_avant.get(nom)} → {apres}"
                        )
                    )

            curseur.execute("SELECT @@sql_mode, @@default_storage_engine, @@transaction_isolation")
            self.stdout.write("Session SQL : " + " | ".join(str(valeur) for valeur in curseur.fetchone()))

            # Contrôle des références orphelines principales.
            controles = [
                ("transactions_comptebancaire", "proprietaire_id", "comptes_utilisateur", "id"),
                ("transactions_transaction", "compte_source_id", "transactions_comptebancaire", "id"),
                ("transactions_transaction", "compte_destination_id", "transactions_comptebancaire", "id"),
                ("paiement_mobile_paiementmobile", "client_id", "comptes_utilisateur", "id"),
                ("paiement_mobile_paiementmobile", "compte_bancaire_id", "transactions_comptebancaire", "id"),
                ("comptes_beneficiaire", "proprietaire_id", "comptes_utilisateur", "id"),
            ]
            for table, colonne, reference, cle in controles:
                curseur.execute(
                    f"SELECT COUNT(*) FROM `{table}` t LEFT JOIN `{reference}` r ON r.`{cle}` = t.`{colonne}` "
                    f"WHERE t.`{colonne}` IS NOT NULL AND r.`{cle}` IS NULL"
                )
                orphelins = curseur.fetchone()[0]
                if orphelins:
                    self.stderr.write(
                        self.style.WARNING(f"Références orphelines : {table}.{colonne} → {reference} : {orphelins}")
                    )
                else:
                    self.stdout.write(f"Intégrité OK : {table}.{colonne}")
