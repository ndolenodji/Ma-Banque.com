"""Tests du parcours mobile money (service simulé, soldes réels en base)."""

import io
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from comptes.models import Notification, Utilisateur
from transactions.models import CompteBancaire, RegleFrais, Transaction

from . import services
from .models import PaiementMobile


class PaiementMobileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.utilisateur = Utilisateur.objects.create_user(
            username="client_mobile",
            password="MotDePasse!2026",
            telephone="+23566000001",
            first_name="Mariam",
            last_name="Abakar",
        )
        cls.compte = CompteBancaire.objects.create(
            proprietaire=cls.utilisateur,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            solde=Decimal("10000.00"),
        )
        cls.autre = Utilisateur.objects.create_user(
            username="autre_mobile",
            password="MotDePasse!2026",
            telephone="+23566000002",
        )
        cls.compte_autre = CompteBancaire.objects.create(
            proprietaire=cls.autre,
            type_compte=CompteBancaire.TypeCompte.COURANT,
            solde=Decimal("50000.00"),
        )
        RegleFrais.objects.create(
            type_operation=RegleFrais.Operation.PAIEMENT_MOBILE,
            libelle="Frais retrait mobile de test",
            frais_fixe=Decimal("100.00"),
        )

    def setUp(self):
        self.client.force_login(self.utilisateur)
        self.donnees = {
            "compte_bancaire": self.compte.pk,
            "operateur": PaiementMobile.Operateur.AIRTEL,
            "numero_telephone": "66000001",
            "montant": "5000.00",
            "cle_idempotence": str(uuid.uuid4()),
        }

    def _creer_paiement(self, statut, montant, operateur=PaiementMobile.Operateur.MOOV, frais=0):
        return PaiementMobile.objects.create(
            client=self.utilisateur,
            compte_bancaire=self.compte,
            reference_transaction=f"TXN-TEST-{uuid.uuid4().hex[:10].upper()}",
            type_operation=PaiementMobile.TypeOperation.RECHARGE,
            operateur=operateur,
            numero_telephone="66000001",
            montant=Decimal(montant),
            frais=Decimal(frais),
            statut=statut,
        )

    def test_pages_accessibles_et_libelle_simulation(self):
        for nom in ("recharger", "retrait_mobile", "historique"):
            reponse = self.client.get(reverse(f"paiement_mobile:{nom}"))
            self.assertEqual(reponse.status_code, 200, nom)
            self.assertContains(reponse, "simul")

    def test_formulaires_mobile_proposent_le_numero_de_compte(self):
        for nom in ("recharger", "retrait_mobile"):
            reponse = self.client.get(reverse(f"paiement_mobile:{nom}"))
            self.assertContains(reponse, f'<option value="{self.compte.numero_compte}">')
            self.assertContains(reponse, 'list="liste-id_compte_bancaire"')

    def test_recharge_accepte_le_numero_de_compte_saisi(self):
        donnees = dict(self.donnees)
        donnees["compte_bancaire"] = self.compte.numero_compte
        with patch(
            "paiement_mobile.services.client_api.initier_demande_paiement",
            return_value={"id_transaction": "SIM-NUMERO-1", "statut": "REUSSI", "message": ""},
        ):
            reponse = self.client.post(reverse("paiement_mobile:recharger"), donnees)
        paiement = PaiementMobile.objects.get(client=self.utilisateur)
        self.assertRedirects(
            reponse,
            reverse("paiement_mobile:statut", args=[paiement.pk]),
            fetch_redirect_response=False,
        )
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("15000.00"))

    def test_qr_personnel_reel_et_limite_au_client(self):
        reponse = self.client.get(reverse("paiement_mobile:mon_code_qr"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "data:image/png;base64,")
        self.assertContains(reponse, self.compte.numero_compte)

        # Un identifiant d'un autre client ne permet pas d'afficher son compte.
        reponse = self.client.get(
            reverse("paiement_mobile:mon_code_qr"), {"compte": self.compte_autre.pk}
        )
        self.assertContains(reponse, self.compte.numero_compte)
        self.assertNotContains(reponse, self.compte_autre.numero_compte)

        telechargement = self.client.get(reverse("paiement_mobile:telecharger_mon_qr"))
        self.assertEqual(telechargement.status_code, 200)
        self.assertEqual(telechargement["Content-Type"], "image/png")
        self.assertTrue(telechargement.content.startswith(b"\x89PNG\r\n\x1a\n"))
        image = Image.open(io.BytesIO(telechargement.content))
        image.verify()
        self.assertGreaterEqual(image.size[0], 200)

    def test_qr_marchand_genere_une_image_reelle(self):
        reponse = self.client.post(
            reverse("paiement_mobile:paiement_marchand"),
            {"compte_bancaire": self.compte.pk, "montant": "2500.00"},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "data:image/png;base64,")
        self.assertContains(reponse, "QR-DEMO-")
        self.assertContains(reponse, "2500,00")
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("10000.00"))

    def test_recharge_reussie_credite_le_compte(self):
        with patch(
            "paiement_mobile.services.client_api.initier_demande_paiement",
            return_value={
                "id_transaction": "SIM-RECHARGE-1",
                "statut": "REUSSI",
                "message": "",
            },
        ) as appel:
            reponse = self.client.post(reverse("paiement_mobile:recharger"), self.donnees)

        paiement = PaiementMobile.objects.get(client=self.utilisateur)
        self.assertRedirects(
            reponse,
            reverse("paiement_mobile:statut", args=[paiement.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(paiement.statut, PaiementMobile.Statut.REUSSI)
        self.assertEqual(paiement.type_operation, PaiementMobile.TypeOperation.RECHARGE)
        self.assertEqual(paiement.reference_externe, "SIM-RECHARGE-1")
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("15000.00"))
        self.assertTrue(
            Transaction.objects.filter(
                compte_destination=self.compte,
                montant=Decimal("5000.00"),
                statut=Transaction.Statut.REUSSIE,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(destinataire=self.utilisateur).exists()
        )
        appel.assert_called_once()

    def test_recharge_en_attente_confirmee_une_seule_fois(self):
        with patch(
            "paiement_mobile.services.client_api.initier_demande_paiement",
            return_value={"id_transaction": "SIM-ATTENTE-1", "statut": "EN_ATTENTE"},
        ):
            self.client.post(reverse("paiement_mobile:recharger"), self.donnees)

        paiement = PaiementMobile.objects.get(client=self.utilisateur)
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("10000.00"))

        with patch(
            "paiement_mobile.services.client_api.consulter_statut",
            return_value={"statut": "REUSSI", "message": "OK"},
        ):
            self.client.get(reverse("paiement_mobile:statut", args=[paiement.pk]))
            self.client.get(reverse("paiement_mobile:statut", args=[paiement.pk]))

        paiement.refresh_from_db()
        self.compte.refresh_from_db()
        self.assertEqual(paiement.statut, PaiementMobile.Statut.REUSSI)
        self.assertEqual(self.compte.solde, Decimal("15000.00"))
        self.assertEqual(
            Transaction.objects.filter(
                compte_destination=self.compte,
                type_transaction=Transaction.Type.PAIEMENT_MOBILE,
            ).count(),
            1,
        )

    def test_retrait_reussi_debite_montant_et_frais(self):
        with patch(
            "paiement_mobile.services.client_api.initier_demande_paiement",
            return_value={"id_transaction": "SIM-RETRAIT-1", "statut": "REUSSI"},
        ):
            reponse = self.client.post(reverse("paiement_mobile:retrait_mobile"), self.donnees)

        paiement = PaiementMobile.objects.get(client=self.utilisateur)
        self.assertRedirects(
            reponse,
            reverse("paiement_mobile:statut", args=[paiement.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(paiement.statut, PaiementMobile.Statut.REUSSI)
        self.assertEqual(paiement.frais, Decimal("100.00"))
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("4900.00"))
        debit = Transaction.objects.get(compte_source=self.compte)
        self.assertEqual(debit.statut, Transaction.Statut.REUSSIE)
        self.assertEqual(debit.montant, Decimal("5000.00"))
        self.assertEqual(debit.frais, Decimal("100.00"))

    def test_retrait_echoue_rembourse_integralement(self):
        with patch(
            "paiement_mobile.services.client_api.initier_demande_paiement",
            return_value={
                "id_transaction": "SIM-RETRAIT-ECHEC",
                "statut": "ECHOUE",
                "message": "Solde opérateur insuffisant",
            },
        ):
            self.client.post(reverse("paiement_mobile:retrait_mobile"), self.donnees)

        paiement = PaiementMobile.objects.get(client=self.utilisateur)
        self.assertEqual(paiement.statut, PaiementMobile.Statut.ECHOUE)
        self.assertIsNotNone(paiement.transaction_remboursement_id)
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("10000.00"))
        remboursement = paiement.transaction_remboursement
        self.assertEqual(remboursement.montant, Decimal("5100.00"))
        self.assertEqual(remboursement.type_transaction, Transaction.Type.REMBOURSEMENT)
        self.assertEqual(
            Transaction.objects.get(pk=paiement.transaction_bancaire_id).statut,
            Transaction.Statut.REMBOURSEE,
        )

    def test_numero_invalide_affiche_une_erreur_sans_500(self):
        donnees = dict(self.donnees, numero_telephone="abc")
        with patch(
            "paiement_mobile.services.client_api.initier_demande_paiement"
        ) as appel:
            reponse = self.client.post(reverse("paiement_mobile:recharger"), donnees)

        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Numéro mobile invalide")
        self.assertFalse(PaiementMobile.objects.exists())
        appel.assert_not_called()

    def test_cle_idempotence_identique_ne_debite_quune_fois(self):
        with patch(
            "paiement_mobile.services.client_api.initier_demande_paiement",
            return_value={"id_transaction": "SIM-DOUBLON", "statut": "EN_ATTENTE"},
        ) as appel:
            premier = services.initier_retrait(
                self.utilisateur,
                self.compte,
                Decimal("3000"),
                PaiementMobile.Operateur.MOOV,
                "66000001",
                cle_idempotence=self.donnees["cle_idempotence"],
            )
            second = services.initier_retrait(
                self.utilisateur,
                self.compte,
                Decimal("3000"),
                PaiementMobile.Operateur.MOOV,
                "66000001",
                cle_idempotence=self.donnees["cle_idempotence"],
            )

        self.assertEqual(premier.pk, second.pk)
        self.assertEqual(appel.call_count, 1)
        self.compte.refresh_from_db()
        self.assertEqual(self.compte.solde, Decimal("6900.00"))

    def test_cle_idempotence_dun_autre_client_refusee(self):
        paiement = self._creer_paiement(PaiementMobile.Statut.REUSSI, "1000")
        PaiementMobile.objects.filter(pk=paiement.pk).update(cle_idempotence="cle-partagee")
        with self.assertRaises(ValidationError):
            services.initier_recharge(
                self.autre,
                self.compte_autre,
                Decimal("2000"),
                PaiementMobile.Operateur.AIRTEL,
                "66000002",
                cle_idempotence="cle-partagee",
            )

    def test_statut_dun_autre_client_est_inaccessible(self):
        paiement = PaiementMobile.objects.create(
            client=self.autre,
            compte_bancaire=self.compte_autre,
            reference_transaction="TXN-AUTRE",
            type_operation=PaiementMobile.TypeOperation.RECHARGE,
            operateur=PaiementMobile.Operateur.AIRTEL,
            numero_telephone="66000002",
            montant=Decimal("1000"),
        )
        reponse = self.client.get(
            reverse("paiement_mobile:statut", args=[paiement.pk])
        )
        self.assertEqual(reponse.status_code, 404)

    def test_historique_statistiques_et_filtres(self):
        self._creer_paiement(PaiementMobile.Statut.REUSSI, "4000", frais=200)
        self._creer_paiement(PaiementMobile.Statut.ECHOUE, "3000")
        self._creer_paiement(
            PaiementMobile.Statut.EN_ATTENTE,
            "2000",
            operateur=PaiementMobile.Operateur.AIRTEL,
        )

        reponse = self.client.get(reverse("paiement_mobile:historique"))
        self.assertEqual(reponse.status_code, 200)
        statistiques = reponse.context["statistiques"]
        self.assertEqual(statistiques["nb_total"], 3)
        self.assertEqual(statistiques["nb_reussi"], 1)
        self.assertEqual(statistiques["nb_attente"], 1)
        self.assertEqual(statistiques["nb_echec"], 1)
        self.assertEqual(statistiques["volume_reussi"], Decimal("4000.00"))
        self.assertEqual(statistiques["frais_reussi"], Decimal("200.00"))

        reponse = self.client.get(
            reverse("paiement_mobile:historique"),
            {"statut": PaiementMobile.Statut.ECHOUE},
        )
        self.assertEqual(len(reponse.context["paiements"]), 1)
        self.assertEqual(
            reponse.context["paiements"][0].statut, PaiementMobile.Statut.ECHOUE
        )

        reponse = self.client.get(
            reverse("paiement_mobile:historique"),
            {"operateur": PaiementMobile.Operateur.AIRTEL},
        )
        self.assertEqual(len(reponse.context["paiements"]), 1)
        self.assertEqual(
            reponse.context["paiements"][0].operateur, PaiementMobile.Operateur.AIRTEL
        )
