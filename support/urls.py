from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("faq/", views.faq, name="faq"),
    path("tickets/", views.mes_tickets, name="mes_tickets"),
    path("tickets/nouveau/", views.creer_ticket, name="creer_ticket"),
    path("tickets/<int:ticket_id>/", views.detail_ticket, name="detail_ticket"),
    path("tickets/<int:ticket_id>/cloturer/", views.cloturer_ticket, name="cloturer_ticket"),
]
