"""2FA TOTP pour The Forge : secret auto-généré, cookie de confiance 30 jours,
routes /auth/setup-2fa et /auth/verify-2fa.

Ce module ne dépend pas du JWT d'authentification courant : il réutilise seulement
les helpers existants (settings, create_access_token, set_auth_cookie, get_current_user,
get_db) qui lui sont injectés à l'enregistrement.
"""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

from totp import generate_secret, get_secret, provisioning_uri, qr_code_png_base64, verify_code

# --- Constantes de configuration -------------------------------------------------
PENDING_COOKIE = "forge_2fa_pending"
TRUST_COOKIE = "forge_2fa_trust"
PENDING_TTL_SECONDS = 300          # 5 minutes pour saisir le code
TRUST_TTL_DAYS = 30              # validité du cookie de confiance


# --- Helpers .env -------------------------------------------------------------

def _env_path() -> Path:
    """Chemin du fichier .env (surchargeable via ENV_FILE)."""
    override = os.getenv("ENV_FILE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / ".env"


def _read_env_lines() -> list[str]:
    path = _env_path()
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _write_env_var(key: str, value: str) -> None:
    """Écrit ou remplace une variable dans .env sans toucher au reste du fichier.

    Si la clé existe déjà (même vide), sa valeur est remplacée sur place.
    Sinon elle est ajoutée en fin de fichier. Le fichier est créé si absent.
    Une erreur d'écriture disque (permissions, FS en lecture seule) est
    ignorée : le secret reste actif en mémoire pour la session courante.
    """
    path = _env_path()
    lines = _read_env_lines()
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if pattern.match(line):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    try:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError:
        pass


def ensure_totp_secret() -> str:
    """Retourne le TOTP_SECRET existant, sinon le génère une fois et l'écrit dans .env.

    La valeur générée n'est jamais affichée ni journalisée.
    """
    existing = (os.getenv("TOTP_SECRET") or "").strip()
    if existing:
        return existing
    secret = generate_secret()
    _write_env_var("TOTP_SECRET", secret)
    os.environ["TOTP_SECRET"] = secret
    return secret


# --- Jetons 2FA (pending / trust) -------------------------------------------

def _encode_token(settings, subject: str, ttl_seconds: int, **extra) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_token(settings, token: str) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except Exception:
        return None


def _set_cookie(response, settings, name: str, token: str, max_age: int) -> None:
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )


def _clear_cookie(response, settings, name: str) -> None:
    response.delete_cookie(
        key=name,
        path="/",
        domain=settings.cookie_domain,
    )


def _trust_is_valid(settings, request) -> bool:
    """Vrai si le navigateur présente un cookie de confiance 2FA valide."""
    payload = _decode_token(settings, request.cookies.get(TRUST_COOKIE))
    if not payload:
        return False
    return payload.get("scope") == "trust"


def _user_from_pending(settings, request) -> dict | None:
    """Charge l'identité depuis le cookie pending émis par login()."""
    payload = _decode_token(settings, request.cookies.get(PENDING_COOKIE))
    if not payload or payload.get("scope") != "pending":
        return None
    return {"id": payload.get("uid"), "email": payload.get("email")}


def issue_pending_cookie(response, settings, user: dict) -> None:
    """Émet le cookie pending (5 min) après un mot de passe valide."""
    token = _encode_token(
        settings,
        user["id"],
        PENDING_TTL_SECONDS,
        uid=user["id"],
        email=user["email"],
        scope="pending",
    )
    _set_cookie(response, settings, PENDING_COOKIE, token, PENDING_TTL_SECONDS)


def register_2fa_routes(
    api_router,
    *,
    settings,
    get_db,
    get_current_user,
    create_access_token,
    set_auth_cookie,
) -> None:
    """Enregistre /auth/setup-2fa et /auth/verify-2fa sur le routeur existant.

    Toutes les dépendances sont injectées pour éviter tout import circulaire
    avec server.py.
    """
    from fastapi import Depends, HTTPException, Request, Response
    from pydantic import BaseModel

    class Verify2FARequest(BaseModel):
        code: str

    class Setup2FAResponse(BaseModel):
        otpauth_uri: str
        qr_code_png_base64: str

    @api_router.post("/auth/setup-2fa", response_model=Setup2FAResponse)
    async def setup_2fa(current_user: dict = Depends(get_current_user)):
        """Enrôlement : disponible seulement pour un utilisateur déjà authentifié.

        Génère TOTP_SECRET une seule fois si absent, puis renvoie l'URI otpauth
        et le QR code. Le secret brut n'est jamais renvoyé ni journalisé.
        """
        secret = ensure_totp_secret()
        uri = provisioning_uri(secret)
        return Setup2FAResponse(
            otpauth_uri=uri,
            qr_code_png_base64=qr_code_png_base64(uri),
        )

    @api_router.post("/auth/verify-2fa")
    async def verify_2fa(
        payload: Verify2FARequest,
        request: Request,
        response: Response,
    ):
        """Deux usages : confirmer l'enrôlement (JWT) ou finaliser le login (pending).

        1. JWT valide présent -> simple confirmation du code, aucun cookie émis.
        2. Cookie pending présent -> émission du JWT + cookie de confiance 30 jours.
        """
        secret = get_secret()
        if not secret:
            raise HTTPException(status_code=400, detail="2FA is not configured")

        if not verify_code(payload.code, secret):
            raise HTTPException(status_code=401, detail="Invalid 2FA code")

        # Cas 1 : confirmation d'enrôlement par un utilisateur authentifié.
        authenticated = _decode_token(settings, request.cookies.get(settings.cookie_name))
        if authenticated:
            return {"ok": True, "enrolled": True}

        # Cas 2 : finalisation du login depuis le cookie pending.
        pending = _user_from_pending(settings, request)
        if not pending:
            raise HTTPException(status_code=401, detail="Authentication required")

        database = get_db()
        email = (pending.get("email") or "").lower().strip()
        user = await database.users.find_one({"email": email})
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")

        token = create_access_token(user["id"], user["email"])
        set_auth_cookie(response, token)

        # Cookie de confiance 30 jours : ce navigateur ne repassera pas par le 2FA.
        trust_ttl = TRUST_TTL_DAYS * 86400
        trust = _encode_token(
            settings,
            user["id"],
            trust_ttl,
            uid=user["id"],
            email=user["email"],
            scope="trust",
        )
        _set_cookie(response, settings, TRUST_COOKIE, trust, trust_ttl)
        _clear_cookie(response, settings, PENDING_COOKIE)

        return {
            "id": user["id"],
            "email": user["email"],
            "name": user.get("name"),
            "role": user.get("role", "user"),
            "token": token,
        }
