"""
Adaptateurs d'opérateurs mobile money.

Les quatre opérateurs du cahier des charges disposent d'un adaptateur, mais
seuls Airtel Money et Moov Money sont identifiés comme opérateurs mobiles
actifs au Tchad. MTN et Orange restent disponibles pour la démonstration
historique et sont signalés comme non disponibles localement.

Aucun adaptateur ne contacte un opérateur réel : ils délèguent au
microservice FastAPI de simulation, lui-même explicitement marqué comme tel.
Les vraies intégrations exigent un contrat, des identifiants API et des URL
de production fournis par l'opérateur.
"""

from dataclasses import dataclass

from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class OperateurMobile:
    code: str
    libelle: str
    disponible_tchad: bool
    note: str


OPERATEURS = {
    "AIRTEL": OperateurMobile(
        "AIRTEL",
        "Airtel Money",
        True,
        "Opérateur mobile actif au Tchad. Intégration réelle soumise au portail développeur Airtel et à un contrat marchand.",
    ),
    "MOOV": OperateurMobile(
        "MOOV",
        "Moov Money",
        True,
        "Opérateur mobile actif au Tchad. Intégration réelle soumise à un accord commercial Moov Africa.",
    ),
    "MTN": OperateurMobile(
        "MTN",
        "MTN Mobile Money",
        False,
        "Adaptateur conservé pour la démonstration ; MTN n'est pas identifié comme opérateur mobile au Tchad.",
    ),
    "ORANGE": OperateurMobile(
        "ORANGE",
        "Orange Money",
        False,
        "Adaptateur conservé pour la démonstration ; Orange n'est pas identifié comme opérateur mobile au Tchad.",
    ),
}


def get_operateur(code):
    try:
        return OPERATEURS[code]
    except KeyError as erreur:
        raise ValidationError("Opérateur mobile money inconnu.") from erreur


def valider_numero(numero, operateur):
    numero = (numero or "").replace(" ", "").replace("-", "")
    if not numero.isdigit() or not 8 <= len(numero) <= 12:
        raise ValidationError("Numéro mobile invalide : 8 à 12 chiffres attendus.")
    if not get_operateur(operateur):
        raise ValidationError("Opérateur mobile money inconnu.")
    return numero


def operateurs_pour_formulaire():
    """Tous les opérateurs, avec un libellé indiquant la disponibilité locale."""
    return [
        (
            code,
            f"{info.libelle} — disponible au Tchad" if info.disponible_tchad else f"{info.libelle} — démonstration",
        )
        for code, info in OPERATEURS.items()
    ]
