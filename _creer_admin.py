import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "banque_app.settings")

import django

django.setup()

from comptes.models import TentativeConnexion, Utilisateur

supprimes = TentativeConnexion.objects.filter(identifiant="admin").delete()
print("tentatives_admin_supprimees", supprimes)

admin = Utilisateur.objects.filter(username="admin").first()
if admin:
    admin.set_password("AdminBanque2026!")
    admin.role = Utilisateur.Role.ADMIN
    admin.is_superuser = True
    admin.is_staff = True
    admin.is_active = True
    admin.est_actif_bancaire = True
    if not admin.telephone:
        admin.telephone = "66000001"
    admin.save()
    print("action=reset")
else:
    Utilisateur.objects.create_superuser(
        username="admin",
        email="admin@mabanque.td",
        password="AdminBanque2026!",
        first_name="Awada",
        last_name="Directrice",
        telephone="66000001",
        role=Utilisateur.Role.ADMIN,
    )
    print("action=create")

for u in Utilisateur.objects.all():
    print(u.id, u.username, u.role, u.is_superuser, u.telephone)
