"""Tests du moteur de score et de l'instruction des alertes de fraude."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import make_aware

from comptes.models import JournalAudit, Notification
from devises.models import Devise
from risques.models import AlerteFraude
from risques.services import evaluer_transaction
from transactions.models import CompteBancaire, Transaction

Utilisateur = get_user_model()


class MoteurRisqueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.devise = Devise.objects.get(code="XAF")
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="risque_client",
            email="risque.client@example.test",
            password="MotDePasseRisque2026!",
            telephone="66092001",
        )
        cls.destinataire = Utilisateur.objects.create_user(
            username="risque_dest",
            email="risque.dest@example.test",
            password="MotDePasseRisque2026!",
            telephone="66092002",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.client_bancaire,
            devise=cls.devise,
            solde=Decimal("5000000.00"),
        )
        cls.compte_destination = CompteBancaire.objects.create(
            proprietaire=cls.destinataire,
            devise=cls.devise,
            solde=Decimal("0.00"),
        )
        # Compte ancien : évite le facteur « compte récent » dans les tests.
        CompteBancaire.objects.filter(pk=cls.compte.pk).update(
            date_ouverture=timezone.now() - timedelta(days=60)
        )

    def _transaction(self, montant="1200000.00", type_transaction=None, statut=None):
        midi = make_aware(datetime.combine(timezone.localdate(), time(12, 0)))
        return Transaction.objects.create(
            type_transaction=type_transaction or Transaction.Type.VIREMENT,
            montant=Decimal(montant),
            compte_source=self.compte,
            compte_destination=self.compte_destination,
            statut=statut or Transaction.Statut.REUSSIE,
            canal=Transaction.Canal.APPLICATION,
            date_transaction=midi,
        )

    def test_alerte_creee_pour_montant_critique_et_nouveau_destinataire(self):
        transaction = self._transaction()
        alerte = AlerteFraude.objects.get(transaction=transaction)
        codes = {regle["code"] for regle in alerte.regles}
        self.assertIn("MONTANT_CRITIQUE", codes)
        self.assertIn("NOUVEAU_DESTINATAIRE", codes)
        self.assertGreaterEqual(alerte.score, 50)
        self.assertIn(alerte.niveau, [AlerteFraude.Niveau.ELEVE, AlerteFraude.Niveau.CRITIQUE])
        self.assertIn("revue humaine", alerte.explication)
        self.assertEqual(alerte.statut, AlerteFraude.Statut.NOUVELLE)

    def test_pas_d_alerte_sous_le_seuil(self):
        transaction = self._transaction(montant="1000.00")
        score, regles = evaluer_transaction(transaction)
        self.assertLess(score, 25)
        self.assertTrue(regles)
        self.assertFalse(AlerteFraude.objects.filter(transaction=transaction).exists())

    def test_depot_et_transaction_echouee_non_analyses(self):
        depot = Transaction.objects.create(
            type_transaction=Transaction.Type.DEPOT,
            montant=Decimal("2000000.00"),
            compte_destination=self.compte,
            statut=Transaction.Statut.REUSSIE,
        )
        echouee = self._transaction(montant="2000000.00", statut=Transaction.Statut.ECHOUEE)
        self.assertFalse(AlerteFraude.objects.filter(transaction=depot).exists())
        self.assertFalse(AlerteFraude.objects.filter(transaction=echouee).exists())

    def test_score_explicable_et_plafonne(self):
        transaction = self._transaction(montant="9000000.00")
        score, regles = evaluer_transaction(transaction)
        self.assertLessEqual(score, 100)
        self.assertTrue(regles)
        self.assertTrue(all({"code", "libelle", "points"} <= set(regle) for regle in regles))


class InstructionAlerteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.devise = Devise.objects.get(code="XAF")
        cls.admin = Utilisateur.objects.create_user(
            username="risque_admin",
            email="risque.admin@example.test",
            password="MotDePasseRisque2026!",
            telephone="66093001",
            role=Utilisateur.Role.ADMIN,
        )
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="risque_suivi",
            email="risque.suivi@example.test",
            password="MotDePasseRisque2026!",
            telephone="66093002",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.client_bancaire,
            devise=cls.devise,
            solde=Decimal("3000000.00"),
        )
        transaction = Transaction.objects.create(
            type_transaction=Transaction.Type.RETRAIT,
            montant=Decimal("1500000.00"),
            compte_source=cls.compte,
            statut=Transaction.Statut.REUSSIE,
            date_transaction=make_aware(datetime.combine(timezone.localdate(), time(12, 0))),
        )
        cls.alerte = AlerteFraude.objects.get(transaction=transaction)

    def setUp(self):
        self.alerte.refresh_from_db()
        self.client.force_login(self.admin)
        self.url_detail = reverse("risques:detail_alerte", args=[self.alerte.pk])
        self.url_traiter = reverse("risques:traiter", args=[self.alerte.pk])

    def test_liste_et_filtres(self):
        reponse = self.client.get(reverse("risques:alertes"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, self.alerte.reference)
        self.assertEqual(reponse.context["statistiques"]["nouvelles"], 1)

        reponse = self.client.get(reverse("risques:alertes"), {"niveau": "FAIBLE"})
        self.assertNotContains(reponse, self.alerte.reference)

    def test_client_non_autorise(self):
        self.client.force_login(self.client_bancaire)
        reponse = self.client.get(reverse("risques:alertes"))
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse["Location"].startswith("/comptes/"))

    def test_transitions_et_commentaire_obligatoire(self):
        reponse = self.client.post(self.url_traiter, {"decision": "CONFIRMEE", "commentaire": "test"})
        self.assertEqual(reponse.status_code, 302)
        self.alerte.refresh_from_db()
        self.assertEqual(self.alerte.statut, AlerteFraude.Statut.NOUVELLE)

        self.client.post(self.url_traiter, {"decision": "EN_REVUE", "commentaire": "Vérification"})
        self.alerte.refresh_from_db()
        self.assertEqual(self.alerte.statut, AlerteFraude.Statut.EN_REVUE)
        self.assertEqual(self.alerte.traite_par, self.admin)
        self.assertIsNotNone(self.alerte.date_traitement)

        self.client.post(self.url_traiter, {"decision": "REJETEE", "commentaire": ""})
        self.alerte.refresh_from_db()
        self.assertEqual(self.alerte.statut, AlerteFraude.Statut.EN_REVUE)

        self.client.post(self.url_traiter, {"decision": "CONFIRMEE", "commentaire": "Confirmé après contrôle"})
        self.alerte.refresh_from_db()
        self.assertEqual(self.alerte.statut, AlerteFraude.Statut.CONFIRMEE)
        self.assertIn("Confirmé après contrôle", self.alerte.commentaire)
        self.assertTrue(
            Notification.objects.filter(destinataire=self.client_bancaire, type_notification="SECURITE").exists()
        )
        self.assertTrue(
            JournalAudit.objects.filter(action="Fraude — alerte instruite").exists()
        )

    def test_decision_finale_irreversible(self):
        AlerteFraude.objects.filter(pk=self.alerte.pk).update(statut=AlerteFraude.Statut.CONFIRMEE)
        self.client.post(self.url_traiter, {"decision": "REJETEE", "commentaire": "tardif"})
        self.alerte.refresh_from_db()
        self.assertEqual(self.alerte.statut, AlerteFraude.Statut.CONFIRMEE)

    def test_endpoint_temps_reel(self):
        reponse = self.client.get(reverse("risques:alertes_non_lues_json"))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()["nouvelles"], 1)
