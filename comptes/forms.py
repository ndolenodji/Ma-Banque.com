"""
Formulaires Django pour l'inscription, la connexion, la gestion du profil,
le KYC simplifié et les bénéficiaires favoris.

La validation stricte côté serveur est assurée nativement par les
`forms.Form` / `forms.ModelForm` de Django (types de champs, `clean_*`,
contraintes des modèles).
"""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError

from .models import Beneficiaire, Utilisateur


class FormulaireInscription(UserCreationForm):
    """Formulaire d'ouverture de compte client (inscription publique)."""

    email = forms.EmailField(required=True, label="Adresse e-mail")
    telephone = forms.CharField(
        required=True,
        max_length=20,
        label="Numéro de téléphone (mobile money)",
        help_text="Ex : 66 12 34 56 - utilisé pour le paiement mobile",
    )
    numero_piece_identite = forms.CharField(required=True, max_length=50, label="Numéro de pièce d'identité")

    class Meta:
        model = Utilisateur
        fields = [
            "first_name",
            "last_name",
            "username",
            "email",
            "telephone",
            "numero_piece_identite",
            "password1",
            "password2",
        ]
        labels = {
            "first_name": "Prénom",
            "last_name": "Nom",
            "username": "Identifiant de connexion",
        }

    def clean_telephone(self):
        telephone = self.cleaned_data["telephone"].replace(" ", "")
        if not telephone.isdigit() or len(telephone) < 8:
            raise ValidationError("Numéro de téléphone invalide.")
        if Utilisateur.objects.filter(telephone=telephone).exists():
            raise ValidationError("Ce numéro de téléphone est déjà utilisé par un autre compte.")
        return telephone

    def save(self, commit=True):
        utilisateur = super().save(commit=False)
        utilisateur.role = Utilisateur.Role.CLIENT
        utilisateur.email = self.cleaned_data["email"]
        utilisateur.telephone = self.cleaned_data["telephone"]
        utilisateur.numero_piece_identite = self.cleaned_data["numero_piece_identite"]
        if commit:
            utilisateur.save()
        return utilisateur


class FormulaireConnexion(forms.Form):
    """Formulaire de connexion simple (identifiant + mot de passe)."""

    identifiant = forms.CharField(label="Identifiant ou e-mail", max_length=150)
    mot_de_passe = forms.CharField(label="Mot de passe", widget=forms.PasswordInput)


class FormulaireProfil(forms.ModelForm):
    """Mise à jour des informations personnelles du client."""

    class Meta:
        model = Utilisateur
        fields = ["first_name", "last_name", "email", "telephone", "adresse"]
        labels = {
            "first_name": "Prénom",
            "last_name": "Nom",
            "email": "E-mail",
            "telephone": "Téléphone",
            "adresse": "Adresse",
        }


class FormulaireBeneficiaire(forms.ModelForm):
    class Meta:
        model = Beneficiaire
        fields = ["nom_complet", "type_beneficiaire", "numero_compte", "operateur", "surnom"]
        labels = {
            "nom_complet": "Nom complet du bénéficiaire",
            "type_beneficiaire": "Type de bénéficiaire",
            "numero_compte": "Numéro de compte ou numéro mobile money",
            "operateur": "Opérateur mobile money (si applicable)",
            "surnom": "Surnom (optionnel)",
        }

    def clean(self):
        donnees = super().clean()
        type_beneficiaire = donnees.get("type_beneficiaire")
        if type_beneficiaire == Beneficiaire.Type.MOBILE_MONEY and not donnees.get("operateur"):
            raise ValidationError({"operateur": "Sélectionnez l'opérateur du portefeuille mobile money."})
        if type_beneficiaire == Beneficiaire.Type.BANCAIRE:
            donnees["operateur"] = ""
        return donnees


class FormulaireDemandeReinitialisation(forms.Form):
    """Demande d'envoi d'un lien de réinitialisation par e-mail."""

    email = forms.EmailField(label="Votre adresse e-mail")
