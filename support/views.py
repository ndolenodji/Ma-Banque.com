"""Vues du support client : FAQ, tickets et fil de messages."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from comptes.models import JournalAudit
from comptes.services_notifications import notifier, notifier_personnel

from .forms import FormulaireReponseTicket, FormulaireTicket
from .models import ArticleFAQ, MessageSupport, TicketSupport


def faq(request):
    recherche = (request.GET.get("q") or "").strip()
    categorie = (request.GET.get("categorie") or "").strip()
    articles = ArticleFAQ.objects.filter(publie=True)
    if recherche:
        articles = articles.filter(Q(question__icontains=recherche) | Q(reponse__icontains=recherche))
    if categorie in ArticleFAQ.Categorie.values:
        articles = articles.filter(categorie=categorie)

    groupes = []
    for code, libelle in ArticleFAQ.Categorie.choices:
        elements = [article for article in articles if article.categorie == code]
        if elements:
            groupes.append({"code": code, "libelle": libelle, "articles": elements})
    return render(
        request,
        "support/faq.html",
        {
            "groupes": groupes,
            "categories": ArticleFAQ.Categorie.choices,
            "recherche": recherche,
            "categorie": categorie,
        },
    )


def _est_personnel(utilisateur):
    return utilisateur.est_administrateur() or utilisateur.est_agent()


@login_required
def mes_tickets(request):
    personnel = _est_personnel(request.user)
    tickets = TicketSupport.objects.select_related("client", "assigne_a").annotate(
        nb_messages=Count("messages")
    )
    if not personnel:
        tickets = tickets.filter(client=request.user)

    statut = (request.GET.get("statut") or "").strip()
    categorie = (request.GET.get("categorie") or "").strip()
    recherche = (request.GET.get("q") or "").strip()
    if statut in TicketSupport.Statut.values:
        tickets = tickets.filter(statut=statut)
    if categorie in TicketSupport.Categorie.values:
        tickets = tickets.filter(categorie=categorie)
    if recherche:
        tickets = tickets.filter(
            Q(reference__icontains=recherche)
            | Q(sujet__icontains=recherche)
            | Q(client__username__icontains=recherche)
        )

    return render(
        request,
        "support/mes_tickets.html",
        {
            "tickets": tickets.order_by("-date_mise_a_jour"),
            "est_personnel": personnel,
            "statuts": TicketSupport.Statut.choices,
            "categories": TicketSupport.Categorie.choices,
            "filtres": {"statut": statut, "categorie": categorie, "recherche": recherche},
        },
    )


@login_required
def creer_ticket(request):
    if request.method == "POST":
        formulaire = FormulaireTicket(request.POST)
        if formulaire.is_valid():
            ticket = TicketSupport.objects.create(
                client=request.user,
                sujet=formulaire.cleaned_data["sujet"],
                categorie=formulaire.cleaned_data["categorie"],
            )
            MessageSupport.objects.create(
                ticket=ticket,
                auteur=request.user,
                message=formulaire.cleaned_data["message"],
            )
            JournalAudit.journaliser(request.user, "Ouverture ticket support", f"{ticket.reference} - {ticket.sujet}")
            notifier(
                request.user,
                "Ticket support ouvert",
                f"Votre ticket {ticket.reference} a bien été enregistré. "
                "Le support vous répondra dans les meilleurs délais.",
                type_notification="SUPPORT",
                niveau="INFO",
                lien=f"/support/tickets/{ticket.pk}/",
            )
            notifier_personnel(
                "Nouveau ticket support",
                f"{ticket.reference} — {ticket.sujet} (client : {request.user}).",
                niveau="ATTENTION",
                lien=f"/support/tickets/{ticket.pk}/",
                roles=("ADMIN", "AGENT"),
            )
            messages.success(request, "Votre ticket a été créé.")
            return redirect("support:detail_ticket", ticket_id=ticket.pk)
    else:
        formulaire = FormulaireTicket()

    return render(request, "support/creer_ticket.html", {"formulaire": formulaire})


@login_required
def detail_ticket(request, ticket_id):
    ticket = get_object_or_404(
        TicketSupport.objects.select_related("client", "assigne_a"), pk=ticket_id
    )
    personnel = _est_personnel(request.user)
    if not personnel and ticket.client_id != request.user.id:
        messages.error(request, "Ce ticket ne vous appartient pas.")
        return redirect("support:mes_tickets")

    messages_ticket = ticket.messages.select_related("auteur")
    if not personnel:
        messages_ticket = messages_ticket.filter(est_note_interne=False)

    formulaire = FormulaireReponseTicket(
        personnel=personnel,
        initial={
            "statut": ticket.statut,
            "priorite": ticket.priorite,
        },
    )
    if request.method == "POST":
        if not personnel and not ticket.est_ouvert:
            messages.error(request, "Ce ticket est clôturé : vous ne pouvez plus y répondre.")
            return redirect("support:detail_ticket", ticket_id=ticket.pk)
        formulaire = FormulaireReponseTicket(request.POST, personnel=personnel)
        if formulaire.is_valid():
            note_interne = personnel and formulaire.cleaned_data.get("note_interne", False)
            MessageSupport.objects.create(
                ticket=ticket,
                auteur=request.user,
                message=formulaire.cleaned_data["message"],
                est_note_interne=note_interne,
            )
            if personnel:
                ticket.assigne_a = request.user
                nouveau_statut = formulaire.cleaned_data.get("statut") or ticket.statut
                nouvelle_priorite = formulaire.cleaned_data.get("priorite") or ticket.priorite
                ticket.statut = nouveau_statut
                ticket.priorite = nouvelle_priorite
                if nouveau_statut in (TicketSupport.Statut.RESOLU, TicketSupport.Statut.FERME):
                    ticket.date_cloture = ticket.date_cloture or timezone.now()
                else:
                    ticket.date_cloture = None
                if formulaire.cleaned_data.get("statut"):
                    JournalAudit.journaliser(
                        request.user,
                        "Mise à jour ticket support",
                        f"{ticket.reference} - statut {nouveau_statut}",
                    )
            elif ticket.statut == TicketSupport.Statut.OUVERT:
                ticket.statut = TicketSupport.Statut.EN_COURS
            ticket.save(
                update_fields=[
                    "statut",
                    "priorite",
                    "assigne_a",
                    "date_cloture",
                    "date_mise_a_jour",
                ]
            )
            if not note_interne:
                if personnel:
                    notifier(
                        ticket.client,
                        "Réponse du support",
                        f"Une réponse a été apportée à votre ticket {ticket.reference}.",
                        type_notification="SUPPORT",
                        niveau="INFO",
                        lien=f"/support/tickets/{ticket.pk}/",
                    )
                else:
                    if ticket.assigne_a_id:
                        notifier(
                            ticket.assigne_a,
                            "Nouveau message client",
                            f"{request.user} a répondu au ticket {ticket.reference}.",
                            type_notification="SUPPORT",
                            niveau="ATTENTION",
                            lien=f"/support/tickets/{ticket.pk}/",
                        )
                    else:
                        notifier_personnel(
                            "Nouveau message client",
                            f"Le ticket {ticket.reference} a reçu un message client.",
                            niveau="ATTENTION",
                            lien=f"/support/tickets/{ticket.pk}/",
                            roles=("ADMIN", "AGENT"),
                        )
            messages.success(request, "Votre message a été publié.")
            return redirect("support:detail_ticket", ticket_id=ticket.pk)

    return render(
        request,
        "support/detail_ticket.html",
        {
            "ticket": ticket,
            "messages_ticket": messages_ticket,
            "formulaire": formulaire,
            "est_personnel": personnel,
            "statuts": TicketSupport.Statut.choices,
            "priorites": TicketSupport.Priorite.choices,
        },
    )


@login_required
@require_POST
def cloturer_ticket(request, ticket_id):
    ticket = get_object_or_404(TicketSupport, pk=ticket_id)
    personnel = _est_personnel(request.user)
    if not personnel and ticket.client_id != request.user.id:
        messages.error(request, "Ce ticket ne vous appartient pas.")
        return redirect("support:mes_tickets")
    if not ticket.est_ouvert:
        messages.info(request, "Ce ticket est déjà clôturé.")
    else:
        ticket.statut = TicketSupport.Statut.FERME
        ticket.date_cloture = timezone.now()
        ticket.save(update_fields=["statut", "date_cloture", "date_mise_a_jour"])
        if personnel:
            JournalAudit.journaliser(
                request.user, "Clôture ticket support", f"{ticket.reference}"
            )
        messages.success(request, "Le ticket a été clôturé.")
    return redirect("support:detail_ticket", ticket_id=ticket.pk)
