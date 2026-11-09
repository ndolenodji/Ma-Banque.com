"""Formulaires du module épargne : objectifs, contributions, arrondis, budgets."""

import re
import uuid
from decimal import Decimal

from django import forms

from transactions.models import CompteBancaire, Transaction

from .models import Budget, ConfigArrondi, ObjectifEpargne


class CompteChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, compte):
        return (
            f"{compte.get_type_compte_display()} n° {compte.numero_compte} — "
            f"{compte.solde} {compte.code_devise}"
        )


def _comptes(utilisateur, *, type_compte=None):
    requete = utilisateur.comptes_bancaires.filter(statut=CompteBancaire.Statut.ACTIF)
    if type_compte:
        requete = requete.filter(type_compte=type_compte)
    return requete.order_by("type_compte", "numero_compte")


class FormulaireObjectif(forms.ModelForm):
    class Meta:
        model = ObjectifEpargne
        fields = ["nom", "description", "montant_cible", "compte_epargne", "date_cible", "couleur"]
        labels = {
            "nom": "Nom de l'objectif",
            "description": "Description",
            "montant_cible": "Montant cible (FCFA)",
            "compte_epargne": "Compte d'épargne",
            "date_cible": "Date cible (facultative)",
            "couleur": "Couleur",
        }
        widgets = {
            "description": forms.TextInput(attrs={"placeholder": "Ex. : voyage, fonds d'urgence..."}),
            "date_cible": forms.DateInput(attrs={"type": "date"}),
            "couleur": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.utilisateur = utilisateur
        if utilisateur is not None:
            if not self.instance.pk:
                # Le propriétaire doit être connu pendant la validation du
                # modèle (clean), avant l'enregistrement.
                self.instance.proprietaire = utilisateur
            self.fields["compte_epargne"].queryset = _comptes(
                utilisateur, type_compte=CompteBancaire.TypeCompte.EPARGNE
            )
        self.fields["compte_epargne"].empty_label = "Sélectionner un compte d'épargne"
        self.fields["montant_cible"].widget.attrs.update({"min": "1", "step": "1"})

    def clean_montant_cible(self):
        montant = self.cleaned_data["montant_cible"]
        if montant <= 0:
            raise forms.ValidationError("Le montant cible doit être supérieur à zéro.")
        if self.instance.pk and montant < self.instance.montant_actuel:
            raise forms.ValidationError(
                "Le montant cible ne peut pas être inférieur au montant déjà épargné "
                f"({self.instance.montant_actuel} FCFA)."
            )
        return montant

    def clean_couleur(self):
        couleur = self.cleaned_data.get("couleur") or "#1656d1"
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", couleur):
            raise forms.ValidationError("Choisissez une couleur au format #RRGGBB.")
        return couleur

    def clean(self):
        donnees = super().clean()
        compte = donnees.get("compte_epargne")
        if compte is not None and self.utilisateur is not None:
            if compte.proprietaire_id != self.utilisateur.pk:
                raise forms.ValidationError("Le compte d'épargne sélectionné ne vous appartient pas.")
        return donnees

    def save(self, commit=True):
        objectif = super().save(commit=False)
        objectif.proprietaire = self.utilisateur
        if commit:
            objectif.save()
        return objectif


class FormulaireContribution(forms.Form):
    compte_source = CompteChoiceField(
        queryset=CompteBancaire.objects.none(),
        label="Depuis le compte",
        empty_label="Sélectionner un compte",
    )
    montant = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Montant à épargner (FCFA)",
    )
    cle_idempotence = forms.UUIDField(widget=forms.HiddenInput, required=False, initial=uuid.uuid4)

    def __init__(self, *args, objectif=None, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.objectif = objectif
        if utilisateur is not None:
            self.fields["compte_source"].queryset = _comptes(utilisateur).exclude(
                pk=getattr(objectif, "compte_epargne_id", None)
            )
        if not self.is_bound:
            self.fields["cle_idempotence"].initial = str(uuid.uuid4())

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if self.objectif is not None and montant > self.objectif.restant:
            raise forms.ValidationError(
                f"Le montant dépasse le restant à épargner ({self.objectif.restant} FCFA)."
            )
        return montant

    def clean(self):
        donnees = super().clean()
        compte = donnees.get("compte_source")
        if compte is not None and self.objectif is not None:
            if compte.pk == self.objectif.compte_epargne_id:
                raise forms.ValidationError(
                    "Le compte source doit être différent du compte d'épargne de l'objectif."
                )
        return donnees


class FormulaireArrondi(forms.ModelForm):
    class Meta:
        model = ConfigArrondi
        fields = ["actif", "compte_source", "compte_epargne", "multiple", "objectif"]
        labels = {
            "actif": "Activer l'arrondi automatique",
            "compte_source": "Compte des dépenses",
            "compte_epargne": "Compte d'épargne crédité",
            "multiple": "Arrondir au multiple de",
            "objectif": "Objectif à alimenter (facultatif)",
        }

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.utilisateur = utilisateur
        if utilisateur is not None:
            if not self.instance.pk:
                self.instance.proprietaire = utilisateur
            self.fields["compte_source"].queryset = _comptes(utilisateur)
            self.fields["compte_epargne"].queryset = _comptes(
                utilisateur, type_compte=CompteBancaire.TypeCompte.EPARGNE
            )
            self.fields["objectif"].queryset = utilisateur.objectifs_epargne.filter(
                statut=ObjectifEpargne.Statut.ACTIF
            ).order_by("nom")
        self.fields["objectif"].empty_label = "Aucun objectif (compte d'épargne seul)"
        self.fields["objectif"].required = False

    def clean(self):
        donnees = super().clean()
        source = donnees.get("compte_source")
        epargne = donnees.get("compte_epargne")
        objectif = donnees.get("objectif")
        if source is not None and self.utilisateur is not None and source.proprietaire_id != self.utilisateur.pk:
            raise forms.ValidationError("Le compte des dépenses ne vous appartient pas.")
        if source is not None and epargne is not None and source.pk == epargne.pk:
            raise forms.ValidationError("Les comptes source et d'épargne doivent être différents.")
        if objectif is not None and epargne is not None and objectif.compte_epargne_id != epargne.pk:
            raise forms.ValidationError(
                "L'objectif sélectionné doit être rattaché au compte d'épargne choisi."
            )
        return donnees

    def save(self, commit=True):
        config = super().save(commit=False)
        config.proprietaire = self.utilisateur
        if commit:
            config.save()
        return config


class FormulaireBudget(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ["categorie", "plafond_mensuel", "seuil_alerte", "actif"]
        labels = {
            "categorie": "Catégorie de dépenses",
            "plafond_mensuel": "Plafond mensuel (FCFA)",
            "seuil_alerte": "Seuil d'alerte (%)",
            "actif": "Budget actif",
        }
        widgets = {
            "plafond_mensuel": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "seuil_alerte": forms.NumberInput(attrs={"min": "1", "max": "100"}),
        }

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.utilisateur = utilisateur
        if utilisateur is not None and not self.instance.pk:
            self.instance.proprietaire = utilisateur
        categories_exclues = {Transaction.Categorie.SALAIRE}
        self.fields["categorie"].choices = [
            choix for choix in Transaction.Categorie.choices if choix[0] not in categories_exclues
        ]

    def clean_categorie(self):
        categorie = self.cleaned_data["categorie"]
        if self.utilisateur is not None:
            doublon = Budget.objects.filter(
                proprietaire=self.utilisateur, categorie=categorie
            ).exclude(pk=self.instance.pk)
            if doublon.exists():
                raise forms.ValidationError("Un budget existe déjà pour cette catégorie.")
        return categorie

    def save(self, commit=True):
        budget = super().save(commit=False)
        budget.proprietaire = self.utilisateur
        if commit:
            budget.save()
        return budget
