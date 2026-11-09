"""
Fichier obsolète — NE PAS UTILISER.

Ce module était une première ébauche du micro-service, dupliquée par erreur
sous banque_app/paiement_mobile_api/. Il ne contient aucune persistance,
accepte n'importe quel montant en flottant et déclare immédiatement les
paiements comme réussis : il ne doit jamais être exposé.

Le micro-service de simulation réellement utilisé par Django se trouve à
la racine du dépôt : paiement_mobile_api/main.py.

Ce fichier échoue volontairement au démarrage pour éviter qu'une commande
uvicorn lancée par erreur dans ce dossier ne fasse croire à un paiement
réussi.
"""

raise RuntimeError(
    "Module obsolète. Lancez le micro-service depuis paiement_mobile_api/ "
    "(racine du projet) et non depuis banque_app/paiement_mobile_api/."
)
