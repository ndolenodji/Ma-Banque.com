"""Formulaires de conversion entre devises."""

import uuid
from decimal import Decimal, ROUND_HALF_UP

from django import forms

from transactions.models import CompteBancaire


class CompteChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, compte):
        return (
            f"{compte.get_type_compte_display()} n° {compte.numero_compte} — "
            f"{compte.solde} {compte.code_devise}"
        )


def _comptes_actifs(utilisateur):
    return utilisateur.comptes_bancaires.filter(
        statut=CompteBancaire.Statut.ACTIF
    ).select_related("devise").order_by("devise__code", "numero_compte")


class FormulaireConversion(forms.Form):
    compte_source = CompteChoiceField(
        queryset=CompteBancaire.objects.none(),
        label="Compte à débiter",
        empty_label="Sélectionner un compte",
    )
    compte_destination = CompteChoiceField(
        queryset=CompteBancaire.objects.none(),
        label="Compte à créditer",
        empty_label="Sélectionner un compte",
    )
    montant = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Montant à convertir",
    )
    cle_idempotence = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.utilisateur = utilisateur
        if utilisateur is not None:
            comptes = _comptes_actifs(utilisateur)
            self.fields["compte_source"].queryset = comptes
            self.fields["compte_destination"].queryset = comptes
        if not self.is_bound:
            self.fields["cle_idempotence"].initial = str(uuid.uuid4())

    def clean(self):
        donnees = super().clean()
        source = donnees.get("compte_source")
        destination = donnees.get("compte_destination")
        if source is None or destination is None:
            return donnees
        if source.pk == destination.pk:
            raise forms.ValidationError("Les comptes source et destination doivent être différents.")
        if source.devise_id == destination.devise_id:
            raise forms.ValidationError("Choisissez deux devises différentes pour une conversion.")
        for compte in (source, destination):
            devise = compte.devise
            if devise.code != "XAF" and not devise.taux_configure:
                raise forms.ValidationError(
                    f"Le taux {devise.code} → XAF n'est pas configuré. "
                    "Un administrateur doit saisir un taux et sa source."
                )
        return donnees

    def apercu(self):
        """Retourne un aperçu exact (Decimal) sans exécuter la conversion."""
        donnees = self.cleaned_data
        source = donnees["compte_source"]
        destination = donnees["compte_destination"]
        montant = donnees["montant"]
        montant_xaf = source.devise.vers_xaf(montant)
        montant_destination = destination.devise.depuis_xaf(montant_xaf)
        taux = (montant_destination / montant).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        return {
            "montant_destination": montant_destination,
            "taux": taux,
            "code_source": source.code_devise,
            "code_destination": destination.code_devise,
        }
