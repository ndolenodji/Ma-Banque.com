"""Tests des cartes bancaires de démonstration."""

from django.test import TestCase
from django.urls import reverse

from comptes.models import Notification, Utilisateur
from kyc.models import DossierKYC
from transactions.models import CompteBancaire

from .models import CarteBancaire


class CartesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_utilisateur = Utilisateur.objects.create_user(
            username="client_cartes",
            password="MotDePasse!2026",
            telephone="+23561000001",
            first_name="Amina",
            last_name="Saleh",
        )
        DossierKYC.objects.create(
            utilisateur=cls.client_utilisateur, statut=DossierKYC.Statut.APPROUVE
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.client_utilisateur,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            solde=100000,
        )
        cls.autre = Utilisateur.objects.create_user(
            username="autre_cartes",
            password="MotDePasse!2026",
            telephone="+23561000002",
        )

    def setUp(self):
        self.client.force_login(self.client_utilisateur)

    def test_acces_page_cartes(self):
        reponse = self.client.get(reverse("cartes:mes_cartes"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Cartes de démonstration")

    def test_creation_carte_virtuelle_active(self):
        reponse = self.client.post(
            reverse("cartes:demander_carte"),
            {
                "compte": self.compte.pk,
                "type_carte": CarteBancaire.TypeCarte.VIRTUELLE,
                "nom_titulaire": "Amina Saleh",
            },
        )
        carte = CarteBancaire.objects.get(utilisateur=self.client_utilisateur)
        self.assertRedirects(
            reponse, reverse("cartes:detail_carte", args=[carte.pk]), fetch_redirect_response=False
        )
        self.assertEqual(carte.statut, CarteBancaire.Statut.ACTIVE)
        self.assertTrue(carte.identifiant_demo.startswith("DEMO-"))
        self.assertEqual(len(carte.quatre_derniers), 4)
        self.assertTrue(Notification.objects.filter(destinataire=self.client_utilisateur).exists())

    def test_creation_carte_physique_demandee(self):
        self.client.post(
            reverse("cartes:demander_carte"),
            {
                "compte": self.compte.pk,
                "type_carte": CarteBancaire.TypeCarte.PHYSIQUE,
                "nom_titulaire": "Amina Saleh",
            },
        )
        carte = CarteBancaire.objects.get(utilisateur=self.client_utilisateur)
        self.assertEqual(carte.statut, CarteBancaire.Statut.DEMANDEE)

    def test_demande_carte_exige_kyc(self):
        autre = Utilisateur.objects.create_user(
            username="sans_kyc",
            password="MotDePasse!2026",
            telephone="+23561000003",
        )
        CompteBancaire.objects.create(proprietaire=autre, solde=5000)
        self.client.force_login(autre)
        reponse = self.client.get(reverse("cartes:demander_carte"))
        self.assertRedirects(reponse, reverse("kyc:mon_dossier"), fetch_redirect_response=False)

    def test_cycle_gel_reactivation_annulation(self):
        carte = CarteBancaire.objects.create(
            utilisateur=self.client_utilisateur,
            compte=self.compte,
            nom_titulaire="Amina Saleh",
            date_expiration="2030-01-01",
            statut=CarteBancaire.Statut.ACTIVE,
        )
        self.client.post(reverse("cartes:basculer_carte", args=[carte.pk]))
        carte.refresh_from_db()
        self.assertEqual(carte.statut, CarteBancaire.Statut.GELEE)

        self.client.post(reverse("cartes:basculer_carte", args=[carte.pk]))
        carte.refresh_from_db()
        self.assertEqual(carte.statut, CarteBancaire.Statut.ACTIVE)

        self.client.post(reverse("cartes:annuler_carte", args=[carte.pk]))
        carte.refresh_from_db()
        self.assertEqual(carte.statut, CarteBancaire.Statut.ANNULEE)

        self.client.post(reverse("cartes:basculer_carte", args=[carte.pk]))
        carte.refresh_from_db()
        self.assertEqual(carte.statut, CarteBancaire.Statut.ANNULEE)

    def test_modification_plafonds(self):
        carte = CarteBancaire.objects.create(
            utilisateur=self.client_utilisateur,
            compte=self.compte,
            nom_titulaire="Amina Saleh",
            date_expiration="2030-01-01",
            statut=CarteBancaire.Statut.ACTIVE,
        )
        reponse = self.client.post(
            reverse("cartes:detail_carte", args=[carte.pk]),
            {
                "paiements_en_ligne": "on",
                "plafond_journalier": "250000",
                "plafond_mensuel": "1000000",
            },
        )
        self.assertRedirects(
            reponse, reverse("cartes:detail_carte", args=[carte.pk]), fetch_redirect_response=False
        )
        carte.refresh_from_db()
        self.assertEqual(carte.plafond_journalier, 250000)
        self.assertFalse(carte.paiements_internationaux)
        self.assertFalse(carte.retraits_autorises)

    def test_plafond_journalier_superieur_refuse(self):
        carte = CarteBancaire.objects.create(
            utilisateur=self.client_utilisateur,
            compte=self.compte,
            nom_titulaire="Amina Saleh",
            date_expiration="2030-01-01",
            statut=CarteBancaire.Statut.ACTIVE,
        )
        reponse = self.client.post(
            reverse("cartes:detail_carte", args=[carte.pk]),
            {
                "paiements_en_ligne": "on",
                "plafond_journalier": "2000000",
                "plafond_mensuel": "1000000",
            },
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "ne peut pas dépasser")

    def test_carte_d_un_autre_client_inaccessible(self):
        carte = CarteBancaire.objects.create(
            utilisateur=self.autre,
            compte=CompteBancaire.objects.create(proprietaire=self.autre, solde=0),
            nom_titulaire="Autre Client",
            date_expiration="2030-01-01",
            statut=CarteBancaire.Statut.ACTIVE,
        )
        reponse = self.client.get(reverse("cartes:detail_carte", args=[carte.pk]))
        self.assertEqual(reponse.status_code, 404)

    def test_aucune_donnee_sensible_affichee(self):
        self.client.post(
            reverse("cartes:demander_carte"),
            {
                "compte": self.compte.pk,
                "type_carte": CarteBancaire.TypeCarte.VIRTUELLE,
                "nom_titulaire": "Amina Saleh",
            },
        )
        carte = CarteBancaire.objects.get(utilisateur=self.client_utilisateur)
        reponse = self.client.get(reverse("cartes:detail_carte", args=[carte.pk]))
        self.assertContains(reponse, carte.numero_masque)
        champs = {champ.name for champ in CarteBancaire._meta.get_fields()}
        for interdit in ("pan", "cvv", "pin", "code_pin"):
            self.assertNotIn(interdit, champs)
