"""Tests du paiement de factures simulé."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comptes.models import Utilisateur
from transactions.models import CompteBancaire, RegleFrais, Transaction

from .models import CompteFacture, Facture, FournisseurFacture


class FacturesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.utilisateur = Utilisateur.objects.create_user(
            username="client_factures",
            password="MotDePasse!2026",
            telephone="+23563000001",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.utilisateur,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            solde=Decimal("10000.00"),
        )
        cls.fournisseur = FournisseurFacture.objects.create(
            nom="Eau — fournisseur de démonstration",
            code="TEST-EAU",
            categorie=FournisseurFacture.Categorie.EAU,
            est_simule=True,
        )

    def setUp(self):
        self.client.force_login(self.utilisateur)

    def _creer_facture(self, montant="3000", **extra):
        donnees = {
            "proprietaire": self.utilisateur,
            "fournisseur": self.fournisseur,
            "reference_externe": "REF-2026-001",
            "libelle": "Facture de test",
            "montant": Decimal(montant),
            "date_emission": timezone.localdate(),
            "date_echeance": timezone.localdate() + timedelta(days=30),
        }
        donnees.update(extra)
        return Facture.objects.create(**donnees)

    def test_page_factures_signale_la_simulation(self):
        reponse = self.client.get(reverse("factures:tableau_factures"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Fournisseurs simulés")
        self.assertContains(reponse, "Aucune facture externe réelle")

    def test_total_paye_mois_courant_uniquement(self):
        maintenant = timezone.now()
        self._creer_facture(
            "3000",
            statut=Facture.Statut.PAYEE,
            date_paiement=maintenant,
        )
        self._creer_facture(
            "7000",
            statut=Facture.Statut.PAYEE,
            date_paiement=maintenant - timedelta(days=40),
        )
        reponse = self.client.get(reverse("factures:tableau_factures"))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.context["total_paye_mois"], Decimal("3000.00"))

    def test_creation_facture_et_reference(self):
        reponse = self.client.post(
            reverse("factures:ajouter_facture"),
            {
                "fournisseur": self.fournisseur.pk,
                "reference_externe": "EXT-42",
                "libelle": "Facture d'eau",
                "montant": "4500",
                "date_emission": timezone.localdate().isoformat(),
                "date_echeance": (timezone.localdate() + timedelta(days=15)).isoformat(),
                "reference_client": "CLIENT-001",
            },
        )
        facture = Facture.objects.get(proprietaire=self.utilisateur)
        self.assertRedirects(
            reponse, reverse("factures:detail_facture", args=[facture.pk]), fetch_redirect_response=False
        )
        self.assertEqual(facture.statut, Facture.Statut.EN_ATTENTE)
        self.assertEqual(facture.montant, Decimal("4500.00"))
        self.assertTrue(
            CompteFacture.objects.filter(
                proprietaire=self.utilisateur,
                fournisseur=self.fournisseur,
                reference_client="CLIENT-001",
            ).exists()
        )

    def test_paiement_debite_et_marque_payee(self):
        RegleFrais.objects.create(
            type_operation=RegleFrais.Operation.PAIEMENT_FACTURE,
            libelle="Frais facture de test",
            frais_fixe=Decimal("100"),
        )
        facture = self._creer_facture("3000")
        reponse = self.client.post(
            reverse("factures:detail_facture", args=[facture.pk]),
            {"compte": self.compte.pk, "cle_idempotence": str(uuid.uuid4())},
        )
        self.assertRedirects(
            reponse, reverse("factures:detail_facture", args=[facture.pk]), fetch_redirect_response=False
        )
        facture.refresh_from_db()
        self.compte.refresh_from_db()
        self.assertEqual(facture.statut, Facture.Statut.PAYEE)
        self.assertIsNotNone(facture.transaction)
        self.assertEqual(facture.transaction.frais, Decimal("100.00"))
        self.assertEqual(self.compte.solde, Decimal("6900.00"))
        self.assertEqual(facture.transaction.type_transaction, Transaction.Type.PAIEMENT_FACTURE)
        self.assertEqual(facture.transaction.categorie, Transaction.Categorie.FACTURES)

    def test_paiement_solde_insuffisant_sans_debit(self):
        facture = self._creer_facture("20000")
        reponse = self.client.post(
            reverse("factures:detail_facture", args=[facture.pk]),
            {"compte": self.compte.pk, "cle_idempotence": str(uuid.uuid4())},
        )
        self.assertRedirects(
            reponse, reverse("factures:detail_facture", args=[facture.pk]), fetch_redirect_response=False
        )
        facture.refresh_from_db()
        self.compte.refresh_from_db()
        self.assertEqual(facture.statut, Facture.Statut.EN_ATTENTE)
        self.assertIsNone(facture.transaction)
        self.assertEqual(self.compte.solde, Decimal("10000.00"))

    def test_double_soumission_ne_debite_qu_une_fois(self):
        facture = self._creer_facture("2500")
        cle = str(uuid.uuid4())
        self.client.post(
            reverse("factures:detail_facture", args=[facture.pk]),
            {"compte": self.compte.pk, "cle_idempotence": cle},
        )
        self.compte.refresh_from_db()
        solde_apres = self.compte.solde
        self.client.post(
            reverse("factures:detail_facture", args=[facture.pk]),
            {"compte": self.compte.pk, "cle_idempotence": cle},
        )
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, solde_apres)
        self.assertEqual(
            Transaction.objects.filter(type_transaction=Transaction.Type.PAIEMENT_FACTURE).count(), 1
        )

    def test_annulation_facture_sans_debit(self):
        facture = self._creer_facture("3000")
        reponse = self.client.post(reverse("factures:annuler_facture", args=[facture.pk]))
        self.assertRedirects(reponse, reverse("factures:tableau_factures"), fetch_redirect_response=False)
        facture.refresh_from_db()
        self.assertEqual(facture.statut, Facture.Statut.ANNULEE)
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("10000.00"))

    def test_facture_payee_non_payable(self):
        facture = self._creer_facture("1000")
        facture.payer(self.compte, cle_idempotence=str(uuid.uuid4()))
        reponse = self.client.post(
            reverse("factures:detail_facture", args=[facture.pk]),
            {"compte": self.compte.pk, "cle_idempotence": str(uuid.uuid4())},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Facture payée")
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("9000.00"))

    def test_facture_d_un_autre_client_inaccessible(self):
        autre = Utilisateur.objects.create_user(
            username="autre_factures",
            password="MotDePasse!2026",
            telephone="+23563000002",
        )
        facture = Facture.objects.create(
            proprietaire=autre,
            fournisseur=self.fournisseur,
            montant=Decimal("1000"),
            date_echeance=timezone.localdate() + timedelta(days=10),
        )
        reponse = self.client.get(reverse("factures:detail_facture", args=[facture.pk]))
        self.assertEqual(reponse.status_code, 404)
