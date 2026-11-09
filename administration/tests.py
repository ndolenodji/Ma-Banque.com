"""Tests du back-office d'administration, des rapports et du journal d'audit."""

from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import make_aware

from comptes.models import JournalAudit
from devises.models import Devise
from transactions.models import CompteBancaire, Transaction

Utilisateur = get_user_model()


class AdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.devise = Devise.objects.get(code="XAF")
        cls.admin = Utilisateur.objects.create_user(
            username="admin_rapports",
            email="admin.rapports@example.test",
            password="MotDePasseAdmin2026!",
            telephone="66094001",
            role=Utilisateur.Role.ADMIN,
        )
        cls.agent = Utilisateur.objects.create_user(
            username="agent_rapports",
            email="agent.rapports@example.test",
            password="MotDePasseAgent2026!",
            telephone="66094002",
            role=Utilisateur.Role.AGENT,
        )
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="client_rapports",
            email="client.rapports@example.test",
            password="MotDePasseClient2026!",
            telephone="66094003",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.client_bancaire,
            devise=cls.devise,
            solde=Decimal("250000.00"),
        )
        cls.transaction = Transaction.objects.create(
            type_transaction=Transaction.Type.DEPOT,
            montant=Decimal("75000.00"),
            frais=Decimal("0.00"),
            compte_destination=cls.compte,
            statut=Transaction.Statut.REUSSIE,
            reference="TX-RAPPORT-TEST",
            date_transaction=make_aware(datetime.combine(timezone.localdate(), time(11, 0))),
        )
        JournalAudit.journaliser(cls.admin, "Test — entrée initiale", "Création du jeu de test")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_acces_back_office_par_role(self):
        self.client.force_login(self.client_bancaire)
        reponse = self.client.get(reverse("administration:tableau_de_bord"))
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse["Location"].startswith("/comptes/"))

        self.client.force_login(self.agent)
        reponse = self.client.get(reverse("administration:tableau_de_bord"))
        self.assertEqual(reponse.status_code, 200)

    def test_tableau_de_bord_et_graphiques_locaux(self):
        reponse = self.client.get(reverse("administration:tableau_de_bord"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "js/chart.umd.min.js")
        self.assertContains(reponse, "graphiqueVolumeAdmin")
        self.assertContains(reponse, "graphiqueTypesAdmin")
        self.assertContains(reponse, "graphiqueTempsReelAdmin")
        self.assertContains(reponse, "activite-temps-reel.json")
        self.assertEqual(reponse.context["volume_transactions"], 1)

    def test_activite_temps_reel_json_reservee_au_personnel(self):
        url = reverse("administration:activite_temps_reel")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.client_bancaire)
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.agent)
        reponse = self.client.get(url)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("no-store", reponse["Cache-Control"])
        donnees = reponse.json()
        self.assertEqual(len(donnees["labels"]), 12)
        self.assertEqual(len(donnees["transactions"]), 12)
        self.assertEqual(len(donnees["paiements_mobiles"]), 12)
        self.assertIn("volume_transactions", donnees)
        self.assertIn("horodatage", donnees)
        self.assertGreaterEqual(donnees["total_transactions"], 0)

    def test_creation_client_et_premier_compte_par_administrateur(self):
        url = reverse("administration:creer_client")
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.admin)
        reponse = self.client.post(
            url,
            {
                "first_name": "Nouveau",
                "last_name": "Client",
                "username": "nouveau_client_admin",
                "email": "nouveau.client.admin@example.test",
                "telephone": "66095001",
                "numero_piece_identite": "CNI-ADMIN-0001",
                "type_compte": CompteBancaire.TypeCompte.COURANT,
                "devise": self.devise.pk,
                "password1": "MotDePasseNouveau2026!",
                "password2": "MotDePasseNouveau2026!",
            },
        )
        client_cree = Utilisateur.objects.get(username="nouveau_client_admin")
        self.assertRedirects(
            reponse,
            reverse("administration:detail_client", args=[client_cree.pk]),
        )
        self.assertEqual(client_cree.role, Utilisateur.Role.CLIENT)
        compte = CompteBancaire.objects.get(proprietaire=client_cree)
        self.assertEqual(compte.solde, Decimal("0.00"))
        self.assertEqual(compte.statut, CompteBancaire.Statut.ACTIF)
        self.assertTrue(
            JournalAudit.objects.filter(action="Création de client").exists()
        )
        self.assertTrue(
            client_cree.notifications.filter(titre="Bienvenue à Ma Banque").exists()
        )

    def test_ouverture_d_un_compte_supplementaire_pour_un_client(self):
        nombre_avant = CompteBancaire.objects.filter(
            proprietaire=self.client_bancaire
        ).count()
        reponse = self.client.post(
            reverse(
                "administration:ouvrir_compte_client",
                args=[self.client_bancaire.pk],
            ),
            {"type_compte": CompteBancaire.TypeCompte.EPARGNE, "devise": self.devise.pk},
        )
        self.assertRedirects(
            reponse,
            reverse("administration:detail_client", args=[self.client_bancaire.pk]),
        )
        self.assertEqual(
            CompteBancaire.objects.filter(proprietaire=self.client_bancaire).count(),
            nombre_avant + 1,
        )
        compte = CompteBancaire.objects.filter(
            proprietaire=self.client_bancaire,
            type_compte=CompteBancaire.TypeCompte.EPARGNE,
        ).latest("id")
        self.assertEqual(compte.solde, Decimal("0.00"))

    def test_rapports_et_contexte(self):
        reponse = self.client.get(reverse("administration:rapports"), {"periode": "30"})
        self.assertEqual(reponse.status_code, 200)
        self.assertTemplateUsed(reponse, "administration/rapports.html")
        self.assertEqual(reponse.context["nb_transactions"], 1)
        self.assertEqual(reponse.context["nb_reussies"], 1)
        self.assertEqual(reponse.context["taux_reussite"], 100.0)
        self.assertContains(reponse, "graphiqueRapportVolume")

    def test_export_rapport_csv(self):
        reponse = self.client.get(reverse("administration:exporter_rapport_csv"), {"periode": "30"})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "text/csv; charset=utf-8")
        contenu = reponse.content.decode("utf-8-sig")
        self.assertIn("Référence;Date;Type", contenu)
        self.assertIn("TX-RAPPORT-TEST", contenu)
        self.assertTrue(JournalAudit.objects.filter(action="Export CSV — rapport d'activité").exists())

    def test_journal_audit_valide_puis_altere(self):
        reponse = self.client.get(reverse("administration:journal_audit"))
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.context["integrite_valide"])
        self.assertContains(reponse, "Chaîne d'intégrité valide")

        entree = JournalAudit.objects.filter(action="Test — entrée initiale").first()
        JournalAudit.objects.filter(pk=entree.pk).update(action="Entrée falsifiée")
        reponse = self.client.get(reverse("administration:journal_audit"))
        self.assertFalse(reponse.context["integrite_valide"])
        self.assertContains(reponse, "Anomalie d'intégrité")

    def test_export_journal_audit(self):
        reponse = self.client.get(reverse("administration:exporter_journal_audit_csv"))
        self.assertEqual(reponse.status_code, 200)
        contenu = reponse.content.decode("utf-8-sig")
        self.assertIn("Empreinte", contenu)
        self.assertIn("Test — entrée initiale", contenu)

    def test_changelists_admin_django(self):
        superutilisateur = Utilisateur.objects.create_superuser(
            username="super_admin_rapports",
            email="super.admin.rapports@example.test",
            password="MotDePasseSuperAdmin2026!",
            telephone="66094009",
        )
        self.client.force_login(superutilisateur)
        for nom in (
            "comptes_utilisateur",
            "transactions_transaction",
            "transactions_comptebancaire",
            "kyc_dossierkyc",
            "risques_alertefraude",
            "reseau_profilagent",
            "reseau_marchand",
            "reseau_demandepaiementmarchand",
            "factures_facture",
            "cartes_cartebancaire",
            "support_ticketsupport",
        ):
            with self.subTest(modele=nom):
                reponse = self.client.get(reverse(f"admin:{nom}_changelist"))
                self.assertEqual(reponse.status_code, 200)

    def test_export_transactions_neutre_les_formules(self):
        Transaction.objects.create(
            type_transaction=Transaction.Type.DEPOT,
            montant=Decimal("1.00"),
            compte_destination=self.compte,
            statut=Transaction.Statut.REUSSIE,
            description="=HYPERLINK(\"http://exemple.test\")",
        )
        reponse = self.client.get(reverse("administration:exporter_transactions_csv"))
        self.assertEqual(reponse.status_code, 200)
        contenu = reponse.content.decode("utf-8-sig")
        self.assertIn("ID;Type;Montant", contenu)

        reponse = self.client.get(reverse("administration:exporter_rapport_csv"), {"periode": "30"})
        contenu = reponse.content.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", contenu)
