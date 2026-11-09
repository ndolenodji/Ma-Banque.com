from django.apps import AppConfig


class RisquesConfig(AppConfig):
    name = "risques"
    verbose_name = "Détection des risques"

    def ready(self):
        from . import signals  # noqa: F401
