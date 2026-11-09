"""
Client HTTP permettant à l'application Django d'appeler le microservice
FastAPI dédié au paiement mobile (paiement_mobile_api/).

Principes de sûreté :
- Django reste la seule source de vérité pour les soldes bancaires ;
- les montants circulent sous forme de chaînes décimales, jamais de floats ;
- chaque appel porte un secret interne partagé (le microservice refuse les
  appels anonymes) ;
- la référence de transaction sert de clé d'idempotence : un nouvel appel
  avec la même référence ne crée pas de doublon chez l'agrégateur ;
- aucune clé d'API réelle n'est utilisée : le service est une SIMULATION
  explicitement identifiée. Une intégration réelle exige les identifiants,
  signatures et URL fournis contractuellement par l'opérateur.
"""

import uuid
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings


class ErreurServicePaiementMobile(Exception):
    """Levée si le microservice FastAPI est injoignable ou renvoie une erreur."""


def _url(chemin):
    return f"{settings.PAIEMENT_MOBILE_API_URL.rstrip('/')}{chemin}"


def _entetes():
    return {
        "Accept": "application/json",
        "X-Interne-Secret": settings.PAIEMENT_MOBILE_API_SECRET,
    }


def generer_reference():
    return f"TXN-{uuid.uuid4().hex[:12].upper()}"


def _detail_erreur(reponse):
    """Extrait un message lisible sans jamais exposer le corps brut complet."""
    try:
        donnees = reponse.json()
    except ValueError:
        return f"Réponse invalide du service (HTTP {reponse.status_code})."
    if isinstance(donnees, dict):
        detail = donnees.get("detail", f"HTTP {reponse.status_code}")
        if isinstance(detail, list):
            messages = []
            for element in detail:
                if isinstance(element, dict):
                    messages.append(str(element.get("msg", element)))
                else:
                    messages.append(str(element))
            return " ; ".join(messages)
        return str(detail)
    return f"HTTP {reponse.status_code}"


def _montant_decimal(montant):
    try:
        valeur = Decimal(str(montant))
    except (InvalidOperation, TypeError, ValueError) as erreur:
        raise ErreurServicePaiementMobile("Montant invalide.") from erreur
    if valeur <= 0:
        raise ErreurServicePaiementMobile("Le montant doit être strictement positif.")
    return format(valeur.quantize(Decimal("0.01")), "f")


def initier_demande_paiement(reference, montant, numero_telephone, operateur, type_operation="DEMANDE"):
    """
    Appelle POST /api/paiement/demande.

    `type_operation` distingue DEMANDE, RECHARGE et RETRAIT_MOBILE. Le
    microservice applique la même référence idempotente quel que soit le
    nombre de tentatives réseau.
    """
    try:
        reponse = requests.post(
            _url("/api/paiement/demande"),
            json={
                "reference_transaction": reference,
                "montant": _montant_decimal(montant),
                "numero_telephone": numero_telephone,
                "operateur": operateur,
                "type_operation": type_operation,
            },
            headers=_entetes(),
            timeout=settings.PAIEMENT_MOBILE_API_TIMEOUT,
        )
    except requests.RequestException as erreur:
        raise ErreurServicePaiementMobile(f"Service de paiement mobile indisponible : {erreur}") from erreur

    if reponse.status_code not in (200, 201):
        raise ErreurServicePaiementMobile(_detail_erreur(reponse))
    try:
        donnees = reponse.json()
    except ValueError as erreur:
        raise ErreurServicePaiementMobile("Réponse illisible du service de paiement mobile.") from erreur
    if not isinstance(donnees, dict) or not donnees.get("id_transaction"):
        raise ErreurServicePaiementMobile("Réponse incomplète du service de paiement mobile.")
    return donnees


def consulter_statut(id_transaction):
    """Appelle GET /api/paiement/statut/{id_transaction}."""
    try:
        reponse = requests.get(
            _url(f"/api/paiement/statut/{id_transaction}"),
            headers=_entetes(),
            timeout=settings.PAIEMENT_MOBILE_API_TIMEOUT,
        )
    except requests.RequestException as erreur:
        raise ErreurServicePaiementMobile(f"Service de paiement mobile indisponible : {erreur}") from erreur

    if reponse.status_code == 404:
        raise ErreurServicePaiementMobile("Transaction inconnue du service de paiement mobile.")
    if reponse.status_code != 200:
        raise ErreurServicePaiementMobile(_detail_erreur(reponse))
    try:
        return reponse.json()
    except ValueError as erreur:
        raise ErreurServicePaiementMobile("Réponse illisible du service de paiement mobile.") from erreur


def verifier_sante():
    """Test de disponibilité non bloquant, utilisé par les tableaux de bord."""
    try:
        reponse = requests.get(
            _url("/"),
            headers=_entetes(),
            timeout=min(settings.PAIEMENT_MOBILE_API_TIMEOUT, 3),
        )
        return reponse.status_code == 200
    except requests.RequestException:
        return False
