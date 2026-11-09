"""Formulaires du réseau d'agents et de marchands."""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from kyc.models import DossierKYC
from transactions.models import CompteBancaire

from .models import Marchand, OperationAgent


class FormulaireInscriptionMarchand(forms.ModelForm):
    class Meta:
        model = Marchand
        fields = ["nom", "categorie", "ville", "adresse", "compte_reglement"]
        labels = {
            "nom": "Nom commercial",
            "categorie": "Catégorie d'activité",
            "ville": "Ville",
            "adresse": "Adresse",
            "compte_reglement": "Compte de règlement",
        }
        widgets = {
            "adresse": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.utilisateur = utilisateur
        if utilisateur is not None:
            self.fields["compte_reglement"].queryset = utilisateur.comptes_bancaires.filter(
                statut=CompteBancaire.Statut.ACTIF
            )
            self.instance.proprietaire = utilisateur

    def clean(self):
        donnees = super().clean()
        if self.utilisateur is not None and self.instance.pk is None:
            dossier = DossierKYC.objects.filter(utilisateur=self.utilisateur).first()
            if dossier is None or dossier.statut != DossierKYC.Statut.APPROUVE:
                raise ValidationError(
                    "Votre dossier KYC doit être approuvé avant de créer un compte marchand."
                )
        return donnees


class FormulaireDemandePaiement(forms.Form):
    montant = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2, label="Montant (FCFA)"
    )
    description = forms.CharField(max_length=160, required=False, label="Objet du paiement")
    duree = forms.TypedChoiceField(
        choices=[
            (15, "15 minutes"),
            (30, "30 minutes"),
            (60, "1 heure"),
            (240, "4 heures"),
            (1440, "24 heures"),
        ],
        coerce=int,
        initial=30,
        label="Validité du QR code",
    )


class FormulairePaiementDemande(forms.Form):
    compte_bancaire = forms.ModelChoiceField(
        queryset=CompteBancaire.objects.none(), label="Compte à débiter"
    )

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        if utilisateur is not None:
            self.fields["compte_bancaire"].queryset = utilisateur.comptes_bancaires.filter(
                statut=CompteBancaire.Statut.ACTIF
            )


class FormulaireOperationAgent(forms.Form):
    numero_compte = forms.CharField(max_length=10, label="Numéro de compte client")
    type_operation = forms.ChoiceField(
        choices=OperationAgent.Type.choices, label="Type d'opération de caisse"
    )
    montant = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2, label="Montant (FCFA)"
    )
    note = forms.CharField(max_length=255, required=False, label="Note (facultative)")

    def clean_numero_compte(self):
        numero = (self.cleaned_data["numero_compte"] or "").strip()
        compte = CompteBancaire.objects.filter(numero_compte=numero).first()
        if compte is None:
            raise forms.ValidationError("Aucun compte bancaire ne porte ce numéro.")
        self.compte = compte
        return numero
