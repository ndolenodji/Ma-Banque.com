"""Formulaires du parcours de vérification d'identité (KYC)."""

from django import forms

from .models import DocumentKYC, DossierKYC


class FormulaireDossierKYC(forms.ModelForm):
    class Meta:
        model = DossierKYC
        fields = ["type_piece", "numero_piece", "date_naissance", "nationalite", "adresse", "profession"]
        widgets = {
            "date_naissance": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "adresse": forms.TextInput(attrs={"placeholder": "Quartier, ville, pays"}),
        }

    def clean_numero_piece(self):
        valeur = (self.cleaned_data.get("numero_piece") or "").strip()
        if len(valeur) < 4:
            raise forms.ValidationError("Le numéro de pièce semble trop court.")
        return valeur


class FormulaireDocumentKYC(forms.ModelForm):
    class Meta:
        model = DocumentKYC
        fields = ["type_document", "fichier"]
        widgets = {"fichier": forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,.pdf"})}

    def clean_fichier(self):
        fichier = self.cleaned_data.get("fichier")
        if not fichier:
            return fichier
        if fichier.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Le fichier ne doit pas dépasser 5 Mo.")
        return fichier
