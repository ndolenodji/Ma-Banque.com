"""Formulaires des opérations bancaires.

Les montants restent des ``Decimal`` (jamais de flottants binaires). Chaque
formulaire d'opération embarque une clé d'idempotence transmise au modèle :
un double clic ou un renvoi du formulaire ne peut pas débiter deux fois.
"""

import uuid

from django import forms
from django.utils import timezone

from comptes.widgets import ChampCompteEditable
from devises.models import Devise

from .models import CompteBancaire, Transaction, VirementProgramme


def _cle_idempotence():
    return str(uuid.uuid4())


CHOIX_CATEGORIES = [("", "Non catégorisé")] + list(Transaction.Categorie.choices)


def _comptes_actifs(utilisateur):
    return utilisateur.comptes_bancaires.filter(statut=CompteBancaire.Statut.ACTIF)


class CompteChoiceField(forms.ModelChoiceField):
    """Liste de comptes lisible : type, numéro et solde disponible."""

    def label_from_instance(self, compte):
        return (
            f"{compte.get_type_compte_display()} n° {compte.numero_compte} — "
            f"{compte.solde} {compte.code_devise}"
        )


class FormulaireDepot(forms.Form):
    compte = ChampCompteEditable(queryset=CompteBancaire.objects.none(), label="Compte à créditer")
    montant = forms.DecimalField(min_value=1, max_digits=14, decimal_places=2, label="Montant")
    tiers_nom = forms.CharField(
        max_length=150,
        required=False,
        label="Nom du déposant (optionnel)",
        help_text="À renseigner si les fonds sont déposés par une autre personne.",
    )
    tiers_telephone = forms.CharField(max_length=20, required=False, label="Téléphone du déposant (optionnel)")
    cle_idempotence = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        if utilisateur is not None:
            self.fields["compte"].queryset = _comptes_actifs(utilisateur)
            self.fields["compte"].widget.set_comptes(self.fields["compte"].queryset)
        if not self.is_bound:
            self.fields["cle_idempotence"].initial = _cle_idempotence()

    def clean(self):
        donnees = super().clean()
        if donnees.get("tiers_telephone") and not donnees.get("tiers_nom"):
            raise forms.ValidationError("Indiquez le nom du déposant avec son numéro de téléphone.")
        return donnees


class FormulaireRetrait(forms.Form):
    compte = ChampCompteEditable(queryset=CompteBancaire.objects.none(), label="Compte à débiter")
    montant = forms.DecimalField(min_value=1, max_digits=14, decimal_places=2, label="Montant")
    categorie = forms.ChoiceField(
        required=False,
        choices=CHOIX_CATEGORIES,
        label="Catégorie de dépense (optionnel)",
        help_text="Utile au suivi de vos budgets.",
    )
    cle_idempotence = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        if utilisateur is not None:
            self.fields["compte"].queryset = _comptes_actifs(utilisateur)
            self.fields["compte"].widget.set_comptes(self.fields["compte"].queryset)
        if not self.is_bound:
            self.fields["cle_idempotence"].initial = _cle_idempotence()


class FormulaireVirement(forms.Form):
    compte_source = ChampCompteEditable(queryset=CompteBancaire.objects.none(), label="Depuis")
    numero_compte_destination = forms.CharField(
        max_length=10,
        label="Numéro de compte du bénéficiaire",
        help_text="Saisissez ou sélectionnez un bénéficiaire enregistré (compte interne à 10 chiffres).",
        widget=forms.TextInput(
            attrs={
                "list": "beneficiaires-virement",
                "autocomplete": "off",
                "inputmode": "numeric",
                "placeholder": "Saisissez le numéro de compte",
            }
        ),
    )
    montant = forms.DecimalField(min_value=1, max_digits=14, decimal_places=2, label="Montant")
    description = forms.CharField(max_length=255, required=False, label="Motif (optionnel)")
    categorie = forms.ChoiceField(
        required=False,
        choices=CHOIX_CATEGORIES,
        label="Catégorie de dépense (optionnel)",
        help_text="Utile au suivi de vos budgets.",
    )
    cle_idempotence = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        if utilisateur is not None:
            self.fields["compte_source"].queryset = _comptes_actifs(utilisateur)
            self.fields["compte_source"].widget.set_comptes(self.fields["compte_source"].queryset)
        if not self.is_bound:
            self.fields["cle_idempotence"].initial = _cle_idempotence()

    def clean_numero_compte_destination(self):
        numero = self.cleaned_data["numero_compte_destination"].strip()
        compte = CompteBancaire.objects.filter(numero_compte=numero).first()
        if compte is None:
            raise forms.ValidationError("Aucun compte bancaire ne correspond à ce numéro.")
        if compte.statut != CompteBancaire.Statut.ACTIF:
            raise forms.ValidationError("Le compte destinataire n'est pas actif.")
        return numero

    def clean(self):
        donnees = super().clean()
        source = donnees.get("compte_source")
        numero = donnees.get("numero_compte_destination")
        if source is not None and numero == source.numero_compte:
            raise forms.ValidationError("Choisissez un compte destinataire différent du compte source.")
        if source is not None and numero:
            destination = CompteBancaire.objects.filter(numero_compte=numero).first()
            if destination is not None and destination.devise_id != source.devise_id:
                raise forms.ValidationError(
                    "Les deux comptes n'utilisent pas la même devise. Passez par le module de change."
                )
        return donnees


class FormulaireOuvertureCompte(forms.Form):
    type_compte = forms.ChoiceField(choices=CompteBancaire.TypeCompte.choices, label="Type de compte")
    devise = forms.ModelChoiceField(
        queryset=Devise.objects.filter(est_active=True),
        label="Devise du compte",
        help_text="La devise XAF (FCFA) est la devise de référence de la banque.",
    )

    def clean_devise(self):
        devise = self.cleaned_data["devise"]
        if not devise.est_active:
            raise forms.ValidationError("Cette devise n'est pas ouverte à l'ouverture de compte.")
        return devise


class FormulaireVirementProgramme(forms.Form):
    compte_source = CompteChoiceField(queryset=CompteBancaire.objects.none(), label="Compte à débiter")
    numero_destination = forms.CharField(
        max_length=10,
        label="Numéro de compte du bénéficiaire",
        help_text="Compte interne à la banque (10 chiffres).",
    )
    montant = forms.DecimalField(min_value=1, max_digits=14, decimal_places=2, label="Montant de chaque échéance")
    description = forms.CharField(max_length=255, required=False, label="Motif (optionnel)")
    frequence = forms.ChoiceField(choices=VirementProgramme.Frequence.choices, label="Fréquence")
    date_debut = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}), label="Première échéance"
    )
    date_fin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Dernière échéance (optionnel)",
    )
    cle_idempotence = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        if utilisateur is not None:
            self.fields["compte_source"].queryset = _comptes_actifs(utilisateur)
        if not self.is_bound:
            self.fields["date_debut"].initial = timezone.localdate()
            self.fields["cle_idempotence"].initial = _cle_idempotence()

    def clean_numero_destination(self):
        numero = self.cleaned_data["numero_destination"].strip()
        compte = CompteBancaire.objects.filter(numero_compte=numero).first()
        if compte is None:
            raise forms.ValidationError("Aucun compte bancaire ne correspond à ce numéro.")
        if compte.statut != CompteBancaire.Statut.ACTIF:
            raise forms.ValidationError("Le compte destinataire n'est pas actif.")
        return numero

    def clean(self):
        donnees = super().clean()
        source = donnees.get("compte_source")
        destination_numero = donnees.get("numero_destination")
        debut = donnees.get("date_debut")
        fin = donnees.get("date_fin")
        if source is not None and destination_numero == source.numero_compte:
            raise forms.ValidationError("Le compte destinataire doit être différent du compte source.")
        if source is not None and destination_numero:
            destination = CompteBancaire.objects.filter(numero_compte=destination_numero).first()
            if destination is not None and destination.devise_id != source.devise_id:
                raise forms.ValidationError(
                    "Les deux comptes n'utilisent pas la même devise. Utilisez la conversion de devises."
                )
        if debut and fin and fin < debut:
            raise forms.ValidationError("La date de fin ne peut pas précéder la première échéance.")
        if debut and donnees.get("frequence") == VirementProgramme.Frequence.UNE_FOIS and fin:
            raise forms.ValidationError("Un virement unique n'a pas besoin de date de fin.")
        return donnees


class FormulaireFiltreHistorique(forms.Form):
    date_debut = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Du")
    date_fin = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Au")
    type_transaction = forms.ChoiceField(
        required=False,
        choices=[("", "Tous les types")] + list(Transaction.Type.choices),
        label="Type",
    )
    montant_min = forms.DecimalField(required=False, min_value=0, label="Montant minimum")


class FormulaireFiltreReleve(forms.Form):
    compte = CompteChoiceField(queryset=CompteBancaire.objects.none(), label="Compte")
    date_debut = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Du")
    date_fin = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Au")

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        aujourdhui = timezone.localdate()
        if utilisateur is not None:
            self.fields["compte"].queryset = utilisateur.comptes_bancaires.all()
        if not self.is_bound:
            self.fields["date_fin"].initial = aujourdhui
            self.fields["date_debut"].initial = aujourdhui - timezone.timedelta(days=29)

    def clean(self):
        donnees = super().clean()
        debut = donnees.get("date_debut")
        fin = donnees.get("date_fin")
        if debut and fin:
            if fin < debut:
                raise forms.ValidationError("La date de fin ne peut pas précéder la date de début.")
            if (fin - debut).days > 366:
                raise forms.ValidationError("La période d'un relevé est limitée à 366 jours.")
        return donnees
