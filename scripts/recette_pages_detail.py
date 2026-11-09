"""
Recette des pages de détail qui exigent des données existantes.

Le script crée des objets de démonstration dans une transaction MySQL, teste
les pages concernées, puis annule intégralement la transaction : aucune
donnée réelle n'est conservée. Exécution :

    .\venv\Scripts\python.exe manage.py shell \
        -c "exec(open('scripts/recette_pages_detail.py', encoding='utf-8').read())"

Les soldes existants ne sont jamais modifiés (aucun POST monétaire).
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import Client
from django.utils import timezone

from cartes.models import CarteBancaire
from comptes.models import Utilisateur
from epargne.models import Budget, ObjectifEpargne
from factures.models import Facture, FournisseurFacture
from paiement_mobile.models import PaiementMobile
from reseau.models import DemandePaiementMarchand, Marchand, ProfilAgent
from risques.models import AlerteFraude
from support.models import MessageSupport, TicketSupport
from transactions.models import CompteBancaire

mbaye = Utilisateur.objects.get(username="Mbaye")
alex = Utilisateur.objects.get(username="Alex")
lari = Utilisateur.objects.get(username="Lari")
admin = Utilisateur.objects.get(username="admin")
compte_courant = CompteBancaire.objects.get(pk=1, proprietaire=mbaye)

resultats = []


def attendre(role, url, codes, client, *, content_type=None, contenu=None):
    reponse = client.get(url)
    ok = reponse.status_code in codes
    if content_type is not None:
        ok = ok and reponse.headers.get("Content-Type", "").startswith(content_type)
    if contenu is not None:
        ok = ok and contenu in reponse.content
    resultats.append((ok, role, url, reponse.status_code))
    if not ok:
        print(
            f"ECHEC [{role}] GET {url} -> {reponse.status_code} "
            f"(attendu {sorted(codes)}, type {content_type})"
        )
    return reponse


with transaction.atomic():
    compte_epargne = CompteBancaire.objects.create(
        proprietaire=mbaye,
        type_compte=CompteBancaire.TypeCompte.EPARGNE,
        solde=Decimal("0"),
    )
    objectif = ObjectifEpargne.objects.create(
        proprietaire=mbaye,
        nom="Recette — objectif de test",
        montant_cible=Decimal("100000"),
        montant_actuel=Decimal("15000"),
        compte_epargne=compte_epargne,
        date_cible=timezone.localdate() + timedelta(days=90),
    )
    budget = Budget.objects.create(
        proprietaire=mbaye,
        categorie="ALIMENTATION",
        plafond_mensuel=Decimal("50000"),
    )
    carte = CarteBancaire.objects.create(
        utilisateur=mbaye,
        compte=compte_courant,
        type_carte=CarteBancaire.TypeCarte.VIRTUELLE,
        nom_titulaire="Mbaye Recette",
        date_expiration=timezone.localdate() + timedelta(days=730),
        statut=CarteBancaire.Statut.ACTIVE,
    )
    fournisseur = FournisseurFacture.objects.filter(actif=True).first()
    facture = Facture.objects.create(
        proprietaire=mbaye,
        fournisseur=fournisseur,
        reference="FAC-RECETTE-DETAIL",
        libelle="Facture de recette",
        montant=Decimal("5000"),
        date_echeance=timezone.localdate() + timedelta(days=10),
    )
    ticket = TicketSupport.objects.create(
        client=mbaye,
        sujet="Ticket de recette",
        categorie=TicketSupport.Categorie.AUTRE,
    )
    MessageSupport.objects.create(ticket=ticket, auteur=mbaye, message="Message de recette.")
    profil_agent = ProfilAgent.objects.create(
        utilisateur=lari,
        nom_agence="Agence de recette",
        ville="N'Djamena",
        float_caisse=Decimal("500000"),
        actif=True,
    )
    marchand = Marchand.objects.create(
        proprietaire=mbaye,
        nom="Marchand de recette",
        compte_reglement=compte_courant,
        categorie="Commerce",
        ville="N'Djamena",
        statut=Marchand.Statut.ACTIF,
    )
    demande = DemandePaiementMarchand.objects.create(
        marchand=marchand,
        montant=Decimal("2500"),
        description="Demande de recette",
        date_expiration=timezone.now() + timedelta(hours=1),
    )
    paiement = PaiementMobile.objects.create(
        client=mbaye,
        compte_bancaire=compte_courant,
        reference_transaction="TXN-RECETTE-DETAIL-001",
        type_operation=PaiementMobile.TypeOperation.RECHARGE,
        operateur=PaiementMobile.Operateur.MTN,
        numero_telephone="+23566000000",
        montant=Decimal("1000"),
        statut=PaiementMobile.Statut.EN_ATTENTE,
    )
    alerte = AlerteFraude.objects.create(
        utilisateur=mbaye,
        transaction_id=2,
        score=60,
        niveau=AlerteFraude.Niveau.ELEVE,
        regles=[{"regle": "Montant élevé", "points": 25}],
        explication="Alerte de recette : vérification du parcours de revue.",
    )

    client = Client(SERVER_NAME="localhost")

    client.force_login(mbaye)
    attendre("Mbaye", f"/cartes/{carte.pk}/", {200}, client)
    attendre("Mbaye", f"/epargne/objectifs/{objectif.pk}/", {200}, client)
    attendre("Mbaye", f"/epargne/objectifs/{objectif.pk}/modifier/", {200}, client)
    attendre("Mbaye", f"/epargne/budgets/{budget.pk}/modifier/", {200}, client)
    attendre("Mbaye", f"/factures/{facture.pk}/", {200}, client)
    attendre("Mbaye", f"/support/tickets/{ticket.pk}/", {200}, client)
    attendre("Mbaye", f"/paiement-mobile/statut/{paiement.pk}/", {200}, client)
    attendre("Mbaye", "/reseau/marchand/", {200}, client)
    attendre("Mbaye", "/reseau/marchand/demandes/creer/", {200}, client)
    attendre("Mbaye", f"/reseau/marchand/demandes/{demande.pk}/", {200}, client)
    qr = attendre("Mbaye", f"/reseau/marchand/demandes/{demande.pk}/qr.png", {200}, client, content_type="image/png")
    resultats.append((qr.content.startswith(b"\x89PNG"), "Mbaye", "signature PNG du QR", qr.status_code))
    attendre("Mbaye", "/epargne/objectifs/nouveau/", {200}, client)
    attendre("Mbaye", "/epargne/arrondis/", {200}, client)

    client.force_login(alex)
    attendre("Alex", f"/reseau/payer/{demande.jeton}/", {200}, client)
    attendre("Alex", f"/cartes/{carte.pk}/", {404}, client)
    attendre("Alex", f"/factures/{facture.pk}/", {404}, client)
    attendre("Alex", f"/support/tickets/{ticket.pk}/", {302}, client)

    client.force_login(lari)
    attendre("Lari", "/reseau/agent/", {200}, client)

    client.force_login(admin)
    attendre("admin", f"/risques/alertes/{alerte.pk}/", {200}, client)
    attendre("admin", f"/reseau/marchand/demandes/{demande.pk}/", {200}, client)
    attendre("admin", f"/support/tickets/{ticket.pk}/", {200}, client)
    attendre("admin", "/admin/transactions/transaction/", {200}, client)
    attendre("admin", "/admin/reseau/profilagent/", {200}, client)
    attendre("admin", "/admin/reseau/marchand/", {200}, client)
    attendre("admin", "/admin/reseau/demandepaiementmarchand/", {200}, client)
    attendre("admin", "/admin/risques/alertefraude/", {200}, client)

    transaction.set_rollback(True)

echecs = [r for r in resultats if not r[0]]
print("\n===== BILAN PAGES DE DETAIL =====")
print(f"Contrôles : {len(resultats)}")
print(f"Succès    : {len(resultats) - len(echecs)}")
print(f"Échecs    : {len(echecs)}")
if echecs:
    for _, role, url, code in echecs:
        print(f" - [{role}] {url} : code {code}")
    raise SystemExit(1)
print("Toutes les pages de détail répondent correctement (transaction annulée).")
