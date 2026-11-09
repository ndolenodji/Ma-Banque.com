"""Formulaires du support client."""

from django import forms

from .models import TicketSupport


class FormulaireTicket(forms.Form):
    sujet = forms.CharField(max_length=180, label="Sujet")
    categorie = forms.ChoiceField(
        choices=TicketSupport.Categorie.choices, initial=TicketSupport.Categorie.AUTRE, label="Catégorie"
    )
    message = forms.CharField(
        label="Décrivez votre demande",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Soyez aussi précis que possible : dates, montants, références…"}),
    )


class FormulaireReponseTicket(forms.Form):
    message = forms.CharField(
        label="Votre message",
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    note_interne = forms.BooleanField(
        required=False,
        label="Note interne (visible uniquement par le support)",
    )
    statut = forms.ChoiceField(
        required=False, choices=TicketSupport.Statut.choices, label="Statut du ticket"
    )
    priorite = forms.ChoiceField(
        required=False, choices=TicketSupport.Priorite.choices, label="Priorité"
    )

    def __init__(self, *args, personnel=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.personnel = personnel
        if not personnel:
            for champ in ("note_interne", "statut", "priorite"):
                self.fields.pop(champ, None)
