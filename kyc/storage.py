"""Stockage privé des justificatifs KYC, non exposé par /media/."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage

stockage_prive_kyc = FileSystemStorage(
    location=str(settings.PRIVATE_STORAGE_ROOT / "kyc"),
    base_url=None,
)
