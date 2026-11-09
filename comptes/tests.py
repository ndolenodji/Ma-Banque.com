"""Tests du simulateur USSD (canal téléphone simulé côté navigateur)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from devises.models import Devise
from transactions.models import CompteBancaire, Transaction

Utilisateur = get_user_model()


class SimulateurUssdTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.devise = Devise.objects.get(code="XAF")
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="ussd_client",
            email="ussd.client@example.test",
            password="MotDePasseUssd2026!",
            telephone="66091001",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.client_bancaire,
            devise=cls.devise,
            solde=Decimal("125000.00"),
        )
        cls.autre_client = Utilisateur.objects.create_user(
            username="ussd_autre",
            email="ussd.autre@example.test",
            password="MotDePasseUssd2026!",
            telephone="66091002",
        )
        cls.compte_autre = CompteBancaire.objects.create(
            proprietaire=cls.autre_client,
            devise=cls.devise,
            solde=Decimal("987654.00"),
        )

    def setUp(self):
        self.url = reverse("comptes:ussd")
        self.client_bancaire_connecte = self.client
        self.client_bancaire_connecte.force_login(self.client_bancaire)

    def test_connexion_obligatoire(self):
        self.client.logout()
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/connexion/", reponse["Location"])

    def test_page_affiche_simulation_sans_pin(self):
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.assertTemplateUsed(reponse, "comptes/ussd.html")
        contenu = reponse.content.decode()
        self.assertIn("Simulation", contenu)
        self.assertNotIn('type="password"', contenu)
        self.assertNotIn('name="pin"', contenu)

    def test_composer_ouvre_le_menu(self):
        reponse = self.client.post(self.url, {"action": "composer"}, follow=True)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.context["etat"], "menu")
        self.assertEqual(self.client.session["ussd_ecran"], "menu")
        self.assertContains(reponse, "Consulter le solde")

    def test_consultation_solde_affiche_uniquement_mes_comptes(self):
        self.client.post(self.url, {"action": "composer"})
        reponse = self.client.post(self.url, {"action": "choix", "choix": "1"}, follow=True)
        self.assertEqual(reponse.context["etat"], "solde")
        contenu = reponse.content.decode()
        self.assertIn(self.compte.numero_compte, contenu)
        self.assertIn("125 000", contenu)
        self.assertNotIn(self.compte_autre.numero_compte, contenu)
        self.assertNotIn("987 654", contenu)

    def test_releve_sans_operation(self):
        self.client.post(self.url, {"action": "composer"})
        reponse = self.client.post(self.url, {"action": "choix", "choix": "2"}, follow=True)
        self.assertEqual(reponse.context["etat"], "releve")
        self.assertContains(reponse, "Aucune opération récente")

    def test_cartes_et_aide(self):
        self.client.post(self.url, {"action": "composer"})
        reponse = self.client.post(self.url, {"action": "choix", "choix": "3"}, follow=True)
        self.assertEqual(reponse.context["etat"], "cartes")
        self.assertContains(reponse, "Aucune carte bancaire")

        self.client.post(self.url, {"action": "menu"})
        reponse = self.client.post(self.url, {"action": "choix", "choix": "4"}, follow=True)
        self.assertEqual(reponse.context["etat"], "aide")
        self.assertContains(reponse, "Ne communiquez jamais votre mot de passe")

    def test_quitter_puis_retour_menu(self):
        self.client.post(self.url, {"action": "composer"})
        reponse = self.client.post(self.url, {"action": "choix", "choix": "0"}, follow=True)
        self.assertEqual(reponse.context["etat"], "quitte")

        reponse = self.client.post(self.url, {"action": "menu"}, follow=True)
        self.assertEqual(reponse.context["etat"], "menu")

    def test_etat_de_session_invalide_reinitialise(self):
        session = self.client.session
        session["ussd_ecran"] = "injection"
        session.save()
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.context["etat"], "accueil")


class TableauDeBordTempsReelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.devise = Devise.objects.get(code="XAF")
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="client_temps_reel",
            email="client.temps.reel@example.test",
            password="MotDePasseTempsReel2026!",
            telephone="66092001",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.client_bancaire,
            devise=cls.devise,
            solde=Decimal("50000.00"),
        )
        cls.autre_client = Utilisateur.objects.create_user(
            username="autre_temps_reel",
            email="autre.temps.reel@example.test",
            password="MotDePasseTempsReel2026!",
            telephone="66092002",
        )
        cls.compte_autre = CompteBancaire.objects.create(
            proprietaire=cls.autre_client,
            devise=cls.devise,
            solde=Decimal("80000.00"),
        )
        Transaction.objects.create(
            type_transaction=Transaction.Type.DEPOT,
            montant=Decimal("12000.00"),
            compte_destination=cls.compte,
            statut=Transaction.Statut.REUSSIE,
            description="Dépôt temps réel du client",
        )
        Transaction.objects.create(
            type_transaction=Transaction.Type.DEPOT,
            montant=Decimal("99999.00"),
            compte_destination=cls.compte_autre,
            statut=Transaction.Statut.REUSSIE,
            description="Dépôt d'un autre client",
        )

    def setUp(self):
        self.client.force_login(self.client_bancaire)

    def test_tableau_de_bord_contient_le_graphique_temps_reel(self):
        reponse = self.client.get(reverse("comptes:tableau_de_bord"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "graphiqueTempsReelClient")
        self.assertContains(reponse, "graphiqueTempsReelClientMobile")
        self.assertContains(reponse, "activite-temps-reel.json")

    def test_activite_temps_reel_limitee_aux_comptes_du_client(self):
        reponse = self.client.get(reverse("comptes:activite_temps_reel"))
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("no-store", reponse["Cache-Control"])
        donnees = reponse.json()
        self.assertEqual(len(donnees["labels"]), 12)
        self.assertEqual(len(donnees["depots"]), 12)
        self.assertEqual(donnees["total_depots"], 12000.0)
        self.assertNotIn(99999.0, donnees["depots"])
        self.assertEqual(donnees["solde_disponible"], "50000.00")

    def test_activite_temps_reel_exige_une_connexion(self):
        self.client.logout()
        reponse = self.client.get(reverse("comptes:activite_temps_reel"))
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/connexion/", reponse["Location"])
