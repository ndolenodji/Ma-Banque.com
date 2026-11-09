"""Tests de la FAQ, des tickets et du fil de support."""

from django.test import TestCase
from django.urls import reverse

from comptes.models import Notification, Utilisateur

from .models import ArticleFAQ, MessageSupport, TicketSupport


class SupportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_utilisateur = Utilisateur.objects.create_user(
            username="client_support",
            password="MotDePasse!2026",
            telephone="+23568000001",
            first_name="Amina",
            last_name="Youssouf",
        )
        cls.autre = Utilisateur.objects.create_user(
            username="autre_support",
            password="MotDePasse!2026",
            telephone="+23568000002",
        )
        cls.agent = Utilisateur.objects.create_user(
            username="agent_support",
            password="MotDePasse!2026",
            telephone="+23568000003",
            role=Utilisateur.Role.AGENT,
        )

    def setUp(self):
        self.client.force_login(self.client_utilisateur)

    def _creer_ticket(self, sujet="Problème de virement"):
        ticket = TicketSupport.objects.create(
            client=self.client_utilisateur, sujet=sujet, categorie=TicketSupport.Categorie.TRANSACTION
        )
        MessageSupport.objects.create(
            ticket=ticket, auteur=self.client_utilisateur, message="Mon virement est bloqué."
        )
        return ticket

    def test_faq_publique_et_articles_seed(self):
        self.client.logout()
        reponse = self.client.get(reverse("support:faq"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Comment ouvrir un compte bancaire")
        self.assertTrue(ArticleFAQ.objects.filter(publie=True).exists())

    def test_faq_recherche_filtre(self):
        reponse = self.client.get(reverse("support:faq"), {"q": "mobile money"})
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "paiement mobile money")

    def test_creation_ticket_et_notifications(self):
        reponse = self.client.post(
            reverse("support:creer_ticket"),
            {
                "sujet": "Cotisation débitée deux fois",
                "categorie": TicketSupport.Categorie.TRANSACTION,
                "message": "Deux débits identiques apparaissent sur mon compte.",
            },
        )
        ticket = TicketSupport.objects.get(client=self.client_utilisateur)
        self.assertRedirects(
            reponse,
            reverse("support:detail_ticket", args=[ticket.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(ticket.statut, TicketSupport.Statut.OUVERT)
        self.assertEqual(ticket.messages.count(), 1)
        self.assertTrue(
            Notification.objects.filter(
                destinataire=self.client_utilisateur, type_notification="SUPPORT"
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                destinataire=self.agent, type_notification="SYSTEME"
            ).exists()
        )

    def test_ticket_dun_autre_client_inaccessible(self):
        ticket = self._creer_ticket()
        self.client.force_login(self.autre)
        reponse = self.client.get(reverse("support:detail_ticket", args=[ticket.pk]))
        self.assertEqual(reponse.status_code, 302)
        self.assertIn(reverse("support:mes_tickets"), reponse["Location"])

    def test_reponse_client_passe_en_cours(self):
        ticket = self._creer_ticket()
        reponse = self.client.post(
            reverse("support:detail_ticket", args=[ticket.pk]),
            {"message": "J'ajoute la référence de l'opération."},
        )
        self.assertEqual(reponse.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, TicketSupport.Statut.EN_COURS)
        self.assertEqual(ticket.messages.count(), 2)

    def test_reponse_agent_note_interne_et_resolution(self):
        ticket = self._creer_ticket()
        self.client.force_login(self.agent)
        reponse = self.client.post(
            reverse("support:detail_ticket", args=[ticket.pk]),
            {
                "message": "Vérification en cours, note confidentielle.",
                "note_interne": "on",
                "statut": TicketSupport.Statut.RESOLU,
                "priorite": TicketSupport.Priorite.HAUTE,
            },
        )
        self.assertEqual(reponse.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, TicketSupport.Statut.RESOLU)
        self.assertEqual(ticket.priorite, TicketSupport.Priorite.HAUTE)
        self.assertEqual(ticket.assigne_a, self.agent)
        self.assertIsNotNone(ticket.date_cloture)

        # Le client ne voit jamais la note interne.
        self.client.force_login(self.client_utilisateur)
        reponse = self.client.get(reverse("support:detail_ticket", args=[ticket.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertNotContains(reponse, "note confidentielle")
        self.assertContains(reponse, "Ce ticket est clôturé")

        # Un ticket clôturé refuse les nouvelles réponses client.
        reponse = self.client.post(
            reverse("support:detail_ticket", args=[ticket.pk]),
            {"message": "Nouvelle demande après clôture."},
        )
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(ticket.messages.filter(message__icontains="Nouvelle demande").count(), 0)

    def test_agent_voit_tous_les_tickets_client_uniquement_les_siens(self):
        ticket = self._creer_ticket("Ticket visible agent")
        ticket_autre = TicketSupport.objects.create(
            client=self.autre, sujet="Ticket autre client", categorie=TicketSupport.Categorie.AUTRE
        )
        self.client.force_login(self.agent)
        reponse = self.client.get(reverse("support:mes_tickets"))
        self.assertContains(reponse, ticket.reference)
        self.assertContains(reponse, ticket_autre.reference)

        self.client.force_login(self.client_utilisateur)
        reponse = self.client.get(reverse("support:mes_tickets"))
        self.assertContains(reponse, ticket.reference)
        self.assertNotContains(reponse, ticket_autre.reference)

    def test_cloture_par_client(self):
        ticket = self._creer_ticket()
        reponse = self.client.post(reverse("support:cloturer_ticket", args=[ticket.pk]))
        self.assertEqual(reponse.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, TicketSupport.Statut.FERME)
        self.assertIsNotNone(ticket.date_cloture)

    def test_filtres_tickets(self):
        premier = self._creer_ticket("Premier ticket")
        second = TicketSupport.objects.create(
            client=self.client_utilisateur,
            sujet="Second ticket",
            categorie=TicketSupport.Categorie.CARTE,
            statut=TicketSupport.Statut.FERME,
        )
        reponse = self.client.get(
            reverse("support:mes_tickets"), {"statut": TicketSupport.Statut.FERME}
        )
        self.assertContains(reponse, second.sujet)
        self.assertNotContains(reponse, premier.sujet)
