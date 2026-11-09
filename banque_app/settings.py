"""
Configuration Django pour le projet Ma Banque (PFE).

Toutes les valeurs sensibles sont lues depuis le fichier .env
(voir .env.example à la racine du projet).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Chemins de base
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# Charge les variables d'environnement depuis le fichier .env
load_dotenv(BASE_DIR / ".env")


def env_bool(nom, defaut="False"):
    return os.getenv(nom, defaut).strip().lower() in ("1", "true", "yes", "oui")


# ---------------------------------------------------------------------------
# Sécurité
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-cle-de-developpement-a-remplacer-en-production",
)

DEBUG = env_bool("DJANGO_DEBUG", "True")

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# ---------------------------------------------------------------------------
# Applications installées
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Applications du projet (une app par domaine fonctionnel)
    "comptes",
    "devises",
    "transactions",
    "paiement_mobile",
    "kyc",
    "cartes",
    "epargne",
    "factures",
    "reseau",
    "support",
    "risques",
    "administration",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Middleware maison : journalise les actions sensibles (voir comptes/middleware.py)
    "comptes.middleware.JournalisationMiddleware",
    # Restreint le back-office Django natif aux administrateurs métier
    "comptes.middleware.RestrictionAdminMiddleware",
    # Met à jour l'activité des sessions / appareils connus
    "comptes.middleware.ActiviteSessionMiddleware",
]

ROOT_URLCONF = "banque_app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "comptes.context_processors.notifications_non_lues",
            ],
        },
    },
]

WSGI_APPLICATION = "banque_app.wsgi.application"

# ---------------------------------------------------------------------------
# Base de données
#
# Choix d'architecture : MySQL est la base "source de vérité" de l'app
# Django (comptes, soldes, transactions). Pour permettre de lancer le
# projet immédiatement sans serveur MySQL installé (contexte académique),
# le projet bascule automatiquement sur SQLite si DB_ENGINE=sqlite ou si
# aucune variable MySQL n'est fournie. Pour la soutenance / production,
# positionner DB_ENGINE=mysql dans le .env.
# ---------------------------------------------------------------------------
DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").strip().lower()

if DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.getenv("DB_NAME", "banque_db"),
            "USER": os.getenv("DB_USER", "root"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                # Mode SQL strict + moteur InnoDB par défaut pour toute
                # nouvelle table. Indispensable aux transactions et aux
                # verrous de lignes (select_for_update) du socle financier.
                "init_command": (
                    "SET sql_mode='STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,"
                    "NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION',"
                    " default_storage_engine=INNODB"
                ),
                "isolation_level": "read committed",
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ---------------------------------------------------------------------------
# Modèle utilisateur personnalisé (Client / Agent / Administrateur)
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "comptes.Utilisateur"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "comptes:connexion"
LOGIN_REDIRECT_URL = "comptes:tableau_de_bord"
LOGOUT_REDIRECT_URL = "comptes:connexion"

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Ndjamena"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Fichiers statiques et médias (photos KYC simulées, relevés PDF, etc.)
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Documents KYC et pièces justificatives : stockage PRIVÉ, hors MEDIA_ROOT.
# Ils ne sont jamais servis par /media/ ; seul un contrôleur d'accès Django
# authentifié et autorisé peut les lire.
PRIVATE_STORAGE_ROOT = BASE_DIR / "private_storage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Sécurité applicative complémentaire
# ---------------------------------------------------------------------------
CSRF_COOKIE_HTTPONLY = False  # nécessaire pour laisser le template lire le jeton dans le formulaire
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 2  # session client = 2h d'inactivité max
X_FRAME_OPTIONS = "DENY"

# Limitation des tentatives de connexion (protection brute-force) :
# nombre d'essais autorisés et durée de blocage, utilisés par comptes/views.py
LOGIN_MAX_TENTATIVES = int(os.getenv("LOGIN_MAX_TENTATIVES", "5"))
LOGIN_BLOCAGE_MINUTES = int(os.getenv("LOGIN_BLOCAGE_MINUTES", "5"))

# ---------------------------------------------------------------------------
# Plafonds de transaction par défaut (modifiables par compte, voir modèle)
# ---------------------------------------------------------------------------
PLAFOND_JOURNALIER_DEFAUT = int(os.getenv("PLAFOND_JOURNALIER_DEFAUT", "1000000"))     # FCFA
PLAFOND_HEBDOMADAIRE_DEFAUT = int(os.getenv("PLAFOND_HEBDOMADAIRE_DEFAUT", "5000000"))  # FCFA
PLAFOND_MENSUEL_DEFAUT = int(os.getenv("PLAFOND_MENSUEL_DEFAUT", "10000000"))          # FCFA

# ---------------------------------------------------------------------------
# Micro-service de paiement mobile (FastAPI)
# ---------------------------------------------------------------------------
PAIEMENT_MOBILE_API_URL = os.getenv("PAIEMENT_MOBILE_API_URL", "http://127.0.0.1:8001")
PAIEMENT_MOBILE_API_TIMEOUT = float(os.getenv("PAIEMENT_MOBILE_API_TIMEOUT", "5"))
# Secret partagé entre Django et le microservice de SIMULATION uniquement.
# En production, il doit être fourni par le gestionnaire de secrets et
# remplacé par la vraie authentification/signature de l'opérateur.
PAIEMENT_MOBILE_API_SECRET = os.getenv(
    "PAIEMENT_MOBILE_API_SECRET", "demo-interne-a-changer-en-production"
)

# ---------------------------------------------------------------------------
# Double authentification TOTP
# ---------------------------------------------------------------------------
TOTP_ISSUER = os.getenv("TOTP_ISSUER", "Ma Banque")
# Clé Fernet de chiffrement des secrets TOTP. Si elle n'est pas fournie,
# une clé est dérivée de SECRET_KEY (acceptable en démonstration, mais une
# clé dédiée est recommandée en production et doit être sauvegardée).
TOTP_ENCRYPTION_KEY = os.getenv("TOTP_ENCRYPTION_KEY", "")
TOTP_PERIODE = 30
TOTP_CHIFFRES = 6

# ---------------------------------------------------------------------------
# E-mails applicatifs (réinitialisation de mot de passe)
#
# EMAIL_BACKEND_MODE :
#   - "file"    : e-mails écrits dans banque_app/emails_dev/ (défaut local) ;
#   - "console" : affichage console ;
#   - "smtp"    : envoi réel, nécessite les paramètres SMTP ci-dessous.
# Aucun identifiant SMTP n'est fourni par défaut : l'envoi réel exige une
# configuration explicite de l'administrateur.
# ---------------------------------------------------------------------------
EMAIL_BACKEND_MODE = os.getenv("EMAIL_BACKEND_MODE", "file").strip().lower()
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@ma-banque.local")
EMAIL_FILE_PATH = BASE_DIR / "emails_dev"

if EMAIL_BACKEND_MODE == "smtp":
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST", "")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", "True")
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", "False")
elif EMAIL_BACKEND_MODE == "console":
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"

# ---------------------------------------------------------------------------
# Durcissement HTTP (actif hors DEBUG ou sur demande explicite)
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", "False")
SESSION_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", "False" if DEBUG else "True")
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if env_bool("DJANGO_DERRIERE_PROXY", "False"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Taille maximale des formulaires et fichiers téléversés (KYC : 5 Mo/document)
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024


# ---------------------------------------------------------------------------
# Messages Django (mappage vers classes CSS maison)
# ---------------------------------------------------------------------------
from django.contrib.messages import constants as messages_constants  # noqa: E402

MESSAGE_TAGS = {
    messages_constants.DEBUG: "debug",
    messages_constants.INFO: "info",
    messages_constants.SUCCESS: "succes",
    messages_constants.WARNING: "attention",
    messages_constants.ERROR: "erreur",
}
