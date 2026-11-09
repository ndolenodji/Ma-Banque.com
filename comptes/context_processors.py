"""
Context processor global : rend disponible le nombre de notifications non
lues dans tous les templates (badge de la cloche, cf. maquettes).
"""

from .models import Notification


def notifications_non_lues(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "notifications_non_lues": Notification.objects.filter(
            destinataire=request.user, lue=False
        ).count()
    }
