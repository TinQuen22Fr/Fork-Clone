"""TOTP (Google Authenticator) : génération de secret, QR code et vérification.

Le secret est lu depuis la variable d'environnement TOTP_SECRET (base32).
Aucun secret n'est écrit dans le dépôt.
"""

from __future__ import annotations

import base64
import io
import os

import pyotp
import qrcode

TOTP_ISSUER = os.getenv("TOTP_ISSUER", "The Forge")
TOTP_ACCOUNT = os.getenv("TOTP_ACCOUNT", "forge-admin")


def generate_secret() -> str:
    """Retourne un nouveau secret base32 (160 bits)."""
    return pyotp.random_base32()


def get_secret() -> str | None:
    """Secret TOTP configuré, ou None si absent."""
    value = (os.getenv("TOTP_SECRET") or "").strip()
    return value or None


def provisioning_uri(secret: str, account: str = TOTP_ACCOUNT) -> str:
    """URI otpauth:// à scanner dans Google Authenticator."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=TOTP_ISSUER)


def qr_code_png_base64(uri: str) -> str:
    """QR code PNG de l'URI, encodé en base64 (pour affichage direct)."""
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def verify_code(code: str, secret: str | None = None) -> bool:
    """Vérifie un code à 6 chiffres. Tolère une fenêtre de ±1 pas (30 s)."""
    secret = secret or get_secret()
    if not secret:
        return False
    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)
