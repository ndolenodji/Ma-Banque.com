"""Tests des devises, taux administrés et conversions."""

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from comptes.models import Utilisateur
from transactions.models import CompteBancaire, ConversionDevise, RegleFrais
from transactions.services import convertir_devises

from .models import Devise


class DevisesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        RegleFrais.objects.all().delete()
        cls.utilisateur = Utilisateur.objects.create_user(
            username="client_devises",
            password="MotDePasse!2026",
            telephone="+23564000001",
        )
        cls.xaf = Devise.objects.get(code="XAF")
        cls.eur = Devise.objects.create(
            code="EUR",
            nom="Euro",
            symbole="€",
            taux_vers_xaf=Decimal("655.957000"),
            source_taux="Taux de test saisi par l'administrateur",
        )
        cls.compte_xaf = CompteBancaire.objects.create(
            proprietaire=cls.utilisateur,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            devise=cls.xaf,
            solde=Decimal("50000.00"),
        )
        cls.compte_eur = CompteBancaire.objects.create(
            proprietaire=cls.utilisateur,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            devise=cls.eur,
            solde=Decimal("0.00"),
        )

    def setUp(self):
        self.client.force_login(self.utilisateur)

    def test_page_taux_et_formulaire(self):
        reponse = self.client.get(reverse("devises:taux"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Taux administrés")
        self.assertContains(reponse, "1 EUR = 655,957000 XAF")
        self.assertContains(reponse, "Convertir un montant")
        self.assertContains(reponse, "Taux de test saisi")

    def test_conversion_via_formulaire(self):
        cle = str(uuid.uuid4())
        reponse = self.client.post(
            reverse("devises:taux"),
            {
                "compte_source": self.compte_xaf.pk,
                "compte_destination": self.compte_eur.pk,
                "montant": "10000",
                "cle_idempotence": cle,
            },
        )
        self.assertRedirects(
            reponse, reverse("devises:historique_conversions"), fetch_redirect_response=False
        )
        self.compte_xaf.refresh_from_db()
        self.compte_eur.refresh_from_db()
        self.assertEqual(self.compte_xaf.solde, Decimal("40000.00"))
        self.assertEqual(self.compte_eur.solde, Decimal("15.24"))
        conversion = ConversionDevise.objects.get(utilisateur=self.utilisateur)
        self.assertEqual(conversion.montant_source, Decimal("10000.00"))
        self.assertEqual(conversion.montant_destination, Decimal("15.24"))
        self.assertEqual(conversion.reference[:4], "CNV-")
        self.assertEqual(conversion.transaction_source.cle_idempotence, cle)

    def test_apercu_json_sans_mutation(self):
        reponse = self.client.get(
            reverse("devises:apercu_conversion"),
            {
                "compte_source": self.compte_xaf.pk,
                "compte_destination": self.compte_eur.pk,
                "montant": "10000",
            },
        )
        self.assertEqual(reponse.status_code, 200)
        donnees = reponse.json()
        self.assertEqual(donnees["montant_destination"], "15.24")
        self.assertEqual(donnees["code_source"], "XAF")
        self.assertEqual(donnees["code_destination"], "EUR")
        self.assertEqual(donnees["total_debit"], "10000.00")
        self.assertIn("Aucun taux de marché", donnees["avertissement"])
        self.compte_xaf.refresh_from_db()
        self.compte_eur.refresh_from_db()
        self.assertEqual(self.compte_xaf.solde, Decimal("50000.00"))
        self.assertEqual(self.compte_eur.solde, Decimal("0.00"))
        self.assertFalse(ConversionDevise.objects.exists())

    def test_apercu_compte_d_un_autre_client_refuse(self):
        autre = Utilisateur.objects.create_user(
            username="autre_devises",
            password="MotDePasse!2026",
            telephone="+23564000002",
        )
        compte_autre = CompteBancaire.objects.create(
            proprietaire=autre,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            devise=self.eur,
            solde=0,
        )
        reponse = self.client.get(
            reverse("devises:apercu_conversion"),
            {
                "compte_source": self.compte_xaf.pk,
                "compte_destination": compte_autre.pk,
                "montant": "1000",
            },
        )
        self.assertEqual(reponse.status_code, 400)

    def test_taux_manquant_bloque_la_conversion(self):
        usd = Devise.objects.create(
            code="USD",
            nom="Dollar américain",
            symbole="$",
            taux_vers_xaf=None,
            source_taux="",
        )
        compte_usd = CompteBancaire.objects.create(
            proprietaire=self.utilisateur,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            devise=usd,
            solde=0,
        )
        with self.assertRaises(ValidationError):
            convertir_devises(
                self.compte_xaf,
                compte_usd,
                Decimal("1000"),
                self.utilisateur,
                cle_idempotence=str(uuid.uuid4()),
            )
        self.compte_xaf.refresh_from_db()
        self.assertEqual(self.compte_xaf.solde, Decimal("50000.00"))

    def test_meme_devise_refusee(self):
        reponse = self.client.post(
            reverse("devises:taux"),
            {
                "compte_source": self.compte_xaf.pk,
                "compte_destination": self.compte_xaf.pk,
                "montant": "1000",
                "cle_idempotence": str(uuid.uuid4()),
            },
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "doivent être différents")

    def test_conversion_idempotente(self):
        cle = str(uuid.uuid4())
        premiere = convertir_devises(
            self.compte_xaf, self.compte_eur, Decimal("5000"), self.utilisateur, cle_idempotence=cle
        )
        self.compte_xaf.refresh_from_db()
        solde_apres = self.compte_xaf.solde
        seconde = convertir_devises(
            self.compte_xaf, self.compte_eur, Decimal("5000"), self.utilisateur, cle_idempotence=cle
        )
        self.compte_xaf.refresh_from_db()
        self.assertEqual(premiere.pk, seconde.pk)
        self.assertEqual(self.compte_xaf.solde, solde_apres)
        self.assertEqual(ConversionDevise.objects.count(), 1)

    def test_historique_conversions(self):
        convertir_devises(
            self.compte_xaf,
            self.compte_eur,
            Decimal("2000"),
            self.utilisateur,
            cle_idempotence=str(uuid.uuid4()),
        )
        reponse = self.client.get(reverse("devises:historique_conversions"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "CNV-")
        self.assertContains(reponse, "XAF")
        self.assertContains(reponse, "EUR")
