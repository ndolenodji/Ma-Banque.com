"""Formulaires du back-office administrateur."""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError

from comptes.models import Utilisateur
from devises.models import Devise
from transactions.models import CompteBancaire


class FormulaireCreationClient(UserCreationForm):
    """Création d'un client et de son compte bancaire initial par un admin."""

    email = forms.EmailField(required=True, label="Adresse e-mail")
    telephone = forms.CharField(
        required=True,
        max_length=20,
        label="Numéro de téléphone",
        help_text="Utilisé pour le paiement mobile.",
    )
    numero_piece_identite = forms.CharField(
        required=False, max_length=50, label="Numéro de pièce d'identité (optionnel)"
    )
    type_compte = forms.ChoiceField(
        choices=CompteBancaire.TypeCompte.choices,
        initial=CompteBancaire.TypeCompte.COURANT,
        label="Type du premier compte",
    )
    devise = forms.ModelChoiceField(
        queryset=Devise.objects.filter(est_active=True),
        label="Devise du compte",
    )

    class Meta:
        model = Utilisateur
        fields = [
            "first_name",
            "last_name",
            "username",
            "email",
            "telephone",
            "numero_piece_identite",
        ]
        labels = {
            "first_name": "Prénom",
            "last_name": "Nom",
            "username": "Identifiant de connexion",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["devise"].initial = Devise.objects.filter(code="XAF").values_list("pk", flat=True).first()

    def clean_telephone(self):
        telephone = self.cleaned_data["telephone"].replace(" ", "")
        if not telephone.isdigit() or len(telephone) < 8:
            raise ValidationError("Numéro de téléphone invalide.")
        if Utilisateur.objects.filter(telephone=telephone).exists():
            raise ValidationError("Ce numéro de téléphone est déjà utilisé par un autre compte.")
        return telephone

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if Utilisateur.objects.filter(email__iexact=email).exists():
            raise ValidationError("Cette adresse e-mail est déjà utilisée.")
        return email

    def save(self, commit=True):
        utilisateur = super().save(commit=False)
        utilisateur.role = Utilisateur.Role.CLIENT
        utilisateur.email = self.cleaned_data["email"]
        utilisateur.telephone = self.cleaned_data["telephone"]
        utilisateur.numero_piece_identite = self.cleaned_data["numero_piece_identite"]
        if commit:
            utilisateur.save()
        return utilisateur
