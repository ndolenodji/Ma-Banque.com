from django import forms
from django.core.exceptions import ValidationError as ErreurDjango

from comptes.widgets import ChampCompteEditable
from transactions.models import CompteBancaire

from . import providers
from .models import PaiementMobile


class FormulairePaiementMobile(forms.Form):
    compte_bancaire = ChampCompteEditable(
        queryset=CompteBancaire.objects.none(), label="Compte bancaire concerné"
    )
    operateur = forms.ChoiceField(choices=(), label="Opérateur mobile money")
    numero_telephone = forms.CharField(max_length=20, label="Numéro de téléphone mobile money")
    montant = forms.DecimalField(min_value=1, max_digits=14, decimal_places=2, label="Montant (FCFA)")

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["operateur"].choices = providers.operateurs_pour_formulaire()
        if utilisateur is not None:
            self.fields["compte_bancaire"].queryset = utilisateur.comptes_bancaires.filter(
                statut=CompteBancaire.Statut.ACTIF
            )
            self.fields["compte_bancaire"].widget.set_comptes(
                self.fields["compte_bancaire"].queryset
            )
            if utilisateur.telephone:
                self.fields["numero_telephone"].initial = utilisateur.telephone

    def clean_numero_telephone(self):
        numero = self.cleaned_data["numero_telephone"]
        operateur = self.cleaned_data.get("operateur")
        try:
            return providers.valider_numero(numero, operateur)
        except ErreurDjango as erreur:
            raise forms.ValidationError(erreur.messages) from erreur

    def clean(self):
        donnees = super().clean()
        compte = donnees.get("compte_bancaire")
        if compte is not None and compte.statut != CompteBancaire.Statut.ACTIF:
            self.add_error(
                "compte_bancaire",
                "Ce compte n'est pas actif : choisissez un compte ouvert pour une opération mobile.",
            )
        return donnees
