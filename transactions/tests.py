"""Tests fonctionnels des opérations bancaires (Task #4).

Ils s'exécutent sur une base de test isolée et vérifient les règles
financières, l'idempotence, les frais, les virements programmés, les exports
de relevés et le contrôle d'accès aux reçus.
"""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from devises.models import Devise
from transactions import views as vues_transactions
from transactions.models import CompteBancaire, RegleFrais, Transaction, VirementProgramme

Utilisateur = get_user_model()


class OperationsBancairesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.devise = Devise.objects.get(code="XAF")
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="client_recette",
            email="client.recette@example.test",
            password="MotDePasseRecette2026!",
            telephone="66090001",
            first_name="Client",
            last_name="Recette",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.client_bancaire,
            devise=cls.devise,
            solde=Decimal("100000.00"),
        )
        cls.beneficiaire = Utilisateur.objects.create_user(
            username="beneficiaire_recette",
            email="beneficiaire.recette@example.test",
            password="MotDePasseRecette2026!",
            telephone="66090002",
            first_name="Bénéficiaire",
            last_name="Recette",
        )
        cls.compte_beneficiaire = CompteBancaire.objects.create(
            proprietaire=cls.beneficiaire,
            devise=cls.devise,
            solde=Decimal("5000.00"),
        )
        cls.tiers = Utilisateur.objects.create_user(
            username="tiers_recette",
            email="tiers.recette@example.test",
            password="MotDePasseRecette2026!",
            telephone="66090003",
        )

        RegleFrais.objects.create(
            type_operation=RegleFrais.Operation.RETRAIT,
            libelle="Retrait de recette",
            frais_fixe=Decimal("50.00"),
            taux_pourcentage=Decimal("1.000"),
            frais_minimum=Decimal("50.00"),
            frais_maximum=Decimal("500.00"),
        )
        RegleFrais.objects.create(
            type_operation=RegleFrais.Operation.VIREMENT,
            libelle="Virement de recette",
            frais_fixe=Decimal("100.00"),
            taux_pourcentage=Decimal("0.000"),
        )

    def setUp(self):
        self.client.force_login(self.client_bancaire)

    # ------------------------------------------------------------------
    # Consultation et contrôles d'accès
    # ------------------------------------------------------------------
    def test_pages_operations_principales(self):
        pages = [
            "transactions:liste_comptes",
            "transactions:releves",
            "transactions:virement",
            "transactions:virements_programmes",
            "transactions:deposer",
            "transactions:retirer",
            "transactions:historique",
        ]
        for nom in pages:
            with self.subTest(page=nom):
                self.assertEqual(self.client.get(reverse(nom)).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("transactions:detail_compte", args=[self.compte.pk])).status_code,
            200,
        )

    def test_ouverture_compte_exige_kyc(self):
        self.client_bancaire.identite_verifiee = False
        self.client_bancaire.save(update_fields=["identite_verifiee"])
        self.assertRedirects(
            self.client.get(reverse("transactions:ouvrir_compte")),
            reverse("kyc:mon_dossier"),
        )

        self.client_bancaire.identite_verifiee = True
        self.client_bancaire.save(update_fields=["identite_verifiee"])
        nombre_avant = CompteBancaire.objects.filter(proprietaire=self.client_bancaire).count()
        reponse = self.client.post(
            reverse("transactions:ouvrir_compte"),
            {"type_compte": CompteBancaire.TypeCompte.EPARGNE, "devise": self.devise.pk},
        )
        compte = CompteBancaire.objects.filter(proprietaire=self.client_bancaire).latest("id")
        self.assertRedirects(
            reponse, reverse("transactions:detail_compte", args=[compte.pk])
        )
        self.assertEqual(compte.type_compte, CompteBancaire.TypeCompte.EPARGNE)
        self.assertEqual(compte.solde, Decimal("0.00"))
        self.assertEqual(
            CompteBancaire.objects.filter(proprietaire=self.client_bancaire).count(),
            nombre_avant + 1,
        )

    # ------------------------------------------------------------------
    # Dépôt / retrait
    # ------------------------------------------------------------------
    def test_depot_et_idempotence(self):
        cle = str(uuid.uuid4())
        donnees = {
            "compte": self.compte.pk,
            "montant": "1000.00",
            "tiers_nom": "Déposant Recette",
            "tiers_telephone": "66000001",
            "cle_idempotence": cle,
        }
        reponse = self.client.post(reverse("transactions:deposer"), donnees)
        self.assertRedirects(
            reponse, reverse("transactions:detail_compte", args=[self.compte.pk])
        )
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("101000.00"))
        transaction = Transaction.objects.get(cle_idempotence=cle)
        self.assertEqual(transaction.tiers_nom, "Déposant Recette")
        self.assertEqual(transaction.statut, Transaction.Statut.REUSSIE)

        # Un renvoi du même formulaire ne crédite pas une seconde fois.
        self.client.post(reverse("transactions:deposer"), donnees)
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("101000.00"))
        self.assertEqual(Transaction.objects.filter(cle_idempotence=cle).count(), 1)

    def test_retrait_applique_les_frais_et_refuse_solde_insuffisant(self):
        reponse = self.client.post(
            reverse("transactions:retirer"),
            {"compte": self.compte.pk, "montant": "1000.00", "cle_idempotence": str(uuid.uuid4())},
        )
        self.assertRedirects(
            reponse, reverse("transactions:detail_compte", args=[self.compte.pk])
        )
        self.compte.refresh_from_db()
        # 50 fixes + 1 % de 1000 = 60 de frais.
        self.assertEqual(self.compte.solde, Decimal("98940.00"))
        transaction = Transaction.objects.order_by("-id").first()
        self.assertEqual(transaction.frais, Decimal("60.00"))
        self.assertEqual(transaction.montant, Decimal("1000.00"))

        reponse = self.client.post(
            reverse("transactions:retirer"),
            {"compte": self.compte.pk, "montant": "999999.00", "cle_idempotence": str(uuid.uuid4())},
            follow=True,
        )
        self.assertContains(reponse, "Votre solde est insuffisant.")
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("98940.00"))

    def test_depot_par_numero_de_compte_et_message_de_confirmation(self):
        reponse = self.client.post(
            reverse("transactions:deposer"),
            {
                "compte": self.compte.numero_compte,
                "montant": "1500.00",
                "cle_idempotence": str(uuid.uuid4()),
            },
            follow=True,
        )
        self.assertContains(
            reponse,
            "Votre virement ou votre transaction a été effectué(e) avec succès.",
        )
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("101500.00"))
        self.assertEqual(
            Transaction.objects.filter(
                type_transaction=Transaction.Type.DEPOT, montant=Decimal("1500.00")
            ).count(),
            1,
        )

    def test_retrait_par_numero_de_compte(self):
        reponse = self.client.post(
            reverse("transactions:retirer"),
            {
                "compte": self.compte.numero_compte,
                "montant": "1000.00",
                "cle_idempotence": str(uuid.uuid4()),
            },
            follow=True,
        )
        self.assertContains(
            reponse,
            "Votre virement ou votre transaction a été effectué(e) avec succès.",
        )
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("98940.00"))

    # ------------------------------------------------------------------
    # Virement immédiat et programmé
    # ------------------------------------------------------------------
    def test_virement_debite_credite_et_notifie(self):
        cle = str(uuid.uuid4())
        reponse = self.client.post(
            reverse("transactions:virement"),
            {
                "compte_source": self.compte.pk,
                "numero_compte_destination": self.compte_beneficiaire.numero_compte,
                "montant": "500.00",
                "description": "Virement de recette",
                "cle_idempotence": cle,
            },
        )
        self.assertRedirects(
            reponse, reverse("transactions:detail_compte", args=[self.compte.pk])
        )
        self.compte.refresh_from_db()
        self.compte_beneficiaire.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("99400.00"))  # 500 + 100 de frais
        self.assertEqual(self.compte_beneficiaire.solde, Decimal("5500.00"))
        transaction = Transaction.objects.get(cle_idempotence=cle)
        self.assertEqual(transaction.statut, Transaction.Statut.REUSSIE)
        self.assertTrue(
            self.beneficiaire.notifications.filter(titre="Virement reçu").exists()
        )

    def test_virement_par_numero_de_compte_source_et_message_de_confirmation(self):
        cle = str(uuid.uuid4())
        reponse = self.client.post(
            reverse("transactions:virement"),
            {
                "compte_source": self.compte.numero_compte,
                "numero_compte_destination": self.compte_beneficiaire.numero_compte,
                "montant": "500.00",
                "description": "Virement saisi au clavier",
                "cle_idempotence": cle,
            },
            follow=True,
        )
        self.assertContains(
            reponse,
            "Votre virement ou votre transaction a été effectué(e) avec succès.",
        )
        self.compte.refresh_from_db()
        self.compte_beneficiaire.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("99400.00"))
        self.assertEqual(self.compte_beneficiaire.solde, Decimal("5500.00"))

    def test_virement_refuse_si_le_solde_est_insuffisant(self):
        cle = str(uuid.uuid4())
        reponse = self.client.post(
            reverse("transactions:virement"),
            {
                "compte_source": self.compte.numero_compte,
                "numero_compte_destination": self.compte_beneficiaire.numero_compte,
                "montant": "200000.00",
                "cle_idempotence": cle,
            },
            follow=True,
        )
        self.assertContains(reponse, "Votre solde est insuffisant.")
        self.compte.refresh_from_db()
        self.compte_beneficiaire.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("100000.00"))
        self.assertEqual(self.compte_beneficiaire.solde, Decimal("5000.00"))
        self.assertFalse(Transaction.objects.filter(cle_idempotence=cle).exists())

    def test_virement_programme_cycle_complet(self):
        cle = str(uuid.uuid4())
        aujourd_hui = timezone.localdate()
        reponse = self.client.post(
            reverse("transactions:virements_programmes"),
            {
                "compte_source": self.compte.pk,
                "numero_destination": self.compte_beneficiaire.numero_compte,
                "montant": "200.00",
                "description": "Programmé de recette",
                "frequence": VirementProgramme.Frequence.HEBDOMADAIRE,
                "date_debut": aujourd_hui.isoformat(),
                "date_fin": "",
                "cle_idempotence": cle,
            },
        )
        self.assertRedirects(reponse, reverse("transactions:virements_programmes"))
        programme = VirementProgramme.objects.get(cle_idempotence=cle)
        self.assertEqual(programme.statut, VirementProgramme.Statut.ACTIF)

        self.client.post(
            reverse("transactions:basculer_virement_programme", args=[programme.pk])
        )
        programme.refresh_from_db()
        self.assertEqual(programme.statut, VirementProgramme.Statut.PAUSE)
        self.client.post(
            reverse("transactions:basculer_virement_programme", args=[programme.pk])
        )
        programme.refresh_from_db()
        self.assertEqual(programme.statut, VirementProgramme.Statut.ACTIF)

        solde_avant = self.compte.solde
        self.client.post(
            reverse("transactions:executer_virement_maintenant", args=[programme.pk])
        )
        programme.refresh_from_db()
        self.compte.refresh_from_db()
        self.compte_beneficiaire.refresh_from_db()
        self.assertEqual(programme.nombre_executions, 1)
        self.assertEqual(programme.prochaine_execution, aujourd_hui + timezone.timedelta(days=7))
        self.assertEqual(self.compte.solde, solde_avant - Decimal("300.00"))
        self.assertEqual(self.compte_beneficiaire.solde, Decimal("5200.00"))

        # Une seconde exécution le même jour est refusée.
        self.client.post(
            reverse("transactions:executer_virement_maintenant", args=[programme.pk])
        )
        programme.refresh_from_db()
        self.compte.refresh_from_db()
        self.assertEqual(programme.nombre_executions, 1)
        self.assertEqual(self.compte.solde, solde_avant - Decimal("300.00"))

        self.client.post(
            reverse("transactions:annuler_virement_programme", args=[programme.pk])
        )
        programme.refresh_from_db()
        self.assertEqual(programme.statut, VirementProgramme.Statut.ANNULE)

    def test_virement_programme_autre_client_interdit(self):
        programme = VirementProgramme.objects.create(
            proprietaire=self.beneficiaire,
            compte_source=self.compte_beneficiaire,
            compte_destination=self.compte,
            numero_destination=self.compte.numero_compte,
            montant=Decimal("100.00"),
            frequence=VirementProgramme.Frequence.MENSUEL,
            date_debut=timezone.localdate(),
            prochaine_execution=timezone.localdate(),
        )
        reponse = self.client.post(
            reverse("transactions:annuler_virement_programme", args=[programme.pk])
        )
        self.assertEqual(reponse.status_code, 404)
        programme.refresh_from_db()
        self.assertEqual(programme.statut, VirementProgramme.Statut.ACTIF)

    # ------------------------------------------------------------------
    # Relevés, exports et reçus
    # ------------------------------------------------------------------
    def test_releve_soldes_et_exports(self):
        self.client.post(
            reverse("transactions:deposer"),
            {"compte": self.compte.pk, "montant": "1000.00", "cle_idempotence": str(uuid.uuid4())},
        )
        self.client.post(
            reverse("transactions:retirer"),
            {"compte": self.compte.pk, "montant": "500.00", "cle_idempotence": str(uuid.uuid4())},
        )
        self.compte.refresh_from_db()
        releve = vues_transactions._construire_releve(
            self.compte, timezone.localdate(), timezone.localdate()
        )
        self.assertEqual(releve["solde_cloture"], self.compte.solde)
        self.assertEqual(releve["nombre"], 2)
        self.assertEqual(
            releve["solde_ouverture"] + releve["total_credits"] - releve["total_debits"],
            releve["solde_cloture"],
        )
        self.assertEqual(releve["lignes"][-1]["solde"], releve["solde_cloture"])

        url_pdf = reverse("transactions:exporter_releve", kwargs={"format_export": "pdf"})
        reponse = self.client.get(
            url_pdf,
            {"compte": self.compte.pk, "date_debut": timezone.localdate().isoformat(), "date_fin": timezone.localdate().isoformat()},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "application/pdf")
        self.assertTrue(reponse.content.startswith(b"%PDF"))

        url_csv = reverse("transactions:exporter_releve", kwargs={"format_export": "csv"})
        reponse = self.client.get(url_csv, {"compte": self.compte.pk})
        self.assertEqual(reponse.status_code, 200)
        contenu = reponse.content.decode("utf-8-sig")
        self.assertIn("Ma Banque", contenu)
        self.assertIn("Solde d'ouverture", contenu)

        url_xlsx = reverse("transactions:exporter_releve", kwargs={"format_export": "xlsx"})
        reponse = self.client.get(url_xlsx, {"compte": self.compte.pk})
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.content.startswith(b"PK"))

    def test_releve_periode_invalide(self):
        url = reverse("transactions:exporter_releve", kwargs={"format_export": "pdf"})
        reponse = self.client.get(
            url,
            {"compte": self.compte.pk, "date_debut": "2026-12-31", "date_fin": "2026-01-01"},
            follow=True,
        )
        self.assertContains(reponse, "La date de fin ne peut pas précéder")

    def test_recu_acces_et_pdf(self):
        self.client.post(
            reverse("transactions:deposer"),
            {"compte": self.compte.pk, "montant": "250.00", "cle_idempotence": str(uuid.uuid4())},
        )
        transaction = Transaction.objects.latest("id")
        reponse = self.client.get(
            reverse("transactions:telecharger_recu", args=[transaction.pk])
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.content.startswith(b"%PDF"))

        autre_client = Client()
        autre_client.force_login(self.tiers)
        reponse = autre_client.get(
            reverse("transactions:telecharger_recu", args=[transaction.pk])
        )
        self.assertEqual(reponse.status_code, 404)
