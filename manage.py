#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import subprocess
import sys
from pathlib import Path

MARQUEUR_VENV = "MA_BANQUE_VENV"


def activer_venv_local():
    """Relance la commande avec le Python du venv livré avec le projet.

    Objectif : « python manage.py ... » fonctionne quel que soit le Python
    présent dans le PATH, sans aucune installation supplémentaire ni
    activation manuelle de l'environnement.
    """
    if os.environ.get(MARQUEUR_VENV) == "1":
        return
    racine = Path(__file__).resolve().parent
    candidats = [
        racine / "venv" / "Scripts" / "python.exe",  # Windows
        racine / "venv" / "bin" / "python",  # Linux / macOS
    ]
    interpreteur = next((chemin for chemin in candidats if chemin.exists()), None)
    if interpreteur is None:
        # Pas de venv local : on laisse le Python courant tenter sa chance.
        return
    try:
        if Path(sys.executable).resolve() == interpreteur.resolve():
            return
    except OSError:
        pass
    environnement = dict(os.environ)
    environnement[MARQUEUR_VENV] = "1"
    try:
        code = subprocess.call([str(interpreteur), *sys.argv], env=environnement)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(code)


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'banque_app.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    activer_venv_local()
    main()
