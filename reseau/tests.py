"""Tests du réseau d'agents et de marchands (QR, caisse, idempotence)."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comptes.models import Notification, Utilisateur
from kyc.models import DossierKYC
from transactions.models import CompteBancaire, RegleFrais, Transaction

from .models import DemandePaiementMarchand, Marchand, OperationAgent, ProfilAgent


class ReseauTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="client_reseau",
            password="MotDePasse!2026",
            telephone="+23567000001",
            first_name="Fatime",
            last_name="Oumar",
        )
        DossierKYC.objects.create(
            utilisateur=cls.client_bancaire, statut=DossierKYC.Statut.APPROUVE
        )
        cls.compte_client = CompteBancaire.objects.create(
            proprietaire=cls.client_bancaire,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            solde=Decimal("100000.00"),
        )

        cls.marchand_user = Utilisateur.objects.create_user(
            username="marchand_reseau",
            password="MotDePasse!2026",
            telephone="+23567000002",
            first_name="Boutique",
            last_name="Tchad",
        )
        DossierKYC.objects.create(
            utilisateur=cls.marchand_user, statut=DossierKYC.Statut.APPROUVE
        )
        cls.compte_marchand = CompteBancaire.objects.create(
            proprietaire=cls.marchand_user,
            type_compte=CompteBancaire.TypeCompte.PROFESSIONNEL,
            solde=Decimal("50000.00"),
        )
        cls.marchand = Marchand.objects.create(
            proprietaire=cls.marchand_user,
            nom="Boutique Tchad",
            categorie="Commerce",
            ville="N'Djamena",
            compte_reglement=cls.compte_marchand,
            statut=Marchand.Statut.ACTIF,
        )

        cls.agent_user = Utilisateur.objects.create_user(
            username="agent_reseau",
            password="MotDePasse!2026",
            telephone="+23567000003",
            role=Utilisateur.Role.AGENT,
        )
        cls.profil_agent = ProfilAgent.objects.create(
            utilisateur=cls.agent_user,
            nom_agence="Agence Centre",
            ville="N'Djamena",
            float_caisse=Decimal("100000.00"),
            plafond_journalier=Decimal("500000.00"),
        )
        cls.autre = Utilisateur.objects.create_user(
            username="autre_reseau",
            password="MotDePasse!2026",
            telephone="+23567000004",
        )
        RegleFrais.objects.create(
            type_operation=RegleFrais.Operation.PAIEMENT_MARCHAND,
            libelle="Frais marchand de test",
            frais_fixe=Decimal("50.00"),
        )

    def setUp(self):
        self.client.force_login(self.client_bancaire)

    def _creer_demande(self, montant="5000.00", expire_dans=30, statut=DemandePaiementMarchand.Statut.EN_ATTENTE):
        return DemandePaiementMarchand.objects.create(
            marchand=self.marchand,
            montant=Decimal(montant),
            description="Achat de test",
            statut=statut,
            date_expiration=timezone.now() + timedelta(minutes=expire_dans),
        )

    def _donnees_agent(self, montant="10000.00", type_operation=OperationAgent.Type.CAISSE_IN):
        return {
            "numero_compte": self.compte_client.numero_compte,
            "type_operation": type_operation,
            "montant": montant,
            "note": "Test caisse",
            "cle_idempotence": str(uuid.uuid4()),
        }

    def test_annuaires_accessibles(self):
        for nom in ("annuaire_agents", "annuaire_marchands"):
            reponse = self.client.get(reverse(f"reseau:{nom}"))
            self.assertEqual(reponse.status_code, 200, nom)
        reponse = self.client.get(reverse("reseau:annuaire_marchands"))
        self.assertContains(reponse, "Boutique Tchad")
        reponse = self.client.get(reverse("reseau:annuaire_agents"))
        self.assertContains(reponse, "Agence Centre")

    def test_inscription_marchand_exige_kyc(self):
        sans_kyc = Utilisateur.objects.create_user(
            username="marchand_sans_kyc",
            password="MotDePasse!2026",
            telephone="+23567000005",
        )
        self.client.force_login(sans_kyc)
        reponse = self.client.post(
            reverse("reseau:inscription_marchand"),
            {
                "nom": "Épicerie test",
                "categorie": "Commerce",
                "ville": "Moundou",
                "adresse": "",
                "compte_reglement": self.compte_client.pk,
            },
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "KYC doit être approuvé")
        self.assertFalse(Marchand.objects.filter(proprietaire=sans_kyc).exists())

    def test_creation_demande_marchand_et_qr(self):
        self.client.force_login(self.marchand_user)
        reponse = self.client.post(
            reverse("reseau:creer_demande"),
            {"montant": "7500.00", "description": "Prestation", "duree": "30"},
        )
        demande = DemandePaiementMarchand.objects.get(marchand=self.marchand)
        self.assertRedirects(
            reponse,
            reverse("reseau:detail_demande", args=[demande.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(demande.montant, Decimal("7500.00"))
        self.assertEqual(demande.statut, DemandePaiementMarchand.Statut.EN_ATTENTE)
        self.assertGreater(demande.date_expiration, timezone.now())

        reponse = self.client.get(reverse("reseau:detail_demande", args=[demande.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "QR code")

        reponse = self.client.get(reverse("reseau:qr_demande", args=[demande.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "image/png")
        self.assertTrue(reponse.content.startswith(b"\x89PNG"))

        self.client.force_login(self.autre)
        reponse = self.client.get(reverse("reseau:qr_demande", args=[demande.pk]))
        self.assertEqual(reponse.status_code, 403)
        reponse = self.client.get(reverse("reseau:detail_demande", args=[demande.pk]))
        self.assertEqual(reponse.status_code, 403)

    def test_paiement_demande_debite_et_credite_avec_frais(self):
        demande = self._creer_demande()
        reponse = self.client.post(
            reverse("reseau:payer_demande", args=[demande.jeton]),
            {"compte_bancaire": self.compte_client.pk},
        )
        self.assertRedirects(
            reponse,
            reverse("reseau:payer_demande", args=[demande.jeton]),
            fetch_redirect_response=False,
        )
        demande.refresh_from_db()
        self.compte_client.refresh_from_db()
        self.compte_marchand.refresh_from_db()
        self.assertEqual(demande.statut, DemandePaiementMarchand.Statut.PAYEE)
        self.assertEqual(demande.payeur, self.client_bancaire)
        self.assertEqual(self.compte_client.solde, Decimal("94950.00"))
        self.assertEqual(self.compte_marchand.solde, Decimal("55000.00"))
        self.assertEqual(demande.transaction.frais, Decimal("50.00"))
        self.assertEqual(demande.transaction.type_transaction, Transaction.Type.PAIEMENT_MARCHAND)
        self.assertTrue(Notification.objects.filter(destinataire=self.marchand_user).exists())

        # Une seconde soumission ne redébite jamais le compte.
        self.client.post(
            reverse("reseau:payer_demande", args=[demande.jeton]),
            {"compte_bancaire": self.compte_client.pk},
        )
        self.compte_client.refresh_from_db()
        self.assertEqual(self.compte_client.solde, Decimal("94950.00"))
        self.assertEqual(
            Transaction.objects.filter(type_transaction=Transaction.Type.PAIEMENT_MARCHAND).count(), 1
        )

    def test_marchand_ne_peut_pas_payer_sa_propre_demande(self):
        demande = self._creer_demande()
        self.client.force_login(self.marchand_user)
        reponse = self.client.post(
            reverse("reseau:payer_demande", args=[demande.jeton]),
            {"compte_bancaire": self.compte_marchand.pk},
        )
        self.assertEqual(reponse.status_code, 302)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandePaiementMarchand.Statut.EN_ATTENTE)
        self.compte_marchand.refresh_from_db()
        self.assertEqual(self.compte_marchand.solde, Decimal("50000.00"))

    def test_demande_expiree_non_payable(self):
        demande = self._creer_demande(expire_dans=-5)
        reponse = self.client.post(
            reverse("reseau:payer_demande", args=[demande.jeton]),
            {"compte_bancaire": self.compte_client.pk},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "expiré")
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandePaiementMarchand.Statut.EXPIREE)
        self.compte_client.refresh_from_db()
        self.assertEqual(self.compte_client.solde, Decimal("100000.00"))

    def test_annulation_demande_marchand(self):
        demande = self._creer_demande()
        self.client.force_login(self.marchand_user)
        reponse = self.client.post(reverse("reseau:annuler_demande", args=[demande.pk]))
        self.assertEqual(reponse.status_code, 302)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandePaiementMarchand.Statut.ANNULEE)

    def test_operation_agent_cash_in_et_idempotence(self):
        self.client.force_login(self.agent_user)
        donnees = self._donnees_agent()
        reponse = self.client.post(reverse("reseau:espace_agent"), donnees)
        self.assertRedirects(reponse, reverse("reseau:espace_agent"), fetch_redirect_response=False)

        self.compte_client.refresh_from_db()
        self.profil_agent.refresh_from_db()
        self.assertEqual(self.compte_client.solde, Decimal("110000.00"))
        self.assertEqual(self.profil_agent.float_caisse, Decimal("110000.00"))
        self.assertEqual(OperationAgent.objects.count(), 1)
        self.assertEqual(
            OperationAgent.objects.get().transaction.type_transaction, Transaction.Type.DEPOT
        )

        # Même clé d'idempotence : aucun second mouvement.
        self.client.post(reverse("reseau:espace_agent"), donnees)
        self.compte_client.refresh_from_db()
        self.profil_agent.refresh_from_db()
        self.assertEqual(self.compte_client.solde, Decimal("110000.00"))
        self.assertEqual(self.profil_agent.float_caisse, Decimal("110000.00"))
        self.assertEqual(OperationAgent.objects.count(), 1)

    def test_operation_agent_cash_out_diminue_le_float(self):
        self.client.force_login(self.agent_user)
        reponse = self.client.post(
            reverse("reseau:espace_agent"),
            self._donnees_agent("20000.00", OperationAgent.Type.CAISSE_OUT),
        )
        self.assertEqual(reponse.status_code, 302)
        self.compte_client.refresh_from_db()
        self.profil_agent.refresh_from_db()
        self.assertEqual(self.compte_client.solde, Decimal("80000.00"))
        self.assertEqual(self.profil_agent.float_caisse, Decimal("80000.00"))
        self.assertEqual(
            OperationAgent.objects.get().transaction.type_transaction, Transaction.Type.RETRAIT
        )

    def test_operation_agent_plafond_journalier(self):
        self.profil_agent.plafond_journalier = Decimal("1000.00")
        self.profil_agent.save(update_fields=["plafond_journalier"])
        self.client.force_login(self.agent_user)
        reponse = self.client.post(
            reverse("reseau:espace_agent"), self._donnees_agent("1500.00")
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Plafond journalier")
        self.assertFalse(OperationAgent.objects.exists())
        self.compte_client.refresh_from_db()
        self.assertEqual(self.compte_client.solde, Decimal("100000.00"))

    def test_operation_agent_float_insuffisant(self):
        self.profil_agent.float_caisse = Decimal("100.00")
        self.profil_agent.save(update_fields=["float_caisse"])
        self.client.force_login(self.agent_user)
        reponse = self.client.post(
            reverse("reseau:espace_agent"),
            self._donnees_agent("5000.00", OperationAgent.Type.CAISSE_OUT),
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Float de caisse insuffisant")
        self.assertFalse(OperationAgent.objects.exists())
        self.compte_client.refresh_from_db()
        self.assertEqual(self.compte_client.solde, Decimal("100000.00"))

    def test_client_ne_peut_pas_acceder_a_la_console_agent(self):
        reponse = self.client.get(reverse("reseau:espace_agent"))
        self.assertEqual(reponse.status_code, 302)
        self.assertIn(reverse("comptes:tableau_de_bord"), reponse["Location"])
