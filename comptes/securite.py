"""
Primitives de sécurité de l'application « Ma Banque » :

- TOTP (RFC 6238) implémenté avec la bibliothèque standard, sans dépendance
  externe supplémentaire ;
- chiffrement Fernet des secrets TOTP au repos (clé dédiée ou dérivée de
  SECRET_KEY, jamais stockée en clair dans la base) ;
- codes de secours à usage unique, stockés hachés ;
- libellé d'appareil lisible pour l'écran « sessions ».
"""

import base64
import hashlib
import hmac
import io
import re
import secrets
import struct
import time
from urllib.parse import quote

try:
    # Dépendance optionnelle : sans elle l'application démarre quand même,
    # seules les images QR sont alors indisponibles (aucune erreur bloquante).
    import qrcode
except ImportError:  # pragma: no cover - environnement Python incomplet
    qrcode = None

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from .models import CodeSecours


# ---------------------------------------------------------------------------
# Chiffrement des secrets TOTP
# ---------------------------------------------------------------------------
def _cle_fernet():
    valeur = (getattr(settings, "TOTP_ENCRYPTION_KEY", "") or "").strip()
    if valeur:
        cle = valeur.encode()
    else:
        empreinte = hashlib.sha256(f"{settings.SECRET_KEY}|totp-v1".encode()).digest()
        cle = base64.urlsafe_b64encode(empreinte)
    try:
        return Fernet(cle)
    except (ValueError, TypeError) as erreur:
        raise ImproperlyConfigured(
            "TOTP_ENCRYPTION_KEY invalide : fournissez une clé Fernet de 32 octets encodée en base64 URL."
        ) from erreur


def chiffrer_secret(secret):
    return _cle_fernet().encrypt(secret.encode()).decode()


def dechiffrer_secret(jeton):
    try:
        return _cle_fernet().decrypt(jeton.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# TOTP (RFC 6238)
# ---------------------------------------------------------------------------
def generer_secret_totp():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_pour(secret, compteur):
    try:
        cle = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    except Exception as erreur:  # noqa: BLE001 - secret corrompu
        raise ValueError("Secret TOTP invalide.") from erreur
    message = struct.pack(">Q", compteur)
    digest = hmac.new(cle, message, hashlib.sha1).digest()
    decalage = digest[-1] & 0x0F
    binaire = struct.unpack(">I", digest[decalage : decalage + 4])[0] & 0x7FFFFFFF
    chiffres = getattr(settings, "TOTP_CHIFFRES", 6)
    return str(binaire % (10**chiffres)).zfill(chiffres)


def code_totp_actuel(secret, horodatage=None):
    periode = getattr(settings, "TOTP_PERIODE", 30)
    moment = int(horodatage if horodatage is not None else time.time())
    return _code_pour(secret, moment // periode)


def verifier_code_totp(secret, code, fenetre=1):
    """Accepte une fenêtre de ±1 période (30 s) pour tolérer les décalages d'horloge."""
    code = re.sub(r"[\s-]", "", str(code or ""))
    if not code.isdigit() or len(code) != getattr(settings, "TOTP_CHIFFRES", 6):
        return False
    periode = getattr(settings, "TOTP_PERIODE", 30)
    compteur = int(time.time()) // periode
    for decalage in range(-fenetre, fenetre + 1):
        try:
            attendu = _code_pour(secret, compteur + decalage)
        except ValueError:
            return False
        if hmac.compare_digest(code, attendu):
            return True
    return False


def uri_provisionnement(utilisateur, secret):
    emetteur = quote(getattr(settings, "TOTP_ISSUER", "Ma Banque"))
    compte = quote(f"{getattr(settings, 'TOTP_ISSUER', 'Ma Banque')}:{utilisateur.username}")
    periode = getattr(settings, "TOTP_PERIODE", 30)
    chiffres = getattr(settings, "TOTP_CHIFFRES", 6)
    return (
        f"otpauth://totp/{compte}?secret={secret}&issuer={emetteur}"
        f"&algorithm=SHA1&digits={chiffres}&period={periode}"
    )


def image_qr_data_uri(contenu):
    if qrcode is None:
        return ""
    image = qrcode.make(contenu)
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return "data:image/png;base64," + base64.b64encode(tampon.getvalue()).decode()


# ---------------------------------------------------------------------------
# Codes de secours
# ---------------------------------------------------------------------------
def _hacher_code_secours(utilisateur, code):
    normalise = re.sub(r"[\s-]", "", code or "").upper()
    return hashlib.sha256(f"code-secours|{utilisateur.pk}|{normalise}".encode()).hexdigest()


def generer_codes_secours(utilisateur, nombre=10):
    """Remplace les anciens codes actifs et renvoie les codes en clair UNE SEULE FOIS."""
    codes = []
    objets = []
    for _ in range(nombre):
        brut = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        codes.append(brut)
        objets.append(CodeSecours(utilisateur=utilisateur, empreinte=_hacher_code_secours(utilisateur, brut)))
    with transaction.atomic():
        CodeSecours.objects.select_for_update().filter(utilisateur=utilisateur, utilise_le__isnull=True).delete()
        CodeSecours.objects.bulk_create(objets)
    return codes


def verifier_et_consommer_code_secours(utilisateur, code):
    empreinte = _hacher_code_secours(utilisateur, code)
    with transaction.atomic():
        candidats = list(
            CodeSecours.objects.select_for_update().filter(utilisateur=utilisateur, utilise_le__isnull=True)
        )
        for candidat in candidats:
            if hmac.compare_digest(candidat.empreinte, empreinte):
                candidat.utilise_le = timezone.now()
                candidat.save(update_fields=["utilise_le"])
                return True
    return False


def nombre_codes_secours_restants(utilisateur):
    return CodeSecours.objects.filter(utilisateur=utilisateur, utilise_le__isnull=True).count()


# ---------------------------------------------------------------------------
# Appareils
# ---------------------------------------------------------------------------
def libelle_appareil(user_agent):
    ua = (user_agent or "").lower()
    if not ua:
        return "Appareil inconnu"
    if "iphone" in ua or "android" in ua and "mobile" in ua:
        systeme = "iPhone" if "iphone" in ua else "Android"
        return f"Mobile {systeme}"
    if "ipad" in ua or "tablet" in ua:
        return "Tablette"
    if "windows" in ua:
        navigateur = "Edge" if "edg/" in ua else "Chrome" if "chrome" in ua else "Firefox" if "firefox" in ua else "Navigateur"
        return f"Ordinateur Windows — {navigateur}"
    if "mac os" in ua or "macintosh" in ua:
        return "Ordinateur Mac"
    if "linux" in ua:
        return "Ordinateur Linux"
    return "Navigateur web"
