"""Formulaires de demande et de pilotage des cartes de démonstration."""

from django import forms

from transactions.models import CompteBancaire

from .models import CarteBancaire


class FormulaireDemandeCarte(forms.Form):
    compte = forms.ModelChoiceField(
        queryset=CompteBancaire.objects.none(),
        label="Compte support",
        help_text="La carte est rattachée à ce compte ; aucun numéro de carte réel n'est généré.",
    )
    type_carte = forms.ChoiceField(
        choices=CarteBancaire.TypeCarte.choices,
        label="Type de carte",
        initial=CarteBancaire.TypeCarte.VIRTUELLE,
    )
    nom_titulaire = forms.CharField(max_length=120, label="Nom inscrit sur la carte")

    def __init__(self, *args, utilisateur=None, **kwargs):
        super().__init__(*args, **kwargs)
        if utilisateur is not None:
            self.fields["compte"].queryset = utilisateur.comptes_bancaires.filter(
                statut=CompteBancaire.Statut.ACTIF
            )
            if not self.is_bound:
                self.fields["nom_titulaire"].initial = (
                    utilisateur.get_full_name() or utilisateur.username
                )


class FormulaireReglagesCarte(forms.ModelForm):
    class Meta:
        model = CarteBancaire
        fields = [
            "paiements_en_ligne",
            "paiements_internationaux",
            "retraits_autorises",
            "plafond_journalier",
            "plafond_mensuel",
        ]
        labels = {
            "paiements_en_ligne": "Paiements en ligne",
            "paiements_internationaux": "Paiements internationaux",
            "retraits_autorises": "Retraits autorisés",
            "plafond_journalier": "Plafond journalier",
            "plafond_mensuel": "Plafond mensuel",
        }

    def clean(self):
        donnees = super().clean()
        journalier = donnees.get("plafond_journalier")
        mensuel = donnees.get("plafond_mensuel")
        if journalier is not None and journalier <= 0:
            raise forms.ValidationError("Le plafond journalier doit être positif.")
        if mensuel is not None and mensuel <= 0:
            raise forms.ValidationError("Le plafond mensuel doit être positif.")
        if journalier is not None and mensuel is not None and journalier > mensuel:
            raise forms.ValidationError(
                "Le plafond journalier ne peut pas dépasser le plafond mensuel."
            )
        return donnees
