from django.apps import AppConfig


class ComptesConfig(AppConfig):
    name = "comptes"
    verbose_name = "Comptes et sécurité"

    def ready(self):
        from . import signals  # noqa: F401 - enregistre les récepteurs de connexion
