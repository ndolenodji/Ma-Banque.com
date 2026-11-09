"""Tests du parcours KYC : pièces privées, soumission et décisions."""

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from comptes.models import JournalAudit, Notification, SmsSimule, Utilisateur

from .models import DocumentKYC, DossierKYC
from .storage import stockage_prive_kyc


class KYCTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_bancaire = Utilisateur.objects.create_user(
            username="client_kyc",
            password="MotDePasse!2026",
            telephone="+23563100001",
            first_name="Client",
            last_name="KYC",
        )
        cls.autre_client = Utilisateur.objects.create_user(
            username="client_kyc_autre",
            password="MotDePasse!2026",
            telephone="+23563100002",
        )
        cls.agent = Utilisateur.objects.create_user(
            username="agent_kyc",
            password="MotDePasse!2026",
            telephone="+23563100003",
            role=Utilisateur.Role.AGENT,
        )
        cls.admin = Utilisateur.objects.create_user(
            username="admin_kyc",
            password="MotDePasse!2026",
            telephone="+23563100004",
            role=Utilisateur.Role.ADMIN,
        )

    def setUp(self):
        self.repertoire = tempfile.TemporaryDirectory()
        self.ancienne_location = stockage_prive_kyc.location
        stockage_prive_kyc._location = self.repertoire.name
        self.client.force_login(self.client_bancaire)

    def tearDown(self):
        stockage_prive_kyc._location = self.ancienne_location
        self.repertoire.cleanup()

    def _dossier(self, utilisateur=None):
        utilisateur = utilisateur or self.client_bancaire
        return DossierKYC.objects.get_or_create(utilisateur=utilisateur)[0]

    def _renseigner_informations(self):
        return self.client.post(
            reverse("kyc:enregistrer_informations"),
            {
                "type_piece": DossierKYC.TypePiece.CNI,
                "numero_piece": "CNI-2026-000123",
                "date_naissance": "1990-05-12",
                "nationalite": "Tchadienne",
                "adresse": "Quartier de recette, N'Djamena",
                "profession": "Ingénieure",
            },
        )

    def _ajouter_document(self, type_document, nom="piece.pdf", contenu=b"%PDF-1.4\n%%EOF"):
        return self.client.post(
            reverse("kyc:ajouter_document"),
            {
                "type_document": type_document,
                "fichier": SimpleUploadedFile(nom, contenu, content_type="application/pdf"),
            },
        )

    def _dossier_soumis(self):
        self._renseigner_informations()
        self._ajouter_document(DocumentKYC.TypeDocument.CNI_RECTO, "recto.pdf")
        self._ajouter_document(DocumentKYC.TypeDocument.CNI_VERSO, "verso.pdf")
        self.client.post(reverse("kyc:soumettre"))
        dossier = self._dossier()
        dossier.refresh_from_db()
        return dossier

    def test_page_kyc_accessible_au_client(self):
        reponse = self.client.get(reverse("kyc:mon_dossier"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Vérification d'identité")
        self.assertTrue(DossierKYC.objects.filter(utilisateur=self.client_bancaire).exists())

    def test_soumission_refusee_sans_les_deux_faces(self):
        self._renseigner_informations()
        self._ajouter_document(DocumentKYC.TypeDocument.CNI_RECTO, "recto.pdf")
        reponse = self.client.post(reverse("kyc:soumettre"), follow=True)
        dossier = self._dossier()
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, DossierKYC.Statut.NON_SOUMIS)
        self.assertContains(reponse, "deux faces de la pi")

    def test_parcours_complet_jusqu_a_approbation(self):
        dossier = self._dossier_soumis()
        self.assertEqual(dossier.statut, DossierKYC.Statut.EN_ATTENTE)
        self.assertEqual(dossier.documents.filter(est_actif=True).count(), 2)
        self.assertEqual(dossier.historique.filter(action="SOUMISSION").count(), 1)
        self.assertTrue(
            Notification.objects.filter(
                destinataire=self.client_bancaire, type_notification=Notification.Type.KYC
            ).exists()
        )
        self.assertTrue(SmsSimule.objects.filter(destinataire=self.client_bancaire).exists())

        self.client.force_login(self.admin)
        reponse = self.client.post(
            reverse("administration:kyc_decider", args=[dossier.pk]),
            {"decision": "APPROUVER", "commentaire": "Pièces conformes."},
        )
        self.assertRedirects(reponse, reverse("administration:kyc_dossiers"), fetch_redirect_response=False)
        dossier.refresh_from_db()
        self.client_bancaire.refresh_from_db()
        self.assertEqual(dossier.statut, DossierKYC.Statut.APPROUVE)
        self.assertEqual(dossier.traite_par, self.admin)
        self.assertIsNotNone(dossier.date_traitement)
        self.assertTrue(self.client_bancaire.identite_verifiee)
        self.assertFalse(dossier.documents.filter(statut=DocumentKYC.Statut.EN_ATTENTE).exists())
        self.assertTrue(dossier.historique.filter(action="APPROBATION").exists())
        self.assertTrue(
            JournalAudit.objects.filter(action="KYC — dossier approuvé").exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                destinataire=self.client_bancaire,
                type_notification=Notification.Type.KYC,
                niveau=Notification.Niveau.SUCCES,
            ).exists()
        )

    def test_liste_kyc_propose_acceptation_et_rejet_directs(self):
        dossier = self._dossier_soumis()
        self.client.force_login(self.admin)
        reponse = self.client.get(
            reverse("administration:kyc_dossiers"), {"statut": "EN_ATTENTE"}
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Accepter")
        self.assertContains(reponse, "Rejeter")
        self.assertContains(
            reponse,
            reverse("administration:kyc_decider", args=[dossier.pk]),
        )

        reponse = self.client.post(
            reverse("administration:kyc_decider", args=[dossier.pk]),
            {"decision": "APPROUVER"},
        )
        self.assertRedirects(
            reponse,
            reverse("administration:kyc_dossiers"),
            fetch_redirect_response=False,
        )
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, DossierKYC.Statut.APPROUVE)

    def test_rejet_exige_un_motif_et_met_a_jour_le_client(self):
        dossier = self._dossier_soumis()
        self.client.force_login(self.agent)

        reponse = self.client.post(
            reverse("administration:kyc_decider", args=[dossier.pk]),
            {"decision": "REJETER", "commentaire": ""},
        )
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, DossierKYC.Statut.EN_ATTENTE)

        reponse = self.client.post(
            reverse("administration:kyc_decider", args=[dossier.pk]),
            {"decision": "REJETER", "commentaire": "La pièce est illisible."},
        )
        self.assertRedirects(reponse, reverse("administration:kyc_dossiers"), fetch_redirect_response=False)
        dossier.refresh_from_db()
        self.client_bancaire.refresh_from_db()
        self.assertEqual(dossier.statut, DossierKYC.Statut.REJETE)
        self.assertEqual(dossier.motif_rejet, "La pièce est illisible.")
        self.assertFalse(self.client_bancaire.identite_verifiee)
        self.assertTrue(
            dossier.documents.filter(statut=DocumentKYC.Statut.REJETE).exists()
        )
        self.assertTrue(dossier.historique.filter(action="REJET").exists())

    def test_approbation_refusee_sans_pieces(self):
        dossier = self._dossier()
        dossier.statut = DossierKYC.Statut.EN_ATTENTE
        dossier.save(update_fields=["statut"])
        self.client.force_login(self.admin)
        self.client.post(
            reverse("administration:kyc_decider", args=[dossier.pk]),
            {"decision": "APPROUVER"},
        )
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, DossierKYC.Statut.EN_ATTENTE)
        self.assertFalse(self.client_bancaire.identite_verifiee)

    def test_document_prive_non_expose_et_telechargement_controle(self):
        self._renseigner_informations()
        self._ajouter_document(DocumentKYC.TypeDocument.CNI_RECTO, "recto.pdf")
        document = DocumentKYC.objects.get(dossier=self._dossier())

        reponse = self.client.get(f"/media/kyc/{document.fichier.name}")
        self.assertEqual(reponse.status_code, 404)

        reponse = self.client.get(reverse("kyc:telecharger_document", args=[document.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "application/pdf")

        self.client.force_login(self.autre_client)
        reponse = self.client.get(reverse("kyc:telecharger_document", args=[document.pk]))
        self.assertEqual(reponse.status_code, 404)

        self.client.force_login(self.admin)
        reponse = self.client.get(reverse("kyc:telecharger_document", args=[document.pk]))
        self.assertEqual(reponse.status_code, 200)

    def test_upload_format_interdit_refuse(self):
        self._renseigner_informations()
        reponse = self.client.post(
            reverse("kyc:ajouter_document"),
            {
                "type_document": DocumentKYC.TypeDocument.CNI_RECTO,
                "fichier": SimpleUploadedFile("malware.exe", b"test", content_type="application/octet-stream"),
            },
            follow=True,
        )
        self.assertEqual(DocumentKYC.objects.filter(dossier=self._dossier()).count(), 0)
        self.assertContains(reponse, "Format non autorisé")

    def test_dossier_approuve_verrouille_les_modifications(self):
        dossier = self._dossier()
        dossier.statut = DossierKYC.Statut.APPROUVE
        dossier.save(update_fields=["statut"])
        self._ajouter_document(DocumentKYC.TypeDocument.CNI_RECTO)
        self.assertEqual(DocumentKYC.objects.filter(dossier=dossier).count(), 0)

    def test_client_ne_peut_pas_ouvrir_le_back_office_kyc(self):
        dossier = self._dossier()
        reponse = self.client.get(reverse("administration:kyc_detail", args=[dossier.pk]))
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse["Location"].startswith("/comptes/"))
