"""
Recette de fumée des routes principales sur la base MySQL réelle.

Ce script ne modifie aucune donnée métier : il se contente d'émettre des
requêtes GET en lecture. Il est prévu pour être exécuté via :

    .\venv\Scripts\python.exe manage.py shell \
        -c "exec(open('scripts/recette_routes.py', encoding='utf-8').read())"

Les résultats sont affichés route par route. Un code 500 est toujours
considéré comme un échec ; les autres codes attendus sont déclarés par rôle.
"""

from django.test import Client

from comptes.models import Utilisateur

client = Client(SERVER_NAME="localhost")
client.raise_request_exception = False

routes_publiques = [
    ("/", {302}),
    ("/comptes/connexion/", {200}),
    ("/comptes/inscription/", {200}),
    ("/comptes/mot-de-passe/oublie/", {200}),
    ("/support/faq/", {200}),
    ("/admin/", {302}),
    ("/media/kyc/identite.pdf", {404}),
]

routes_client = [
    ("/", {200}),
    ("/comptes/", {200}),
    ("/comptes/profil/", {200}),
    ("/comptes/beneficiaires/", {200}),
    ("/comptes/notifications/", {200}),
    ("/comptes/notifications/non-lues.json", {200}),
    ("/comptes/parametres/", {200}),
    ("/comptes/parametres/2fa/codes-secours/", {200, 302}),
    ("/comptes/parametres/sessions/", {200}),
    ("/comptes/ussd/", {200}),
    ("/operations/comptes/", {200}),
    ("/operations/comptes/ouvrir/", {200, 302}),
    ("/operations/comptes/1/", {200}),
    ("/operations/comptes/1/releve.pdf", {200}),
    ("/operations/depots-retraits/", {200}),
    ("/operations/releves/", {200}),
    ("/operations/historique/", {200}),
    ("/operations/transactions/2/recu.pdf", {200}),
    ("/operations/virements-programmes/", {200}),
    ("/operations/virement/", {200}),
    ("/operations/deposer/", {200}),
    ("/operations/retirer/", {200}),
    ("/paiement-mobile/recharger/", {200}),
    ("/paiement-mobile/retrait/", {200}),
    ("/paiement-mobile/paiement-marchand/", {200}),
    ("/paiement-mobile/historique/", {200}),
    ("/kyc/", {200}),
    ("/kyc/informations/", {405}),
    ("/kyc/documents/ajouter/", {405}),
    ("/cartes/", {200}),
    ("/cartes/demander/", {200, 302}),
    ("/epargne/", {200}),
    ("/epargne/objectifs/nouveau/", {200, 302}),
    ("/epargne/arrondis/", {200, 302}),
    ("/epargne/budgets/", {200}),
    ("/factures/", {200}),
    ("/factures/ajouter/", {200}),
    ("/devises/", {200}),
    ("/devises/conversions/", {200}),
    ("/reseau/agents/", {200}),
    ("/reseau/marchands/", {200}),
    ("/reseau/marchand/", {200}),
    ("/reseau/marchand/inscription/", {200, 302}),
    ("/reseau/agent/", {302, 403}),
    ("/support/faq/", {200}),
    ("/support/tickets/", {200}),
    ("/support/tickets/nouveau/", {200}),
    ("/risques/alertes/", {302, 403}),
    ("/administration/", {302, 403}),
    ("/administration/rapports/", {302, 403}),
    ("/admin/", {302, 403}),
]

routes_agent = [
    ("/", {200, 302}),
    ("/reseau/agent/", {200, 302}),
    ("/operations/deposer/", {200}),
    ("/operations/retirer/", {200}),
    ("/administration/", {200}),
    ("/administration/rapports/", {200}),
    ("/administration/kyc/", {200}),
    ("/administration/clients/", {200}),
    ("/administration/transactions/", {200}),
    ("/administration/journal-audit/", {302, 403}),
    ("/administration/utilisateurs-internes/", {302, 403}),
    ("/risques/alertes/", {302, 403}),
    ("/admin/", {302, 403}),
    ("/support/tickets/", {200}),
]

routes_admin = [
    ("/", {200, 302}),
    ("/administration/", {200}),
    ("/administration/clients/", {200}),
    ("/administration/clients/1/", {200}),
    ("/administration/transactions/", {200}),
    ("/administration/transactions/export.csv", {200}),
    ("/administration/rapports/", {200}),
    ("/administration/rapports/export.csv", {200}),
    ("/administration/journal-audit/", {200}),
    ("/administration/journal-audit/export.csv", {200}),
    ("/administration/utilisateurs-internes/", {200}),
    ("/administration/kyc/", {200}),
    ("/administration/kyc/1/", {200}),
    ("/risques/alertes/", {200}),
    ("/risques/alertes/non-lues.json", {200}),
    ("/reseau/agent/", {200, 302}),
    ("/reseau/marchand/", {200}),
    ("/support/tickets/", {200}),
    ("/admin/", {200}),
    ("/admin/comptes/utilisateur/", {200}),
    ("/admin/risques/alertefraude/", {200}),
    ("/admin/kyc/dossierkyc/", {200}),
    ("/admin/transactions/transaction/", {200}),
    ("/admin/reseau/profilagent/", {200}),
]

resultats = []


def verifier(role, routes):
    utilisateur = Utilisateur.objects.get(username=role)
    client.force_login(utilisateur)
    for url, attendus in routes:
        reponse = client.get(url)
        ok = reponse.status_code in attendus
        resultats.append((ok, role, url, reponse.status_code, sorted(attendus)))
        if not ok:
            print(f"ECHEC [{role}] GET {url} -> {reponse.status_code} (attendu {sorted(attendus)})")


# Routes publiques : aucun utilisateur connecté.
for url, attendus in routes_publiques:
    reponse = client.get(url)
    ok = reponse.status_code in attendus
    resultats.append((ok, "public", url, reponse.status_code, sorted(attendus)))
    if not ok:
        print(f"ECHEC [public] GET {url} -> {reponse.status_code} (attendu {sorted(attendus)})")

verifier("Mbaye", routes_client)
verifier("Lari", routes_agent)
verifier("admin", routes_admin)

echecs = [r for r in resultats if not r[0]]
print("\n===== BILAN RECETTE ROUTES =====")
print(f"Routes testées : {len(resultats)}")
print(f"Succès         : {len(resultats) - len(echecs)}")
print(f"Échecs         : {len(echecs)}")
if echecs:
    for _, role, url, code, attendus in echecs:
        print(f" - [{role}] {url} : {code} au lieu de {attendus}")
    raise SystemExit(1)
print("Toutes les routes répondent avec le code attendu.")
