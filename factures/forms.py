"""Formulaires du paiement de factures simulées."""

import uuid
from datetime import timedelta

from django import forms
from django.utils import timezone

from transactions.models import CompteBancaire

from .models import CompteFacture, Facture, FournisseurFacture


class CompteChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, compte):
        return (
            f"{compte.get_type_compte_display()} n° {compte.numero_compte} — "
            f"{compte.solde} {compte.code_devise}"
        )


class FormulaireFacture(forms.ModelForm):
    reference_client = forms.CharField(
        max_length=80,
        required=False,
        label="Votre référence client chez le fournisseur (facultatif)",
        help_text="Mémorisée pour faciliter les prochains paiements.",
    )

    class Meta:
        model = Facture
        fields = [
            "fournisseur",
            "reference_externe",
            "libelle",
            "montant",
            "date_emission",
            "date_echeance",
        ]
        labels = {
            "fournisseur": "Fournisseur (simulation)",
            "reference_externe": "Référence de la facture chez le fournisseur",
            "libelle": "Libellé",
            "montant": "Montant (FCFA)",
            "date_emission": "Date d'émission",
            "date_echeance": "Date d'échéance",
        }
        widgets = {
            "date_emission": forms.DateInput(attrs={"type": "date"}),
            "date_echeance": forms.DateInput(attrs={"type": "date"}),
            "montant": forms.NumberInput(attrs={"min": "1", "step": "1"}),
        }

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.utilisateur = utilisateur
        self.fields["fournisseur"].queryset = FournisseurFacture.objects.filter(actif=True)
        self.fields["reference_externe"].required = False
        if not self.is_bound and not self.instance.pk:
            aujourd_hui = timezone.localdate()
            self.fields["date_emission"].initial = aujourd_hui
            self.fields["date_echeance"].initial = aujourd_hui + timedelta(days=30)

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise forms.ValidationError("Le montant doit être positif.")
        return montant

    def save(self, commit=True):
        facture = super().save(commit=False)
        facture.proprietaire = self.utilisateur
        if commit:
            facture.save()
            reference_client = (self.cleaned_data.get("reference_client") or "").strip()
            if reference_client:
                reference, _ = CompteFacture.objects.get_or_create(
                    proprietaire=self.utilisateur,
                    fournisseur=facture.fournisseur,
                    reference_client=reference_client,
                    defaults={"libelle": facture.libelle},
                )
                if facture.libelle and reference.libelle != facture.libelle:
                    reference.libelle = facture.libelle
                    reference.save(update_fields=["libelle"])
        return facture


class FormulairePaiementFacture(forms.Form):
    compte = CompteChoiceField(
        queryset=CompteBancaire.objects.none(),
        label="Compte à débiter",
        empty_label="Sélectionner un compte",
    )
    cle_idempotence = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        if utilisateur is not None:
            self.fields["compte"].queryset = utilisateur.comptes_bancaires.filter(
                statut=CompteBancaire.Statut.ACTIF
            ).order_by("type_compte", "numero_compte")
        if not self.is_bound:
            self.fields["cle_idempotence"].initial = str(uuid.uuid4())
