"""Champ de compte permettant la saisie libre ou la sélection.

Le client peut taper directement le numéro de son compte ou le choisir
dans la liste proposée (balise ``datalist``). La résolution reste faite
côté serveur, uniquement parmi les comptes autorisés du formulaire.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html, format_html_join

from transactions.models import CompteBancaire


class WidgetCompteEditable(forms.TextInput):
    """Zone de saisie avec suggestions des comptes de l'utilisateur."""

    def __init__(self, attrs=None):
        attributs = {
            "autocomplete": "off",
            "inputmode": "numeric",
            "placeholder": "Saisissez ou sélectionnez un numéro de compte",
        }
        if attrs:
            attributs.update(attrs)
        super().__init__(attributs)
        self.comptes = []

    def set_comptes(self, comptes):
        self.comptes = list(comptes)

    def render(self, name, value, attrs=None, renderer=None):
        attrs = dict(attrs or {})
        attrs.setdefault("id", f"id_{name}")
        liste_id = f"liste-{attrs['id']}"
        attrs["list"] = liste_id
        champ = super().render(name, value, attrs, renderer)
        options = format_html_join(
            "",
            '<option value="{}">{}</option>',
            (
                (
                    compte.numero_compte,
                    f"{compte.get_type_compte_display()} — n° {compte.numero_compte} — "
                    f"{compte.solde} {compte.code_devise}",
                )
                for compte in self.comptes
            ),
        )
        return format_html(
            '{}<datalist id="{}">{}</datalist>',
            champ,
            liste_id,
            options,
        )


class ChampCompteEditable(forms.ModelChoiceField):
    """ModelChoiceField acceptant un numéro de compte ou son identifiant."""

    widget = WidgetCompteEditable

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", WidgetCompteEditable())
        kwargs.setdefault(
            "help_text",
            "Saisissez le numéro de compte ou sélectionnez-le dans la liste.",
        )
        super().__init__(*args, **kwargs)

    def label_from_instance(self, compte):
        return (
            f"{compte.get_type_compte_display()} n° {compte.numero_compte} — "
            f"{compte.solde} {compte.code_devise}"
        )

    def prepare_value(self, value):
        if isinstance(value, CompteBancaire):
            return value.numero_compte
        return super().prepare_value(value)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        valeur = str(value).strip().replace(" ", "")
        compte = self.queryset.filter(numero_compte=valeur).first()
        if compte is None and valeur.isdigit():
            # Rétrocompatibilité avec les formulaires qui transmettent la clé.
            compte = self.queryset.filter(pk=int(valeur)).first()
        if compte is None:
            raise ValidationError(
                "Aucun compte bancaire actif ne correspond à ce numéro.",
                code="invalid_choice",
            )
        return compte
