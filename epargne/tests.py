"""Tests des objectifs d'épargne, arrondis et budgets."""

import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from comptes.models import Utilisateur
from transactions.models import CompteBancaire, RegleFrais, Transaction

from .models import Budget, ConfigArrondi, ContributionEpargne, ObjectifEpargne


class EpargneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        RegleFrais.objects.all().delete()
        cls.utilisateur = Utilisateur.objects.create_user(
            username="client_epargne",
            password="MotDePasse!2026",
            telephone="+23562000001",
            first_name="Nadia",
            last_name="Mahamat",
        )
        cls.courant = CompteBancaire.objects.create(
            proprietaire=cls.utilisateur,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            solde=Decimal("10000.00"),
        )
        cls.epargne = CompteBancaire.objects.create(
            proprietaire=cls.utilisateur,
            type_compte=CompteBancaire.TypeCompte.EPARGNE,
            solde=Decimal("0.00"),
        )

    def setUp(self):
        self.client.force_login(self.utilisateur)

    def _creer_objectif(self, montant="5000"):
        return ObjectifEpargne.objects.create(
            proprietaire=self.utilisateur,
            nom="Fonds d'urgence",
            montant_cible=Decimal(montant),
            compte_epargne=self.epargne,
            couleur="#1656d1",
        )

    def test_page_epargne(self):
        reponse = self.client.get(reverse("epargne:tableau_epargne"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Mes objectifs d'épargne")

    def test_creation_objectif_via_formulaire(self):
        reponse = self.client.post(
            reverse("epargne:creer_objectif"),
            {
                "nom": "Voyage",
                "description": "Voyage familial",
                "montant_cible": "250000",
                "compte_epargne": self.epargne.pk,
                "date_cible": "",
                "couleur": "#00a651",
            },
        )
        objectif = ObjectifEpargne.objects.get(nom="Voyage")
        self.assertRedirects(
            reponse, reverse("epargne:detail_objectif", args=[objectif.pk]), fetch_redirect_response=False
        )
        self.assertEqual(objectif.proprietaire, self.utilisateur)
        self.assertEqual(objectif.montant_actuel, Decimal("0.00"))

    def test_contribution_debite_credite_et_trace(self):
        objectif = self._creer_objectif("5000")
        cle = uuid.uuid4()
        reponse = self.client.post(
            reverse("epargne:contribuer_objectif", args=[objectif.pk]),
            {"compte_source": self.courant.pk, "montant": "3000", "cle_idempotence": str(cle)},
        )
        self.assertRedirects(
            reponse, reverse("epargne:detail_objectif", args=[objectif.pk]), fetch_redirect_response=False
        )
        self.courant.refresh_from_db()
        self.epargne.refresh_from_db()
        objectif.refresh_from_db()
        self.assertEqual(self.courant.solde, Decimal("7000.00"))
        self.assertEqual(self.epargne.solde, Decimal("3000.00"))
        self.assertEqual(objectif.montant_actuel, Decimal("3000.00"))
        self.assertEqual(objectif.statut, ObjectifEpargne.Statut.ACTIF)
        operation = Transaction.objects.get(cle_idempotence=str(cle))
        self.assertEqual(operation.categorie, Transaction.Categorie.EPARGNE)
        self.assertEqual(operation.type_transaction, Transaction.Type.VIREMENT)
        self.assertTrue(
            ContributionEpargne.objects.filter(objectif=objectif, transaction=operation).exists()
        )

    def test_contribution_idempotente(self):
        objectif = self._creer_objectif("5000")
        cle = str(uuid.uuid4())
        premiere = objectif.contribuer(self.courant, Decimal("1000"), cle_idempotence=cle)
        self.courant.refresh_from_db()
        solde_apres_premiere = self.courant.solde
        seconde = objectif.contribuer(self.courant, Decimal("1000"), cle_idempotence=cle)
        self.courant.refresh_from_db()
        self.assertEqual(premiere.pk, seconde.pk)
        self.assertEqual(self.courant.solde, solde_apres_premiere)
        self.assertEqual(ContributionEpargne.objects.filter(objectif=objectif).count(), 1)

    def test_contribution_depasse_restant_refusee(self):
        objectif = self._creer_objectif("5000")
        reponse = self.client.post(
            reverse("epargne:contribuer_objectif", args=[objectif.pk]),
            {
                "compte_source": self.courant.pk,
                "montant": "6000",
                "cle_idempotence": str(uuid.uuid4()),
            },
        )
        self.assertRedirects(
            reponse, reverse("epargne:detail_objectif", args=[objectif.pk]), fetch_redirect_response=False
        )
        self.courant.refresh_from_db()
        objectif.refresh_from_db()
        self.assertEqual(self.courant.solde, Decimal("10000.00"))
        self.assertEqual(objectif.montant_actuel, Decimal("0.00"))

    def test_objectif_atteint(self):
        objectif = self._creer_objectif("1200")
        objectif.contribuer(self.courant, Decimal("1200"), cle_idempotence=str(uuid.uuid4()))
        objectif.refresh_from_db()
        self.assertEqual(objectif.statut, ObjectifEpargne.Statut.ATTEINT)
        self.assertEqual(objectif.progression, 100)

    def test_annulation_conserve_les_fonds(self):
        objectif = self._creer_objectif("5000")
        objectif.contribuer(self.courant, Decimal("1000"), cle_idempotence=str(uuid.uuid4()))
        reponse = self.client.post(reverse("epargne:annuler_objectif", args=[objectif.pk]))
        self.assertRedirects(reponse, reverse("epargne:tableau_epargne"), fetch_redirect_response=False)
        objectif.refresh_from_db()
        self.epargne.refresh_from_db()
        self.assertEqual(objectif.statut, ObjectifEpargne.Statut.ANNULE)
        self.assertEqual(self.epargne.solde, Decimal("1000.00"))

    def test_arrondi_sur_retrait(self):
        objectif = self._creer_objectif("5000")
        ConfigArrondi.objects.create(
            proprietaire=self.utilisateur,
            actif=True,
            compte_source=self.courant,
            compte_epargne=self.epargne,
            multiple=ConfigArrondi.Multiple.MILLE,
            objectif=objectif,
        )
        reponse = self.client.post(
            reverse("transactions:retirer"),
            {
                "compte": self.courant.pk,
                "montant": "4300",
                "categorie": Transaction.Categorie.ALIMENTATION,
                "cle_idempotence": str(uuid.uuid4()),
            },
        )
        self.assertRedirects(
            reponse, reverse("transactions:detail_compte", args=[self.courant.pk]), fetch_redirect_response=False
        )
        self.courant.refresh_from_db()
        self.epargne.refresh_from_db()
        objectif.refresh_from_db()
        self.assertEqual(self.courant.solde, Decimal("5000.00"))
        self.assertEqual(self.epargne.solde, Decimal("700.00"))
        self.assertEqual(objectif.montant_actuel, Decimal("700.00"))
        self.assertTrue(
            ContributionEpargne.objects.filter(
                objectif=objectif, type_contribution=ContributionEpargne.Type.ARRONDI
            ).exists()
        )

    def test_configuration_arrondi(self):
        objectif = self._creer_objectif("5000")
        reponse = self.client.post(
            reverse("epargne:configurer_arrondi"),
            {
                "actif": "on",
                "compte_source": self.courant.pk,
                "compte_epargne": self.epargne.pk,
                "multiple": "1000",
                "objectif": objectif.pk,
            },
        )
        self.assertRedirects(
            reponse, reverse("epargne:configurer_arrondi"), fetch_redirect_response=False
        )
        config = ConfigArrondi.objects.get(proprietaire=self.utilisateur)
        self.assertTrue(config.actif)
        self.assertEqual(config.multiple, 1000)

    def test_budget_suit_les_depenses(self):
        reponse = self.client.post(
            reverse("epargne:budgets"),
            {
                "categorie": Transaction.Categorie.ALIMENTATION,
                "plafond_mensuel": "1000",
                "seuil_alerte": "80",
                "actif": "on",
            },
        )
        self.assertRedirects(reponse, reverse("epargne:budgets"), fetch_redirect_response=False)
        budget = Budget.objects.get(proprietaire=self.utilisateur)
        self.courant.retirer(
            Decimal("800"),
            description="Courses",
            categorie=Transaction.Categorie.ALIMENTATION,
            cle_idempotence=str(uuid.uuid4()),
        )
        self.assertEqual(budget.montant_depense(), Decimal("800.00"))
        self.assertEqual(budget.pourcentage_consomme, 80)
        self.assertFalse(budget.depassement)

    def test_un_seul_budget_par_categorie(self):
        Budget.objects.create(
            proprietaire=self.utilisateur,
            categorie=Transaction.Categorie.TRANSPORT,
            plafond_mensuel=Decimal("5000"),
        )
        reponse = self.client.post(
            reverse("epargne:budgets"),
            {
                "categorie": Transaction.Categorie.TRANSPORT,
                "plafond_mensuel": "8000",
                "seuil_alerte": "80",
                "actif": "on",
            },
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Un budget existe déjà")
        self.assertEqual(Budget.objects.filter(proprietaire=self.utilisateur).count(), 1)

    def test_objectif_d_un_autre_client_inaccessible(self):
        autre = Utilisateur.objects.create_user(
            username="autre_epargne",
            password="MotDePasse!2026",
            telephone="+23562000002",
        )
        compte_autre = CompteBancaire.objects.create(
            proprietaire=autre,
            type_compte=CompteBancaire.TypeCompte.EPARGNE,
            solde=0,
        )
        objectif = ObjectifEpargne.objects.create(
            proprietaire=autre,
            nom="Objectif privé",
            montant_cible=Decimal("1000"),
            compte_epargne=compte_autre,
            couleur="#1656d1",
        )
        reponse = self.client.get(reverse("epargne:detail_objectif", args=[objectif.pk]))
        self.assertEqual(reponse.status_code, 404)
