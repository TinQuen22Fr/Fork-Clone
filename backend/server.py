# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
#
# Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
# modifier selon les termes de la GNU General Public License telle que publiée
# par la Free Software Foundation, soit la version 3, soit (à votre choix) toute
# version ultérieure. Il est distribué SANS AUCUNE GARANTIE.
# Voir le fichier LICENSE ou <https://www.gnu.org/licenses/>.

"""
Claude Unchained Forge — backend FastAPI.

Points clés de cette version :
- Configuration tolérante : aucune variable d'environnement n'est lue avec
  os.environ[...] au niveau module, donc l'import ne casse jamais. La validation
  se fait au démarrage (lifespan) avec des messages explicites.
- CORS strict : origines listées explicitement, jamais "*" avec credentials.
- Cookies configurables (secure / samesite / domain) selon le déploiement.
- chat_send : la génération de réponse est isolée dans generate_ai_response(),
  actuellement un mock. Il suffira de remplacer le corps de cette fonction.
"""

from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import io
import re
import json
import time
from html import unescape
import asyncio
import base64
import shlex
import shutil
import subprocess
import tempfile
import zipfile
import uuid
import logging
import secrets
import socket
import ipaddress
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from urllib.parse import urlparse, urljoin
from contextlib import asynccontextmanager
from contextvars import ContextVar

_current_provider: ContextVar[str] = ContextVar("current_provider", default="claude")

def set_current_provider(pid: str):
    _current_provider.set(pid or "claude")

# --- Selecteur de modele Claude -------------------------------------------
# Le jeton OAuth d'abonnement donne acces a plusieurs modeles. Cette liste sert
# de REPLI : le catalogue reel est decouvert dynamiquement via l'endpoint
# Anthropic GET /v1/models (qui accepte le Bearer OAuth d'abonnement), avec
# cache et retour automatique a cette liste statique en cas d'echec.
# `model_override` choisi cote UI est propage via ce ContextVar, sans toucher
# au defaut configure dans CLAUDE_MODEL.
CLAUDE_MODELS: tuple[str, ...] = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-4-5-20251001",
    "claude-fable-5-1",
    "claude-opus-4-1",
    "claude-sonnet-4-6",
)

_current_claude_model: ContextVar[str] = ContextVar("current_claude_model", default="")

def _resolve_claude_model() -> str:
    """Modele Claude a utiliser : override du tour si present, sinon defaut."""
    return _current_claude_model.get() or settings.claude_model
from datetime import datetime, timezone, timedelta
from typing import Optional, AsyncIterator

# --- Item 8 : effort de raisonnement (reasoning_effort) -------------------
# Valeurs acceptees par les API compatibles OpenAI (gpt-oss, o-series, etc.).
_REASONING_EFFORTS = ("low", "medium", "high")
# Familles de modeles qui honorent `reasoning_effort`. Envoyer ce parametre a
# un modele qui ne le supporte pas provoque un 400 : on ne l'injecte donc que
# pour les modeles explicitement compatibles ("selon le modele").
_REASONING_MODEL_RE = re.compile(
    r"(gpt-oss|gpt-5|gpt-4\.1|o[134](?:-|$)|qwen3|deepseek-r1|"
    r"reasoning|thinking|glm-4\.\d|magistral)",
    re.I,
)
# Override explicite du tour courant, pose par les routes /chat/* (ContextVar :
# le patron deja utilise pour le provider/projet, sans alourdir les signatures).
_current_reasoning_effort: ContextVar[str] = ContextVar(
    "current_reasoning_effort", default=""
)


def set_current_reasoning_effort(value: Optional[str]) -> None:
    """Pose l'effort de raisonnement demande pour le tour courant (vide = auto)."""
    _current_reasoning_effort.set((value or "").strip().lower())


def _resolve_reasoning_effort(model: str) -> str:
    """Determine le `reasoning_effort` a envoyer pour un modele donne.

    - Rien si le modele ne fait pas partie des familles compatibles ;
    - la valeur demandee par l'utilisateur si elle est valide (low/medium/high) ;
    - sinon `medium` comme defaut raisonnable pour les modeles compatibles.
    """
    if not model or not _REASONING_MODEL_RE.search(model):
        return ""
    requested = _current_reasoning_effort.get()
    if requested in _REASONING_EFFORTS:
        return requested
    if requested == "off":
        return ""
    return "medium"

import bcrypt
import jwt
import httpx
from fastapi import (
    FastAPI,
    APIRouter,
    HTTPException,
    Request,
    Response,
    Depends,
    Query,
    UploadFile,
    File,
    Form,
)
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse, FileResponse
from starlette.background import BackgroundTask
from motor.motor_asyncio import AsyncIOMotorClient

from preview_runtime import PreviewManager
from pydantic import BaseModel, EmailStr, Field


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("forge")


# =========================================================================
# Configuration
# =========================================================================
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")

def _normalize_base_url(raw: str, *, assume_v1: bool = True) -> str:
    """Normalise une URL de base compatible OpenAI.

    - supprime les espaces et les slashs de fin ;
    - retire un suffixe deja complet ("/chat/completions", "/completions") ;
    - ajoute le segment de version ("/v1") s'il est absent, sauf si l'URL
      se termine deja par un segment de version ("/v1", "/v2", "/v1beta"…).

    Aucune modification du comportement existant quand l'URL est deja propre.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    low = url.lower()
    # URL deja complete : on retire l'endpoint pour ne garder que la base.
    for suffix in ("/chat/completions", "/completions"):
        if low.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            low = url.lower()
            break
    if not assume_v1:
        return url
    # Un segment de version est-il deja present en fin d'URL ?
    last = url.rsplit("/", 1)[-1].lower()
    if last.startswith("v") and any(ch.isdigit() for ch in last):
        return url
    return url + "/v1"



class Settings:
    """Lecture non bloquante de l'environnement. Validation au démarrage."""

    def __init__(self) -> None:
        # --- Sécurité / JWT ---
        self.jwt_secret: str = _env("JWT_SECRET")
        self.jwt_algorithm: str = "HS256"
        self.jwt_expire_days: int = int(_env("JWT_EXPIRE_DAYS", "7"))

        # --- Base de données ---
        self.mongo_url: str = _env("MONGO_URL")
        self.db_name: str = _env("DB_NAME", "forge")

        # --- Frontend / CORS ---
        # Accepte une ou plusieurs origines séparées par des virgules.
        self.frontend_urls: list[str] = [
            u.strip().rstrip("/")
            for u in _env("FRONTEND_URL").split(",")
            if u.strip()
        ]

        # --- Cookies ---
        # En HTTPS derrière Nginx : secure=True. En HTTP local : secure=False.
        self.cookie_secure: bool = _env_bool("COOKIE_SECURE", True)
        # "lax" si front et back partagent le domaine, "none" si cross-site.
        self.cookie_samesite: str = _env("COOKIE_SAMESITE", "lax").lower()
        self.cookie_domain: Optional[str] = _env("COOKIE_DOMAIN") or None
        # Nom du cookie de session, paramétrable pour isoler les jetons entre
        # une instance hôte et une instance sandboxée (preview de sous-projet)
        # qui partageraient le même domaine cookie.
        self.cookie_name: str = _env("COOKIE_NAME", "access_token")

        # --- Compte admin initial ---
        self.admin_email: str = _env("ADMIN_EMAIL", "admin@forge.dev").lower()
        self.admin_password: str = _env("ADMIN_PASSWORD")

        # --- Inscription publique ---
        # Faux par défaut : une instance exposée sur Internet ne doit pas
        # laisser n'importe qui se créer un compte. Passer à true seulement
        # si l'ouverture est voulue.
        self.allow_registration: bool = _env_bool("ALLOW_REGISTRATION", False)

        # --- Divers ---
        self.max_image_mb: int = int(_env("MAX_IMAGE_MB", "8"))
        self.max_upload_mb: int = int(_env("MAX_UPLOAD_MB", "16"))
        # Nb max de caracteres extraits d'un fichier texte/PDF injecte au prompt.
        self.max_file_chars: int = int(_env("MAX_FILE_CHARS", "40000"))
        self.max_attachments: int = int(_env("MAX_ATTACHMENTS", "10"))

        # --- Outils web de l'agent ---
        # Google Custom Search est optionnel : si ces deux variables sont
        # renseignees, il prend le pas sur DuckDuckGo (qui ne demande aucune cle).
        self.google_cse_key: str = _env("GOOGLE_CSE_KEY")
        self.google_cse_cx: str = _env("GOOGLE_CSE_CX")
        self.fetch_url_max_chars: int = int(_env("FETCH_URL_MAX_CHARS", "12000"))
        # Chromium headless est lourd : desactivable sur petite machine.
        self.enable_screenshot: bool = _env_bool("ENABLE_SCREENSHOT", True)
        self.screenshot_retention_hours: int = int(
            _env("SCREENSHOT_RETENTION_HOURS", "48")
        )

        # --- Transcription vocale (repli quand le navigateur ne sait pas faire) ---
        # Utilise la cle Groq deja configuree ; endpoint compatible OpenAI.
        self.stt_model: str = _env("STT_MODEL", "whisper-large-v3-turbo")
        self.stt_max_mb: int = int(_env("STT_MAX_MB", "20"))
        self.history_turns: int = int(_env("HISTORY_TURNS", "20"))

        # --- Providers cloud gratuits, compatibles OpenAI -------------------
        # Aucune cle ni aucun modele en dur : tout vient du .env et de la
        # decouverte dynamique via GET {base_url}/models.
        self.free_providers: dict[str, dict] = {}
        for pid, label, default_base in (
            ("groq", "Groq", "https://api.groq.com/openai/v1"),
            ("cerebras", "Cerebras", "https://api.cerebras.ai/v1"),
            ("sambanova", "SambaNova", "https://api.sambanova.ai/v1"),
            ("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1"),
            ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
            # Provider proxy generique : n'importe quelle passerelle compatible
            # OpenAI (proxies locaux, Freebuff, …). PROXY_BASE_URL est obligatoire,
            # PROXY_API_KEY et PROXY_MODEL optionnels (decouverte /models sinon).
            ("proxy", "Proxy (OpenAI)", ""),
        ):
            up = pid.upper()
            self.free_providers[pid] = {
                "label": label,
                "base_url": _normalize_base_url(
                    _env(f"{up}_BASE_URL", default_base),
                    assume_v1=(pid == "proxy"),
                ),
                "api_key": _env(f"{up}_API_KEY"),
                # Vide = premier modele decouvert dynamiquement.
                "model": _env(f"{up}_MODEL"),
                "timeout": float(_env(f"{up}_TIMEOUT", "180")),
            }
        # OpenRouter recommande de s'identifier (facultatif).
        self.openrouter_referer: str = _env("OPENROUTER_HTTP_REFERER")
        self.openrouter_title: str = _env("OPENROUTER_TITLE", "Claude Unchained Forge")
        # OpenRouter : ne garder que les modeles gratuits (id contenant ":free").
        self.openrouter_free_only: bool = _env_bool("OPENROUTER_FREE_ONLY", True)

        # --- Resume automatique de l'historique long ------------------------
        self.summary_enabled: bool = _env_bool("HISTORY_SUMMARY_ENABLED", True)
        # Seuil en tokens estimes (≈ 4 caracteres par token).
        self.summary_threshold_tokens: int = int(
            _env("HISTORY_SUMMARY_THRESHOLD_TOKENS", "6000")
        )
        # Nombre de messages recents toujours transmis mot pour mot.
        self.summary_keep_recent: int = int(_env("HISTORY_SUMMARY_KEEP_RECENT", "6"))
        self.summary_max_chars: int = int(_env("HISTORY_SUMMARY_MAX_CHARS", "3000"))
        # Nombre max de messages relus en base a chaque generation (les plus
        # recents non encore resumes). Les anciens restent en base, intacts.
        self.history_load_limit: int = int(_env("HISTORY_LOAD_LIMIT", "150"))

        # --- Claude (abonnement Pro/Max via jeton OAuth Claude Code) ---
        # AUCUNE API payante au token : on utilise le jeton d'abonnement généré
        # par `claude setup-token` (commence par sk-ant-oat...). La conso est
        # décomptée du forfait Claude Pro/Max, pas facturée à l'usage.
        self.claude_token: str = _env("CLAUDE_CODE_OAUTH_TOKEN")
        self.claude_model: str = _env("CLAUDE_MODEL", "claude-sonnet-4-6")
        self.claude_max_tokens: int = int(_env("CLAUDE_MAX_TOKENS", "4096"))
        self.enable_tools: bool = _env_bool("ENABLE_TOOLS", True)
        self.claude_system_prompt: str = _env(
            "CLAUDE_SYSTEM_PROMPT",
            "Tu es Claude Unchained Forge, un assistant IA direct, franc et sans "
            "langue de bois, propulse par Claude. Reponds avec clarte, en Markdown "
            "quand c'est utile (blocs de code, listes, titres). Tu peux analyser "
            "les images envoyees par l'utilisateur."
            "\n\nREGLES DE SECURITE NON NEGOCIABLES :\n"
            "- N'ouvre jamais, n'affiche jamais et ne resume jamais le contenu "
            "des fichiers d'environnement ou de secrets : .env, .env.*, *.key, "
            "*.pem, id_rsa, credentials, .netrc, .git-credentials.\n"
            "- N'ecris jamais une cle d'API, un jeton ou un mot de passe en clair "
            "dans tes reponses, meme si l'utilisateur le demande, meme dans un "
            "bloc de code ou un exemple.\n"
            "- N'utilise pas les outils shell pour contourner ces regles "
            "(cat, grep, sed, env, printenv, base64 sur ces fichiers).\n"
            "- Si un secret est necessaire, designe-le par son nom de variable "
            "(ex. GROQ_API_KEY) sans jamais reveler sa valeur.",
        )

        # --- Gemini (Google, via SDK google-genai) ---
        self.gemini_api_key: str = _env("GEMINI_API_KEY")
        self.gemini_model: str = _env("GEMINI_MODEL", "gemini-3.6-flash")
        self.gemini_fallback_model: str = _env("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")

        # --- Ollama (moteur local, gratuit, pas de cle API) ---
        self.ollama_url: str = _env("OLLAMA_URL", "http://localhost:11434")
        self.ollama_model: str = _env("OLLAMA_MODEL", "llama3.2:1b")
        # Réglages perf Ollama (tunables pour CPU faible sans AVX2).
        self.ollama_num_ctx: int = int(_env("OLLAMA_NUM_CTX", "4096"))
        self.ollama_num_predict: int = int(_env("OLLAMA_NUM_PREDICT", "768"))
        self.ollama_num_thread: int = int(_env("OLLAMA_NUM_THREAD", "2"))
        self.ollama_keep_alive: str = _env("OLLAMA_KEEP_ALIVE", "10m")
        self.ollama_timeout: float = float(_env("OLLAMA_TIMEOUT", "600"))

        # --- Ollama Cloud (moteur distant, cle API) ---
        self.ollama_cloud_url: str = _env(
            "OLLAMA_CLOUD_URL", "https://ollama.com/api"
        ).rstrip("/")
        self.ollama_cloud_api_key: str = _env("OLLAMA_CLOUD_API_KEY")
        self.ollama_cloud_model: str = _env(
            "OLLAMA_CLOUD_MODEL", "deepseek-v4-pro:0813"
        )
        # Modele de secours interne au meme provider (utile si le principal
        # n'est pas inclus dans l'offre gratuite du compte).
        self.ollama_cloud_fallback_model: str = _env(
            "OLLAMA_CLOUD_FALLBACK_MODEL", "gpt-oss:120b"
        )
        self.ollama_cloud_timeout: float = float(_env("OLLAMA_CLOUD_TIMEOUT", "180"))

        # --- OpenCode Zen / Go (passerelle compatible OpenAI) ---
        self.opencode_base_url: str = _env(
            "OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1"
        ).rstrip("/")
        self.opencode_api_key: str = _env("OPENCODE_API_KEY")
        self.opencode_model: str = _env("OPENCODE_MODEL", "deepseek-v4-flash")
        self.opencode_fallback_model: str = _env(
            "OPENCODE_FALLBACK_MODEL", "glm-5.3-flash"
        )
        self.opencode_timeout: float = float(_env("OPENCODE_TIMEOUT", "180"))
        # OpenCode Go exige un User-Agent identifiable (pas un nom de lib HTTP).
        self.opencode_user_agent: str = _env(
            "OPENCODE_USER_AGENT", "claude-unchained-forge/1.0"
        )
        # auto | chat | messages | responses (auto = deduit de l'id du modele)
        self.opencode_transport: str = _env("OPENCODE_TRANSPORT", "auto").lower()

        # --- Routeur / cascade de bascule ---
        # Ordre de priorite pour le mode auto et pour la cascade. Le provider
        # local `ollama` est TOUJOURS repousse en dernier recours.
        self.provider_priority: list[str] = [
            p.strip()
            for p in _env(
                "PROVIDER_PRIORITY",
                "claude,opencode,groq,cerebras,sambanova,nvidia,openrouter,"
                "gemini,ollama_cloud,ollama",
            ).split(",")
            if p.strip()
        ]
        self.enable_fallback: bool = _env_bool("ENABLE_FALLBACK", True)

        # --- Synthese vocale : 100% gratuit, sans cle, sans quota ---
        # Moteur 1 : Kokoro en local (kokoro-onnx, CPU). Moteur 2 : edge-tts.
        self.tts_engine: str = _env("TTS_ENGINE", "auto").lower()  # auto|kokoro|edge
        self.kokoro_model_path: str = _env(
            "KOKORO_MODEL_PATH", str(ROOT_DIR / "models" / "kokoro-v1.0.onnx")
        )
        self.kokoro_voices_path: str = _env(
            "KOKORO_VOICES_PATH", str(ROOT_DIR / "models" / "voices-v1.0.bin")
        )
        self.kokoro_voice: str = _env("KOKORO_VOICE", "ff_siwis")
        self.kokoro_timeout: int = int(_env("KOKORO_TIMEOUT", "45"))
        self.tts_voice: str = _env("TTS_VOICE", "fr-FR-DeniseNeural")
        self.tts_rate: str = _env("TTS_RATE", "+0%")
        self.tts_pitch: str = _env("TTS_PITCH", "+0Hz")
        self.tts_max_chars: int = int(_env("TTS_MAX_CHARS", "4000"))

        # --- Sauvegarde du workspace sur GitHub ---
        # Jeton personnel (classic ou fine-grained, portee "repo"). Accepte les
        # deux noms de variable ; surchargeable depuis l'interface.
        self.github_pat: str = _env("GITHUB_PAT") or _env("GITHUB_TOKEN")
        # Racine contenant un sous-dossier par projet : /var/www/forge/workspace/<projet>
        self.workspace_root: str = _env(
            "WORKSPACE_ROOT", str(ROOT_DIR.parent / "workspace")
        )
        # Repli mono-projet (dev local) si la racine ci-dessus n'existe pas.
        self.workspace_dir: str = _env("WORKSPACE_DIR", str(ROOT_DIR.parent))
        self.git_author_name: str = _env("GIT_AUTHOR_NAME", "Claude Unchained Forge")
        # Preview automatique : wildcard DNS *.preview.<domaine> -> Dedibox.
        self.preview_domain: str = _env(
            "PREVIEW_DOMAIN_SUFFIX", "preview.quentin-astro.fr"
        ).strip().strip(".")
        self.preview_port_base: int = int(_env("PREVIEW_PORT_BASE", "8090"))
        self.preview_port_max: int = int(_env("PREVIEW_PORT_MAX", "8189"))
        # Regeneration automatique de la map Nginx des sous-domaines de preview.
        self.preview_map_auto_refresh: bool = _env(
            "PREVIEW_MAP_AUTO_REFRESH", "1"
        ).strip().lower() not in ("0", "false", "no", "off", "")
        self.preview_map_refresh_cmd: str = _env(
            "PREVIEW_MAP_REFRESH_CMD",
            "sudo /usr/bin/bash /var/www/forge/deploy/setup-preview-domain.sh --map-only",
        ).strip()
        self.preview_map_refresh_timeout: int = int(
            _env("PREVIEW_MAP_REFRESH_TIMEOUT", "120")
        )
        # Arret automatique d'une preview sans activite (secondes, 0 = jamais).
        self.preview_idle_timeout: int = int(_env("PREVIEW_IDLE_TIMEOUT", "900"))
        # Relance des previews actives au demarrage du backend (OFF par defaut :
        # une preview ne demarre que sur clic explicite de l'utilisateur).
        self.preview_autostart: bool = _env(
            "PREVIEW_AUTOSTART", "0"
        ).strip().lower() not in ("0", "false", "no", "off", "")
        self.git_author_email: str = _env(
            "GIT_AUTHOR_EMAIL", "forge@localhost"
        )

    def validate(self) -> list[str]:
        """Retourne la liste des problèmes bloquants (vide si tout va bien)."""
        problems: list[str] = []

        if not self.mongo_url:
            problems.append(
                "MONGO_URL est absent : impossible de se connecter à MongoDB."
            )
        if not self.frontend_urls:
            problems.append(
                "FRONTEND_URL est absent : le CORS refusera toutes les requêtes "
                "du navigateur. Renseignez l'URL exacte du frontend "
                "(ex. https://forge.quentin-astro.fr)."
            )
        if self.cookie_samesite not in ("lax", "strict", "none"):
            problems.append(
                f"COOKIE_SAMESITE='{self.cookie_samesite}' invalide "
                "(valeurs acceptées : lax, strict, none)."
            )
        if self.cookie_samesite == "none" and not self.cookie_secure:
            problems.append(
                "COOKIE_SAMESITE=none impose COOKIE_SECURE=true "
                "(exigence des navigateurs)."
            )
        return problems


settings = Settings()

# JWT_SECRET manquant : on ne casse pas, mais on génère une clé éphémère et on
# hurle dans les logs. Conséquence : tous les tokens sont invalidés à chaque
# redémarrage. Acceptable en dev, jamais en production.
if not settings.jwt_secret:
    settings.jwt_secret = secrets.token_urlsafe(48)
    logger.warning(
        "JWT_SECRET absent — une clé éphémère a été générée. Les sessions "
        "seront perdues à chaque redémarrage. Définissez JWT_SECRET dans .env."
    )


# =========================================================================
# Base de données (initialisée dans le lifespan)
# =========================================================================
mongo_client: Optional[AsyncIOMotorClient] = None
db = None


def get_db():
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Base de données indisponible. Vérifiez MONGO_URL et les logs.",
        )
    return db


# =========================================================================
# Cycle de vie
# =========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mongo_client, db

    problems = settings.validate()
    for p in problems:
        logger.error("CONFIG: %s", p)

    _purge_old_screenshots()

    if settings.mongo_url:
        try:
            mongo_client = AsyncIOMotorClient(
                settings.mongo_url, serverSelectionTimeoutMS=5000
            )
            await mongo_client.admin.command("ping")
            db = mongo_client[settings.db_name]
            logger.info("MongoDB connecté (base : %s)", settings.db_name)
        except Exception:
            logger.exception(
                "Connexion MongoDB impossible — l'API démarre en mode dégradé."
            )
            db = None

    if db is not None:
        await db.users.create_index("email", unique=True)
        await db.conversations.create_index([("user_id", 1), ("updated_at", -1)])
        await db.messages.create_index([("conversation_id", 1), ("created_at", 1)])
        await db.context_facts.create_index(
            [("user_id", 1), ("project", 1), ("norm", 1)]
        )
        await _seed_admin()
        asyncio.create_task(_autostart_previews())
        asyncio.create_task(_reap_idle_previews())

    logger.info("Origines CORS autorisées : %s", settings.frontend_urls or "(aucune)")
    logger.info(
        "Inscription publique : %s",
        "OUVERTE" if settings.allow_registration else "fermée",
    )
    logger.info(
        "Cookies : secure=%s samesite=%s domain=%s",
        settings.cookie_secure,
        settings.cookie_samesite,
        settings.cookie_domain or "(par défaut)",
    )

    yield

    await preview_mgr().stop_all()
    if mongo_client is not None:
        mongo_client.close()
        logger.info("Connexion MongoDB fermée.")


async def _seed_admin() -> None:
    """Crée le compte admin s'il n'existe pas. Ne réécrit jamais un mot de passe
    existant : un reset se fait explicitement, pas au redémarrage."""
    if not settings.admin_password:
        logger.info("ADMIN_PASSWORD absent — aucun compte admin créé.")
        return

    existing = await db.users.find_one({"email": settings.admin_email})
    if existing is None:
        await db.users.insert_one(
            {
                "id": str(uuid.uuid4()),
                "email": settings.admin_email,
                "password_hash": hash_password(settings.admin_password),
                "name": "Admin",
                "role": "admin",
                "created_at": now_iso(),
            }
        )
        logger.info("Compte admin créé : %s", settings.admin_email)
    else:
        logger.info("Compte admin déjà présent : %s", settings.admin_email)


app = FastAPI(title="Claude Unchained Forge API", lifespan=lifespan)
api_router = APIRouter(prefix="/api")


# =========================================================================
# Modèles
# =========================================================================
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CreateConversationRequest(BaseModel):
    title: Optional[str] = "New Chat"
    project: Optional[str] = None


class CreateProjectRequest(BaseModel):
    name: str
    preview_url: Optional[str] = None


class ProjectUpdateRequest(BaseModel):
    preview_url: Optional[str] = None


class RenameRequest(BaseModel):
    title: str


class RegenerateRequest(BaseModel):
    conversation_id: str
    provider: str = "claude"
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None


class FeedbackRequest(BaseModel):
    feedback: Optional[str] = None  # "up" | "down" | None


# =========================================================================
# Helpers
# =========================================================================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def set_auth_cookie(response: Response, token: str) -> None:
    kwargs = {
        "key": settings.cookie_name,
        "value": token,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "max_age": settings.jwt_expire_days * 24 * 3600,
        "path": "/",
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    response.set_cookie(**kwargs)


def clear_auth_cookie(response: Response) -> None:
    kwargs = {"key": settings.cookie_name, "path": "/"}
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    response.delete_cookie(**kwargs)


async def get_current_user(request: Request) -> dict:
    database = get_db()

    token = request.cookies.get(settings.cookie_name)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user = await database.users.find_one({"id": payload["sub"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    user.pop("password_hash", None)
    user.pop("_id", None)
    return user


# =========================================================================
# Génération de la réponse IA  —  Claude via abonnement (jeton OAuth)
# =========================================================================
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Identité exigée par Anthropic pour les jetons OAuth d'abonnement (Claude Code).
# Le premier bloc system DOIT être exactement cette chaîne, sinon 400/401.
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# --- Tool Calling (capacités agentiques) ---------------------------------
MAX_TOOL_ITERS = 25  # garde-fou contre les boucles d'outils infinies

# Etapes autonomes : une fois une etape validee (outils executes), on
# tronque ses sorties brutes pour ne pas saturer la fenetre de contexte
# envoyee au modele au tour suivant (les sorties completes restent en base).
STEP_LOG_TRUNC = 1200  # caracteres conserves par sortie d'outil validee

# Les captures sont ecrites sur disque et servies par /api/screenshots/{nom}.
SCREENSHOT_DIR = ROOT_DIR / "static" / "screenshots"

TOOLS = [
    {
        "name": "bash",
        "description": (
            "Exécute une commande shell et renvoie le code de sortie, stdout "
            "et stderr. Le répertoire de travail est TOUJOURS la racine du "
            "projet actif du workspace : utilise des chemins relatifs et ne "
            "sors jamais de ce dossier. Timeout de sécurité de 30 secondes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "La commande shell complète à exécuter.",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Lit et renvoie le contenu texte d'un fichier local sur le serveur."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Chemin absolu ou relatif du fichier à lire.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Recherche sur le web et renvoie une liste concise de resultats "
            "(titre, URL, extrait). Utilise cet outil des qu'une information "
            "recente, factuelle ou externe est necessaire."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "La requete de recherche.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Nombre de resultats souhaites (defaut 5).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Telecharge une page web et renvoie son contenu texte nettoye "
            "(sans balises, scripts ni styles), tronque. A utiliser apres un "
            "web_search pour lire reellement une page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "L'URL a telecharger."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "screenshot_url",
        "description": (
            "Ouvre une URL dans un navigateur sans interface et capture le "
            "rendu visuel de la page. Renvoie le chemin de l'image, a inserer "
            "dans la reponse sous forme ![capture](chemin) pour l'afficher. "
            "A n'utiliser que si l'aspect VISUEL importe : le texte seul "
            "s'obtient avec fetch_url, bien plus econome."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "L'URL a capturer."},
                "full_page": {
                    "type": "boolean",
                    "description": "Capturer toute la page plutot que l'ecran visible.",
                },
            },
            "required": ["url"],
        },
    },
]


def _openai_tool_schema(tools: list[dict]) -> list[dict]:
    """Traduit le schema Anthropic (name/description/input_schema) vers le
    format standard `tools` compatible OpenAI (Groq, Cerebras, SambaNova,
    NVIDIA NIM, OpenRouter, Ollama, OpenCode transport /chat/completions)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


OPENAI_TOOLS = _openai_tool_schema(TOOLS)


def _responses_tool_schema(tools: list[dict]) -> list[dict]:
    """Format `tools` de l'API OpenAI Responses (schema plat, sans sous-objet
    `function`) : transport /responses d'OpenCode (gpt-5.6-luna, muse-spark)."""
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in tools
    ]


RESPONSES_TOOLS = _responses_tool_schema(TOOLS)


def _gemini_tool_declaration():
    """Traduit TOOLS vers `types.Tool` (function calling natif Gemini)."""
    from google.genai import types

    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=t["input_schema"],
            )
            for t in TOOLS
        ]
    )


# =========================================================================
# Cadrage systeme par projet — cloisonnement strict du workspace
# =========================================================================
# Le modele n'a aucune idee de l'environnement reel : sans ce bloc il croit
# tourner sur une plateforme cloud, reclame sudo et deborde sur les autres
# projets du workspace. Le prompt est reconstruit a chaque appel LLM en
# fonction du projet actif de la conversation.
_current_project: ContextVar[str] = ContextVar("forge_current_project", default="")

FORGE_RULES_FILE = ".forge-rules"
FORGE_RULES_MAX_CHARS = int(_env("FORGE_RULES_MAX_CHARS", "8000"))


def set_current_project(name: Optional[str]) -> None:
    """Fixe le projet actif pour la requete en cours (contextvar)."""
    _current_project.set((name or "").strip())


def current_project() -> str:
    return _current_project.get()


def project_root(name: str = "") -> Optional[Path]:
    """Racine absolue du projet actif dans le workspace, si elle existe."""
    name = (name or _current_project.get()).strip()
    if not name or "/" in name or name.startswith("."):
        return None
    root = Path(settings.workspace_root) / name
    return root if root.is_dir() else None


def _read_project_rules(name: str) -> str:
    """Regles locales du projet (<projet>/.forge-rules), concatenees au prompt."""
    root = project_root(name)
    if root is None:
        return ""
    f = root / FORGE_RULES_FILE
    if not f.is_file():
        return ""
    try:
        return f.read_text(encoding="utf-8", errors="replace")[:FORGE_RULES_MAX_CHARS].strip()
    except OSError:
        return ""


def _forge_environment_block() -> str:
    return (
        "IDENTITE ET ENVIRONNEMENT REEL (non negociable) :\n"
        "- Tu es le moteur de code integre a The Forge, une application "
        "auto-hebergee sur un serveur Linux prive.\n"
        "- Tu n'es PAS sur une plateforme cloud distante (ni Anthropic, ni "
        "Emergent, ni aucun SaaS). Il n'y a ni conteneur jetable, ni sandbox "
        "recreable, ni outil propriétaire tiers : tout ce que tu modifies est "
        "persistant et reel.\n"
        "- Tu ne disposes que des outils exposes par cette Forge "
        "(bash, read_file, web_search, fetch_url, screenshot_url). N'invente "
        "jamais un outil, une API interne ou une commande de plateforme.\n\n"
        "ABSENCE TOTALE DE PRIVILEGES D'ADMINISTRATION :\n"
        "- Tu n'as ni sudo, ni root, ni aucun moyen d'en obtenir. C'est une "
        "contrainte de securite definitive : ne la demande pas, ne la "
        "contourne pas, ne propose pas de commande `sudo`.\n"
        "- Tu es developpeur applicatif dans ton dossier, pas administrateur "
        "systeme : pas de systemctl, apt, useradd, chown hors de ton projet, "
        "ni modification de la configuration de l'OS.\n"
        "- Toute difficulte de droits se resout avec le code, les fichiers et "
        "les dependances LOCALES de ton projet (venv, node_modules, scripts du "
        "projet). Si c'est impossible sans privileges, dis-le clairement et "
        "propose une alternative applicative."
    )


def _forge_project_block(project: str) -> str:
    root = f"{settings.workspace_root.rstrip('/')}/{project}"
    ws = settings.workspace_root.rstrip("/")
    forge_root = str(Path(ws).parent)
    return (
        f"CLOISONNEMENT STRICT — PROJET ACTIF : {project}\n"
        f"- Ton univers d'action commence et s'arrete exclusivement a "
        f"{root}/.\n"
        f"- Toute commande shell (git, tests, scripts, npm, pip) doit avoir "
        f"pour repertoire de travail {root}. Tu y es deja place : utilise des "
        f"chemins RELATIFS et ne fais jamais `cd` en dehors.\n"
        f"- INTERDICTION FORMELLE d'inspecter, lister, lire, modifier ou "
        f"executer quoi que ce soit dans les autres dossiers de {ws}/ : les "
        f"autres projets te sont inconnus et interdits, aucune lecture "
        f"« pour comparaison » n'est autorisee.\n"
        f"- INTERDICTION FORMELLE de toucher a la racine de production "
        f"{forge_root} (code de la Forge, deploy/, backend/, frontend/, "
        f"scripts d'installation) et a l'OS hote (/etc, /var/log, /usr, "
        f"systemd, nginx).\n"
        f"- Si une tache semble exiger de sortir de {root}/, ne le fais pas : "
        f"explique la limite et propose une solution interne au projet.\n"
        f"- CE PROJET N'EST PAS LA FORGE EN PRODUCTION QUE L'UTILISATEUR "
        f"UTILISE POUR TE PARLER, meme si son code est identique ou proche "
        f"(cas d'auto-hebergement/dogfooding) : c'est une copie isolee en "
        f"{root}/. Toute modification ici reste dans cette preview et NE "
        f"MET JAMAIS A JOUR l'instance reelle en production. Si l'utilisateur "
        f"demande un correctif « en prod », « sur mon serveur » ou « pour de "
        f"vrai », rappelle-lui explicitement le circuit a suivre : 1) valider "
        f"le correctif ici en preview, 2) le pousser sur GitHub (bouton "
        f"Sauvegarder sur GitHub), 3) executer upgrade.sh sur son serveur pour "
        f"le deployer reellement. Ne dis jamais avoir applique un changement "
        f"en production : tu n'y as aucun acces depuis ce projet."
    )


_FORGE_TOOLS_RULE_BLOCK = (
    "REGLE OBLIGATOIRE SUR LES OUTILS :\n"
    "1. Apres avoir execute un ou plusieurs outils, tu DOIS IMPERATIVEMENT "
    "formuler une reponse textuelle claire et complete pour l'utilisateur.\n"
    "2. Tu as l'interdiction formelle de renvoyer un message vide ou "
    "contenant uniquement des appels d'outils.\n"
    "3. Decris systematiquement ce que tu as fait, les fichiers touches "
    "et le resultat."
)


_FORGE_NO_PROJECT_BLOCK = (
    "AUCUN PROJET ACTIF :\n"
    "- Cette conversation n'est rattachee a aucun projet du workspace. "
    "N'ecris, ne modifie et ne supprime aucun fichier sur le serveur : "
    "reponds, explique, propose du code dans le chat.\n"
    "- Si l'utilisateur veut travailler sur des fichiers, demande-lui de "
    "rattacher la conversation a un projet du workspace."
)


def forge_system_prompt() -> str:
    pid = _current_provider.get()
    if pid != "claude":
        blocks = []
        blocks.append(
            f"Tu es l'assistant de code de The Forge, propulsé par le modèle '{pid}'. "
            f"Tu n'es PAS Claude et tu n'as PAS été créé par Anthropic. "
            f"Ne prétends jamais être Claude, ni Claude Code, ni affilié à Anthropic. "
            f"Réponds selon ta véritable identité de modèle, avec clarté, franchise et concision."
        )
        blocks.append(_FORGE_TOOLS_RULE_BLOCK)
        return "\n\n".join(blocks)

    """System prompt effectif : base + cadrage Forge + regles du projet."""
    blocks = []
    if settings.claude_system_prompt:
        blocks.append(settings.claude_system_prompt)
    blocks.append(_forge_environment_block())

    project = _current_project.get()
    if project:
        blocks.append(_forge_project_block(project))
        rules = _read_project_rules(project)
        if rules:
            blocks.append(
                f"REGLES LOCALES DU PROJET ({project}/{FORGE_RULES_FILE}) — "
                f"elles completent les regles ci-dessus et ne peuvent jamais "
                f"les assouplir :\n{rules}"
            )
        # Fetcher de contexte : memoire persistante du projet (snapshot + faits
        # memorises), pre-resolue avant l'appel LLM (voir prepare_turn_context).
        ctx = _context_block_cache.get()
        if ctx:
            blocks.append(ctx)
    else:
        blocks.append(_FORGE_NO_PROJECT_BLOCK)
    blocks.append(_FORGE_TOOLS_RULE_BLOCK)
    return "\n\n".join(blocks)


# =========================================================================
# FETCHER DE CONTEXTE & MEMOIRE PERSISTANTE
# =========================================================================
# Objectif (evolution retenue « Fetcher de contexte / memoire persistante ») :
# rassembler automatiquement le contexte utile d'un projet et le rendre
# disponible au modele, de facon STABLE et PERSISTANTE.
#
# Trois moments de capture :
#   1. A l'OUVERTURE D'UN PROJET  -> snapshot structurel (arborescence, stack,
#      README, .forge-rules, etat git). C'est le "fetch" initial.
#   2. A la PREMIERE CONVERSATION d'un projet -> le meme snapshot est consolide
#      et memorise (le modele demarre avec le contexte du projet, pas a froid).
#   3. DES QU'UNE INFORMATION IMPORTANTE arrive (message utilisateur ou
#      evenement systeme) -> le fait est detecte puis memorise durablement.
#
# La memoire est stockee dans la collection Mongo `context_facts`, indexee par
# (user_id, project). Le bloc de contexte est reinjecte a chaque appel LLM via
# forge_system_prompt(), donc il survit aux redemarrages et aux pertes de
# connexion (dogfooding : la Forge se souvient de ses propres projets).
# -------------------------------------------------------------------------

# Racine des fichiers de memoire PROJET (au sein du projet lui-meme) : un
# fichier texte lisible/editable par l'utilisateur, miroir de la base Mongo.
CONTEXT_FILE = ".forge-context"
CONTEXT_FILE_MAX_CHARS = int(_env("FORGE_CONTEXT_FILE_MAX_CHARS", "8000"))
# Nb max de faits memoires reinjectes par projet (les plus importants d'abord).
CONTEXT_MAX_FACTS = int(_env("FORGE_CONTEXT_MAX_FACTS", "40"))
# Nb max de faits renvoyes a l'UI (plus large que le prompt : l'utilisateur
# doit pouvoir consulter l'integralite de la memoire d'un projet).
CONTEXT_UI_MAX_FACTS = int(_env("FORGE_CONTEXT_UI_MAX_FACTS", "500"))
# Taille du snapshot d'arborescence (fichiers listes).
CONTEXT_TREE_MAX_ENTRIES = int(_env("FORGE_CONTEXT_TREE_MAX_ENTRIES", "80"))
# Profondeur maximale du scan d'arborescence.
CONTEXT_TREE_MAX_DEPTH = int(_env("FORGE_CONTEXT_TREE_MAX_DEPTH", "3"))

# Dossiers ignorés lors du scan (dependances reinstallables / bruit).
_CONTEXT_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".next", ".turbo", ".cache", ".mypy_cache",
    ".pytest_cache", "coverage", ".idea", ".vscode", "static",
}

# Fichiers de description de stack : lus pour deduire la nature du projet.
_CONTEXT_MANIFESTS = (
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile",
    "go.mod", "Cargo.toml", "composer.json", "Gemfile", "pom.xml",
    "build.gradle", "Dockerfile", "docker-compose.yml", "Makefile",
)

# Mots-clés signalant une information IMPORTANTE dans un texte (heuristique
# locale, sans appel LLM : cout zero, fonctionne hors-ligne).
# ---------------------------------------------------------------------
# CAPTURE DES FAITS — priorisation & anti-bruit
#
# Le Fetcher ne memorise PAS tout ce qui passe : il classe chaque passage
# selon sa priorite et ignore le bruit (logs, politesses, phrases sans
# enjeu). Ordre de priorite conforme au cadrage produit :
#   4  instruction/decision/regle EXPLICITE (utilisateur)
#   3  architecture / choix technique acte
#   2  chemin systeme cle ou variable d'environnement identifiee
#   1  fait factuel minimum (version, port, url, domaine)
#   0  bruit -> ignore
# ---------------------------------------------------------------------

# Categorie 4 (maximale) : l'utilisateur formule une regle / instruction.
_RULE_KEYWORDS = (
    "il faut", "tu dois", "on doit", "je veux que", "je souhaite que",
    "ne jamais", "ne pas", "toujours", "interdit", "obligatoire",
    "souviens-toi", "souviens", "retiens", "note bien", "notez bien",
    "a retenir", "a ne pas oublier", "rappel important", "regle", "consigne",
    "a partir de maintenant", "desormais", "pour l'avenir", "decision",
)

# Categorie 3 (haute) : decisions explicites et architecture figee.
_ARCH_KEYWORDS = (
    "architecture", "on utilise", "on a choisi", "on retient",
    "convention", "pattern", "stack", "techno",
    "base de donnees", "collection", "schema", "endpoint", "contrat api",
    "deploiement", "production", "branche", "depot", "migration",
)

# Categorie 2 (moyenne) : chemins SYSTEME cles uniquement. Un simple /tmp ou
# un chemin relatif ne doit PAS declencher a lui seul une memorisation.
_SYS_PATH_RE = re.compile(
    r"(?<![\w/])("
    r"/(?:etc|opt|srv|usr|var|home|root|mnt|media)"
    r"(?:/[\w.@-]+)+"
    r")"
)
_ENV_VAR_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]{2,})\b"
    r"(?=\s*[=:]|\s+(?:est|sont|contient|vaut|doit etre)\b)",
    re.IGNORECASE,
)

# Categorie 1 : motifs factuels minimaux (version, port, url, domaine).
_FACT_PATTERNS = (
    (re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b"), "version"),
    (re.compile(r"\bport\s*[:=]?\s*(\d{2,5})\b", re.I), "port"),
    (re.compile(r"\bhttps?://[\w.-]+(?::\d+)?(?:/[\w./-]*)?", re.I), "url"),
    (re.compile(r"\b([a-z0-9-]+\.(?:fr|com|org|net|io|dev|ai))\b", re.I), "domaine"),
)

# Duree minimale d'une phrase candidate a la memorisation.
_MIN_SENTENCE_LEN = 12


def _project_context_file(name: str) -> Optional[Path]:
    root = project_root(name)
    return (root / CONTEXT_FILE) if root is not None else None


def _scan_project_tree(root: Path) -> list[str]:
    """Arborescence compacte du projet (fichiers, profondeur limitee)."""
    lines: list[str] = []
    base_depth = len(root.parts)

    def walk(d: Path):
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        for e in entries:
            if len(lines) >= CONTEXT_TREE_MAX_ENTRIES:
                return
            if e.name in _CONTEXT_SKIP_DIRS or e.name.startswith(".forge-"):
                continue
            depth = len(e.parts) - base_depth
            if depth > CONTEXT_TREE_MAX_DEPTH:
                continue
            rel = e.relative_to(root)
            if e.is_dir():
                lines.append(f"{rel}/")
                walk(e)
            else:
                lines.append(str(rel))

    walk(root)
    if len(lines) >= CONTEXT_TREE_MAX_ENTRIES:
        lines.append("... (arborescence tronquee)")
    return lines


def _detect_stack(root: Path) -> list[str]:
    """Deduit la stack technique des fichiers manifestes presents."""
    found: list[str] = []
    for m in _CONTEXT_MANIFESTS:
        if (root / m).is_file():
            found.append(m)
    # Signatures plus parlantes.
    stack: list[str] = []
    if (root / "package.json").is_file():
        try:
            pkg = json.loads((root / "package.json").read_text(errors="replace"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            notable = [
                k for k in ("react", "vue", "next", "vite", "express",
                            "fastapi", "axios", "tailwindcss", "typescript")
                if k in deps
            ]
            stack.append("Node/JS" + (f" ({', '.join(notable)})" if notable else ""))
        except Exception:  # noqa: BLE001
            stack.append("Node/JS")
    if (root / "requirements.txt").is_file() or (root / "pyproject.toml").is_file():
        stack.append("Python")
    if (root / "go.mod").is_file():
        stack.append("Go")
    if (root / "Cargo.toml").is_file():
        stack.append("Rust")
    if (root / "Dockerfile").is_file() or (root / "docker-compose.yml").is_file():
        stack.append("Docker")
    if found:
        stack.append(f"manifestes: {', '.join(found)}")
    return stack


def _git_head_info(root: Path) -> dict:
    """Branche + dernier commit du projet (sans dependance GitPython)."""
    info: dict = {}
    if not (root / ".git").exists():
        return info
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
        if branch:
            info["branch"] = branch
        last = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--pretty=%h %s"],
            capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
        if last:
            info["last_commit"] = last
    except Exception:  # noqa: BLE001
        pass
    return info


def _read_readme_excerpt(root: Path) -> str:
    """Premier extrait du README : donne l'intention du projet."""
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        f = root / name
        if f.is_file():
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                return txt.strip()[:1500]
            except OSError:
                return ""
    return ""


# --- Cache des snapshots de projet --------------------------------------
# `fetch_project_snapshot` scanne le disque (arborescence, git via 2
# sous-processus, README) : c'est de loin le cout dominant a l'ouverture d'un
# projet et a chaque appel /context. Le dossier ne change pas entre deux
# ouvertures rapprochees : on memorise donc le snapshot avec un TTL court,
# invalide automatiquement si le mtime de la racine ou du .git change.
_SNAPSHOT_TTLS = float(_env("FORGE_SNAPSHOT_TTL", "15"))
# {project: (timestamp, signature_mtime, snapshot)}
_SNAPSHOT_CACHE: dict[str, tuple[float, float, dict]] = {}


def _project_sig(root: Optional[Path]) -> float:
    """Signature d'invalidation : mtime de la racine et du .git du projet."""
    if root is None:
        return 0.0
    sig = 0.0
    try:
        sig = root.stat().st_mtime
    except OSError:
        return 0.0
    try:
        git_dir = root / ".git"
        if git_dir.exists():
            sig = max(sig, git_dir.stat().st_mtime)
    except OSError:
        pass
    return sig


def invalidate_project_snapshot(name: str = "") -> None:
    """Purge le cache snapshot (projet precis, ou tout si name est vide)."""
    if not name:
        _SNAPSHOT_CACHE.clear()
        return
    _SNAPSHOT_CACHE.pop(name, None)


def fetch_project_snapshot(name: str) -> dict:
    """
    FETCH à l'ouverture d'un projet : photographie structurelle complete.

    Retourne un dict {project, root, stack, tree, git, readme, rules, is_repo}
    utilisé tel quel pour la mémoire et pour le bloc de contexte du prompt.
    """
    root = project_root(name)
    if root is None:
        return {"project": name, "root": None, "exists": False}

    now = time.monotonic()
    sig = _project_sig(root)
    cached = _SNAPSHOT_CACHE.get(name)
    if cached is not None:
        ts, cached_sig, snap = cached
        if (now - ts) < _SNAPSHOT_TTLS and cached_sig == sig:
            return snap

    rules = _read_project_rules(name)
    snap = {
        "project": name,
        "root": str(root),
        "exists": True,
        "is_repo": (root / ".git").exists(),
        "stack": _detect_stack(root),
        "tree": _scan_project_tree(root),
        "git": _git_head_info(root),
        "readme": _read_readme_excerpt(root),
        "rules": rules,
        "fetched_at": now_iso(),
    }
    _SNAPSHOT_CACHE[name] = (now, sig, snap)
    return snap


def _norm_fact(text: str) -> str:
    """Clé de deduplication : texte normalisé (minuscules, espaces compresses)."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:200]


def _fact_priority(text: str) -> tuple[int, str]:
    """
    Classe un fait selon sa PRIORITE (0 = a ignorer, plus haut = plus important).

      4 = instruction/decision/regle EXPLICITE
      3 = architecture / choix technique acte
      2 = chemin systeme cle ou variable d'environnement
      1 = fait factuel minimum (version, port, url, domaine)
      0 = bruit -> non memorise

    Anti-bruit strict : un simple mot isole ("erreur", "api", "version") ne
    suffit pas ; il faut un marqueur d'instruction, un mot d'architecture,
    un chemin SYSTEME, ou un motif factuel fort.
    """
    low = (text or "").lower().strip()
    if len(low) < _MIN_SENTENCE_LEN:
        return 0, ""

    if any(k in low for k in _RULE_KEYWORDS):
        return 4, "regle"
    if any(k in low for k in _ARCH_KEYWORDS):
        return 3, "architecture"
    if _SYS_PATH_RE.search(text or ""):
        return 2, "chemin"
    if _ENV_VAR_RE.search(text or ""):
        return 2, "variable"
    if len(low) >= 20:
        for pat, kind in _FACT_PATTERNS:
            if pat.search(text or ""):
                return 1, kind
    return 0, ""


def _looks_important(text: str) -> bool:
    """Ce texte merite-t-il d'etre memorise ? (priorite >= 1)"""
    return _fact_priority(text)[0] > 0


def _extract_fact_labels(text: str) -> list[str]:
    """Etiquettes de fait detectees, pour enrichir/organiser la memoire."""
    labels: list[str] = []
    _, kind = _fact_priority(text)
    if kind and kind not in labels:
        labels.append(kind)
    for pat, k in _FACT_PATTERNS:
        if pat.search(text or "") and k not in labels:
            labels.append(k)
    return labels


async def remember_context_facts(
    user_id: str,
    project: str,
    facts: list[dict],
    source: str = "user",
) -> int:
    """
    Memorise durablement des faits (message utilisateur ou evènement systeme).

    Chaque fait : {"text": str, "kind": str, "labels": [...], "source": str}.
    Deduplication par (project, norm) ; un fait deja connu voit son compteur de
    confirmation incrementé. Retourne le nombre de nouveaux faits inseres.
    """
    if not project or not facts:
        return 0
    database = get_db()
    inserted = 0
    now = now_iso()
    for f in facts:
        text = re.sub(r"\s+", " ", (f.get("text") or "").strip())
        if not text:
            continue
        norm = _norm_fact(text)
        existing = await database.context_facts.find_one(
            {"user_id": user_id, "project": project, "norm": norm}, {"_id": 1}
        )
        if existing:
            await database.context_facts.update_one(
                {"_id": existing["_id"]},
                {"$set": {"updated_at": now}, "$inc": {"confirmations": 1}},
            )
            continue
        await database.context_facts.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "project": project,
            "text": text[:1000],
            "norm": norm,
            "kind": f.get("kind") or "note",
            "labels": f.get("labels") or [],
            "priority": int(f.get("priority") or 0),
            "source": f.get("source") or source,
            "confirmations": 1,
            "created_at": now,
            "updated_at": now,
        })
        inserted += 1
    return inserted


async def capture_user_facts(user_id: str, project: str, text: str) -> int:
    """
    Capture les informations IMPORTANTES d'un message utilisateur.

    Ne memorise pas tout : uniquement les passages qui declenchent
    l'heuristique `_looks_important` (phrases decoupees sur la ponctuation).
    """
    if not project or not text:
        return 0
    # Decoupage en phrases : on memorise l'unité de sens, pas le pave entier.
    candidates = re.split(r"(?<=[.!?;\n])\s+", text)
    facts: list[dict] = []
    for c in candidates:
        c = c.strip()
        if not c or len(c) > 500:
            continue
        priority, kind = _fact_priority(c)
        if priority <= 0:
            continue  # bruit : non memorise
        facts.append({
            "text": c,
            "kind": kind or "user",
            "priority": priority,
            "labels": _extract_fact_labels(c),
            "source": "user",
        })
    return await remember_context_facts(user_id, project, facts, "user")


def _extract_system_facts(tool_steps: list[dict]) -> list[dict]:
    """
    Faits SYSTEME importants reveles par les outils d'un tour.

    On ne memorise que les sorties porteuses de chemins SYSTEME cles, de
    variables d'environnement (noms seulement, jamais les valeurs : le
    redact_secrets amont les a deja masquees) ou de marqueurs d'architecture.
    Sortie volontairement tres filtree (anti-bruit : pas les listings, pas les
    simple lignes de statut).
    """
    facts: list[dict] = []
    seen: set[str] = set()
    for step in tool_steps or []:
        tool = step.get("tool") or ""
        out = step.get("output") or ""
        if not out:
            continue
        for line in out.splitlines():
            line = line.strip()
            if len(line) < _MIN_SENTENCE_LEN or len(line) > 300:
                continue
            pr, kind = _fact_priority(line)
            if pr < 2:
                continue  # rien de structurel : on ignore
            key = _norm_fact(line)
            if key in seen:
                continue
            seen.add(key)
            facts.append({
                "text": line,
                "kind": kind,
                "priority": pr,
                "labels": _extract_fact_labels(line),
                "source": "system",
                "tool": tool,
            })
    return facts[:8]  # borne anti-saturation de la collection


async def recall_context_facts(user_id: str, project: str) -> list[dict]:
    """Rappelle les faits memorises d'un projet (plus confirmes/recents d'abord)."""
    if not project:
        return []
    database = get_db()
    docs = await database.context_facts.find(
        {"user_id": user_id, "project": project}, {"_id": 0}
    ).sort([("priority", -1), ("confirmations", -1), ("updated_at", -1)]).to_list(CONTEXT_MAX_FACTS)
    return docs


async def recall_context_facts_ui(user_id: str, project: str) -> list[dict]:
    """Variante UI : renvoie jusqu'a CONTEXT_UI_MAX_FACTS faits (liste complete
    pour le panneau deroulant), sans le plafond serre du prompt LLM."""
    if not project:
        return []
    database = get_db()
    return await database.context_facts.find(
        {"user_id": user_id, "project": project}, {"_id": 0}
    ).sort([("priority", -1), ("confirmations", -1), ("updated_at", -1)]).to_list(CONTEXT_UI_MAX_FACTS)


async def count_context_facts(user_id: str, project: str) -> int:
    """Total REEL de faits memorises pour un projet (sans plafond)."""
    if not project:
        return 0
    database = get_db()
    return await database.context_facts.count_documents(
        {"user_id": user_id, "project": project}
    )


async def fetch_and_store_project(
    user_id: str, project: str, source: str = "system"
) -> dict:
    """
    FETCH COMPLET d'un projet (ouverture de projet / premiere conversation).

    Consolide le snapshot structurel en memoire : la stack, l'etat git et les
    regles locales deviennent des faits persistants. Idempotent (deduplication).
    """
    snap = fetch_project_snapshot(project)
    if not snap.get("exists"):
        return {"ok": False, "project": project, "reason": "projet introuvable"}

    facts: list[dict] = []
    if snap.get("stack"):
        facts.append({
            "text": f"Stack technique de {project} : {', '.join(snap['stack'])}.",
            "kind": "snapshot", "source": source,
        })
    if snap.get("git"):
        g = snap["git"]
        facts.append({
            "text": (
                f"Depot git de {project} — branche {g.get('branch', '?')}"
                + (f", dernier commit : {g.get('last_commit')}" if g.get("last_commit") else "")
                + "."
            ),
            "kind": "snapshot", "source": source,
        })
    if snap.get("rules"):
        facts.append({
            "text": f"Regles locales du projet {project} : {snap['rules'][:500]}",
            "kind": "rules", "source": source,
        })
    if snap.get("readme"):
        facts.append({
            "text": f"Extrait README {project} : {snap['readme'][:500]}",
            "kind": "readme", "source": source,
        })

    inserted = await remember_context_facts(user_id, project, facts, source)
    logger.info(
        "Fetcher: projet %s — snapshot consolide (%d nouveau(x) fait(s))",
        project, inserted,
    )
    return {"ok": True, "project": project, "inserted": inserted, "snapshot": snap}

def _render_context_block(snap: dict, facts: list[dict]) -> str:
    """
    Construit le bloc de contexte « MEMOIRE DU PROJET » injecte dans le prompt.

    Sert au modele pour repartir avec le contexte reel du projet (structure,
    stack, regles, faits memorises) sans que l'utilisateur ait a tout repeter.
    """
    if not snap or not snap.get("exists"):
        return ""
    lines: list[str] = [f"MEMOIRE PERSISTANTE DU PROJET « {snap['project']} » :"]
    lines.append(
        "- Ce bloc est fourni par le Fetcher de contexte de la Forge. Il resulte "
        "des captures automatiques faites a l'ouverture du projet et a chaque "
        "information importante. Fie-toi a lui au lieu de re-scanner."
    )
    if snap.get("stack"):
        lines.append(f"- Stack : {', '.join(snap['stack'])}.")
    if snap.get("git"):
        g = snap["git"]
        lines.append(
            f"- Git : branche {g.get('branch', '?')}"
            + (f", dernier commit {g.get('last_commit')}" if g.get("last_commit") else "")
            + "."
        )
    tree = snap.get("tree") or []
    if tree:
        lines.append("- Arborescence (extrait) :\n  " + "\n  ".join(tree[:60]))
    if facts:
        lines.append("- Faits memorises (les plus importants d'abord) :")
        for f in facts[:CONTEXT_MAX_FACTS]:
            tag = f.get("kind") or "note"
            lines.append(f"  [{tag}] {f.get('text', '')[:400]}")
    return "\n".join(lines)


def sync_context_file(name: str, snap: dict, facts: list[dict]) -> None:
    """
    Miroir disque lisible de la memoire (<projet>/.forge-context).

    Permet a l'utilisateur de voir/editer ce que la Forge retient. Jamais
    bloquant : un echec d'ecriture est ignore (la base Mongo reste la source).
    """
    path = _project_context_file(name)
    if path is None:
        return
    try:
        header = (
            "# Mémoire de contexte — généré automatiquement par The Forge\n"
            "# Ce fichier est un miroir lisible de la mémoire du Fetcher.\n"
            f"# Dernière mise à jour : {now_iso()}\n\n"
        )
        body = _render_context_block(snap, facts)
        path.write_text((header + body)[:CONTEXT_FILE_MAX_CHARS], encoding="utf-8")
    except OSError:
        pass


async def get_project_context(user_id: str, project: str) -> str:
    """
    Contexte pret a injecter dans le prompt pour un projet donne.

    Combine le snapshot structurel frais et les faits memorises. Utilise par
    forge_system_prompt() — donc actif des la premiere conversation du projet.
    """
    if not project:
        return ""
    snap = fetch_project_snapshot(project)
    facts = await recall_context_facts(user_id, project)
    block = _render_context_block(snap, facts)
    # Miroir disque en tache de fond (pas de blocage du tour).
    try:
        asyncio.get_running_loop().create_task(
            asyncio.to_thread(sync_context_file, project, snap, facts)
        )
    except RuntimeError:
        pass
    return block


# Contexte projet du tour courant : rempli par les routes avant generate, lu par
# forge_system_prompt() (asynchrone -> on met en cache le bloc resolu).
_context_block_cache: ContextVar[str] = ContextVar("forge_context_block", default="")


async def prepare_turn_context(user_id: Optional[str], project: Optional[str]) -> str:
    """
    Pre-resout le bloc de contexte du projet pour le tour courant et le range
    dans le ContextVar lu par forge_system_prompt().

    Appelee en amont de chaque appel LLM (routes /chat/*), y compris dans la
    tache de fond du mode autonome (les ContextVar ne sont pas heritees par
    asyncio.create_task : il faut donc la reaffirmer explicitement).
    """
    block = ""
    if user_id and project:
        try:
            block = await get_project_context(user_id, project)
        except Exception:  # noqa: BLE001
            logger.warning("Fetcher: contexte indisponible pour %s", project)
            block = ""
    _context_block_cache.set(block)
    return block


# Garde-fou defensif : bloque toute commande visant a afficher/extraire le
# contenu de fichiers sensibles (secrets, cles privees), sans retirer shell=True
# ni restreindre les binaires disponibles (choix d'architecture assume : machine
# dediee mono-utilisateur). Detection au niveau du texte de la commande, avant
# toute execution.
_BASH_SENSITIVE_FILE_RE = re.compile(
    r"(\.env(?:\.[A-Za-z0-9_\-]+)?|\.pem|id_rsa(?:\.pub)?|id_ed25519(?:\.pub)?|\.key)\b",
    re.I,
)


def _truncate_lines(text: str, head: int = 50, tail: int = 50) -> str:
    """Au-dela de head+tail lignes : garde les `head` premieres et les `tail`
    dernieres, avec un message d'omission au milieu."""
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text
    omitted = len(lines) - head - tail
    return "\n".join(
        lines[:head]
        + [f"[... {omitted} lignes omises ...]"]
        + lines[-tail:]
    )


def _tool_bash(command: str) -> str:
    if not command:
        return "Erreur: commande vide."
    if _BASH_SENSITIVE_FILE_RE.search(command):
        return "Accès aux fichiers de configuration sensibles/secrets interdit via bash."
    # cwd force sur la racine du projet actif : le modele ne travaille jamais
    # a la racine de la Forge ni dans un autre projet du workspace.
    cwd = project_root()
    # Les builds front (vite/npm) depassent largement 30s : timeout etendu a 180s
    # uniquement pour ces commandes, 30s pour tout le reste.
    _low = command.lower()
    _timeout = 180 if any(k in _low for k in ("build", "vite", "npm")) else 30
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_timeout,
            cwd=str(cwd) if cwd else None,
        )
        out = _truncate_lines(result.stdout or "")[:8000]
        err = _truncate_lines(result.stderr or "")[:4000]
        return (
            f"cwd: {cwd or '(aucun projet actif)'}\n"
            f"exit_code: {result.returncode}\n"
            f"--- stdout ---\n{out}\n"
            f"--- stderr ---\n{err}"
        ).strip()
    except subprocess.TimeoutExpired:
        return "Erreur: timeout de 30 secondes dépassé."
    except Exception as e:  # noqa: BLE001
        return f"Erreur d'exécution: {e}"


def _tool_read_file(path: str) -> str:
    low = (path or "").lower()
    base = low.rsplit("/", 1)[-1]
    if (
        base.startswith(".env")
        or ".env." in base
        or base in {"credentials", ".netrc", ".git-credentials", "id_rsa", "id_ed25519"}
        or low.endswith((".key", ".pem", ".p12", ".pfx"))
    ):
        return (
            "Erreur: lecture refusee. Ce fichier contient des secrets "
            "(garde-fou de securite de la Forge)."
        )
    return _tool_read_file_raw(path)


def _tool_read_file_raw(path: str) -> str:
    if not path:
        return "Erreur: chemin vide."
    try:
        p = Path(path).expanduser()
        if not p.is_file():
            return f"Erreur: fichier introuvable: {path}"
        data = p.read_text(errors="replace")
        if len(data) > 100_000:
            data = data[:100_000] + "\n... (contenu tronqué)"
        return data
    except Exception as e:  # noqa: BLE001
        return f"Erreur de lecture: {e}"


def _tool_web_search(query: str, max_results: int = 5) -> str:
    """
    Recherche web. Delegue au module autonome tools/web_search (DuckDuckGo
    puis repli Wikipedia, sans cle). Google Custom Search reste un pre-filtre
    facultatif si sa paire de cles est configuree.
    Sortie volontairement compacte pour ne pas saturer le contexte.
    """
    query = (query or "").strip()
    if not query:
        return "Erreur: requete vide."
    n = max(1, min(int(max_results or 5), 10))

    if settings.google_cse_key and settings.google_cse_cx:
        try:
            resp = httpx.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": settings.google_cse_key,
                    "cx": settings.google_cse_cx,
                    "q": query,
                    "num": n,
                },
                timeout=20.0,
            )
            resp.raise_for_status()
            items = resp.json().get("items") or []
            if items:
                return _format_search_results([
                    {
                        "title": i.get("title", ""),
                        "href": i.get("link", ""),
                        "body": i.get("snippet", ""),
                    }
                    for i in items
                ], query, "Google Custom Search")
        except Exception as e:  # noqa: BLE001
            logger.warning("Google CSE indisponible, repli DuckDuckGo: %s", str(e)[:150])

    from tools.web_search import web_search as _web_search_autonome

    return _web_search_autonome(query, n)
def _format_search_results(results: list[dict], query: str, source: str) -> str:
    if not results:
        return f"Aucun resultat pour « {query} »."
    lines = [f"{len(results)} resultats pour « {query} » (via {source}) :"]
    for i, r in enumerate(results, 1):
        snippet = " ".join((r.get("body") or "").split())[:300]
        lines.append(
            f"\n{i}. {r.get('title', '(sans titre)')}\n"
            f"   {r.get('href') or r.get('url', '')}\n"
            f"   {snippet}"
        )
    return "\n".join(lines)


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(
    r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", re.S | re.I
)


def _validate_public_url(url: str) -> Optional[str]:
    """
    Garde-fou anti-SSRF : resout le nom d'hote et rejette toute IP privee,
    loopback, lien-local ou reservee. Retourne un message d'erreur explicite
    si l'URL est interdite, sinon None.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return "Erreur: URL invalide."
    if parsed.scheme not in ("http", "https"):
        return "Erreur: seuls les schemas http:// et https:// sont autorises."
    hostname = parsed.hostname
    if not hostname:
        return "Erreur: URL invalide (hote manquant)."
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return f"Erreur: resolution DNS impossible pour '{hostname}': {e}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return (
                f"Erreur: acces interdit a une adresse interne/privee "
                f"({hostname} -> {ip_str}). Les URL internes, locales ou "
                f"reservees ne sont pas autorisees (protection anti-SSRF)."
            )
    return None


def _tool_fetch_url(url: str) -> str:
    """Telecharge une page et en extrait le texte, sans dependance lourde."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Erreur: l'URL doit commencer par http:// ou https://."
    try:
        # Suit les redirections manuellement (follow_redirects=False) pour
        # revalider chaque hote traverse : une redirection ne doit jamais
        # permettre de contourner le blocage des adresses internes/privees.
        current_url = url
        resp = None
        for _ in range(5):
            err = _validate_public_url(current_url)
            if err:
                return err
            resp = httpx.get(
                current_url,
                timeout=25.0,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ClaudeUnchainedForge/1.0)"},
            )
            location = resp.headers.get("location")
            if resp.status_code in (301, 302, 303, 307, 308) and location:
                current_url = urljoin(current_url, location)
                continue
            break
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"Erreur de telechargement: {str(e)[:300]}"

    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "xml" not in ctype:
        if any(t in ctype for t in ("text/", "json")):
            return resp.text[: settings.fetch_url_max_chars]
        return f"Contenu non textuel ({ctype or 'type inconnu'}), lecture ignoree."

    html = _SCRIPT_RE.sub(" ", resp.text)
    text = _TAG_RE.sub(" ", html)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    truncated = len(text) > settings.fetch_url_max_chars
    out = text[: settings.fetch_url_max_chars]
    if truncated:
        out += f"\n\n[... tronque a {settings.fetch_url_max_chars} caracteres ...]"
    return f"Contenu de {url} :\n\n{out}"


def _tool_screenshot_url(url: str, full_page: bool = False) -> str:
    """
    Capture le rendu d'une page via Chromium sans interface.

    Volontairement desactivable (`ENABLE_SCREENSHOT`) : un navigateur headless
    est lourd pour un petit serveur.
    """
    if not settings.enable_screenshot:
        return (
            "Erreur: capture d'ecran desactivee (ENABLE_SCREENSHOT=false). "
            "Utilise fetch_url pour recuperer le texte de la page."
        )
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Erreur: l'URL doit commencer par http:// ou https://."

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            "Erreur: Playwright n'est pas installe. Sur le serveur : "
            "pip install playwright && python3 -m playwright install chromium"
        )

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.png"
    path = SCREENSHOT_DIR / name
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            page = browser.new_page(
                viewport={"width": 1280, "height": 800},
                device_scale_factor=1,
            )
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            title = page.title()
            page.screenshot(path=str(path), full_page=bool(full_page))
            browser.close()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            return (
                "Erreur: le navigateur Chromium de Playwright n'est pas installe sur ce "
                "serveur. Lance le script d'installation : "
                "sudo bash deploy/install-playwright.sh (Ubuntu 26.04), puis redemarre le "
                "backend. Verifie aussi PLAYWRIGHT_BROWSERS_PATH dans le .env."
            )
        return f"Erreur de capture: {msg[:300]}"

    public = f"/api/screenshots/{name}"
    return (
        f"Capture reussie de {url} (titre : {title}).\n"
        f"Chemin de l'image : {public}\n"
        f"Insere-la dans ta reponse avec : ![capture]({public})"
    )


SECRET_PATTERNS = [
    # Prefixes de jetons connus
    re.compile(r"\b(sk-or-v1-)[A-Za-z0-9_\-]{8,}", re.I),
    re.compile(r"\b(sk-ant-[a-z0-9\-]{0,12}-)[A-Za-z0-9_\-]{8,}", re.I),
    re.compile(r"\b(sk-)[A-Za-z0-9]{20,}"),
    re.compile(r"\b(gsk_)[A-Za-z0-9]{10,}"),
    re.compile(r"\b(csk-)[A-Za-z0-9]{10,}"),
    re.compile(r"\b(nvapi-)[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\b(gh[prous]_)[A-Za-z0-9_]+"),  # ghp_, gho_, ghu_, ghs_, ghr_
    re.compile(r"\b(github_pat_)[A-Za-z0-9_]{10,}"),
    re.compile(r"\b(AIza)[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(xox[baprs]-)[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

# Affectations de type CLE=valeur dans une sortie d'outil (cat .env, printenv...)
SECRET_ASSIGN = re.compile(
    r"^(\s*(?:export\s+)?[A-Z0-9_]*"
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIAL|DSN|MONGO_URL)"
    r"[A-Z0-9_]*\s*[=:]\s*)(\S.*)$",
    re.M,
)


def redact_secrets(text: str) -> str:
    """Masque les secrets (jetons connus, affectations CLE=valeur) dans un texte."""
    if not text:
        return text
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub(
            lambda m: (m.group(1) + "***REDACTED***") if m.groups() else "***REDACTED***",
            out,
        )
    out = SECRET_ASSIGN.sub(lambda m: m.group(1) + "***REDACTED***", out)
    return out


def _run_tool(name: str, tool_input: dict) -> str:
    return redact_secrets(_run_tool_raw(name, tool_input))


def _run_tool_raw(name: str, tool_input: dict) -> str:
    if name == "bash":
        return _tool_bash(tool_input.get("command", ""))
    if name == "read_file":
        return _tool_read_file(tool_input.get("path", ""))
    if name == "web_search":
        return _tool_web_search(
            tool_input.get("query", ""), tool_input.get("max_results", 5)
        )
    if name == "fetch_url":
        return _tool_fetch_url(tool_input.get("url", ""))
    if name == "screenshot_url":
        return _tool_screenshot_url(
            tool_input.get("url", ""), tool_input.get("full_page", False)
        )
    return f"Erreur: outil inconnu '{name}'."


def _build_messages(history: list[dict], text: str,
                    images: Optional[list[dict]]) -> list[dict]:
    """Construit le tableau `messages` au format Anthropic à partir de l'historique."""
    messages: list[dict] = []
    for h in history:
        role = "user" if h.get("role") == "user" else "assistant"
        content = (h.get("content") or "").strip()
        if not content:
            continue
        messages.append({"role": role, "content": [{"type": "text", "text": content}]})

    # Message courant (texte + image éventuelle)
    current: list[dict] = []
    for img in images or []:
        current.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img.get("mime") or "image/png",
                "data": img["data"],
            },
        })
    current.append({"type": "text", "text": text or "(image)"})
    messages.append({"role": "user", "content": current})
    return messages


async def _generate_ollama(history: list[dict], text: str) -> tuple[str, list[dict]]:
    """Génération via Ollama local (ex: qwen2.5-coder:3b), avec tool-calling
    (schema `tools` OpenAI, supporte par Ollama >= 0.3 sur les modeles compatibles)."""
    messages = []
    for h in history:
        r = "user" if h.get("role") == "user" else "assistant"
        c = (h.get("content") or "").strip()
        if c:
            messages.append({"role": r, "content": c})
    messages.append({"role": "user", "content": text or "(vide)"})
    if forge_system_prompt():
        messages = [{"role": "system", "content": forge_system_prompt()}] + messages

    tool_steps: list[dict] = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.ollama_timeout, connect=5.0)
    ) as http:
        for _ in range(MAX_TOOL_ITERS):
            payload = {
                "model": settings.ollama_model,
                "messages": messages,
                "stream": False,
                "keep_alive": settings.ollama_keep_alive,
                "options": {
                    "num_ctx": settings.ollama_num_ctx,
                    "num_predict": settings.ollama_num_predict,
                    "num_thread": settings.ollama_num_thread,
                },
            }
            if settings.enable_tools:
                payload["tools"] = OPENAI_TOOLS
            try:
                logger.info(
                    "Appel Ollama sur %s (modele %s)...",
                    settings.ollama_url, settings.ollama_model,
                )
                resp = await http.post(f"{settings.ollama_url}/api/chat", json=payload)
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Ollama injoignable sur {settings.ollama_url}. Vérifie qu'il "
                        "tourne (`ollama serve`) et que OLLAMA_URL est correct."
                    ),
                )
            except httpx.HTTPError as e:
                logger.exception("Erreur réseau Ollama")
                raise HTTPException(status_code=502, detail=f"Erreur réseau Ollama: {e}")

            if resp.status_code == 404:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Modèle Ollama '{settings.ollama_model}' introuvable. "
                        f"Lance d'abord : ollama pull {settings.ollama_model}"
                    ),
                )
            if resp.status_code >= 400:
                detail = resp.text
                try:
                    detail = resp.json().get("error", detail)
                except Exception:
                    pass
                raise HTTPException(status_code=502, detail=f"Erreur Ollama: {detail}")

            data = resp.json()
            msg = data.get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            if tool_calls and settings.enable_tools:
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                })
                # 1 seul outil par tour (evite le batching d'outils)
                tool_calls = (tool_calls or [])[:1]
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args or "{}")
                        except json.JSONDecodeError:
                            args = {}
                    args = args or {}
                    output = _run_tool(name, args)
                    tool_steps.append({"tool": name, "input": args, "output": output[:4000]})
                    messages.append({
                        "role": "tool", "content": output or "(vide)", "tool_name": name,
                    })
                continue

            answer = (msg.get("content") or "").strip()
            return (answer or "(reponse vide)", tool_steps)

    return ("⚠️ Trop d'etapes d'outils, reponse non finalisee.", tool_steps)


async def _generate_claude(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
) -> tuple[str, list[dict]]:
    """
    Appelle Claude via le jeton OAuth d'ABONNEMENT (Claude Pro/Max), avec
    support du TOOL CALLING (bash + read_file) et boucle agentique.

    Retourne (texte_final, tool_steps) où tool_steps liste les outils exécutés
    pour transparence côté frontend.

    Le jeton est généré par l'utilisateur via `claude setup-token`. On parle
    directement à l'endpoint Messages d'Anthropic en respectant les exigences
    des jetons OAuth (Authorization: Bearer + beta oauth + identité system).
    """
    if not settings.claude_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "CLAUDE_CODE_OAUTH_TOKEN absent. Genere un jeton avec "
                "`claude setup-token` (compte Claude Pro/Max) puis colle-le dans "
                "backend/.env. Aucune API payante n'est utilisee."
            ),
        )

    messages = _build_messages(history, text, images)
    tool_steps: list[dict] = []

    for _ in range(MAX_TOOL_ITERS):
        data = await _call_anthropic(messages)
        stop = data.get("stop_reason")
        content_blocks = data.get("content", [])

        if stop == "tool_use" and settings.enable_tools:
            # Réinjecter le tour assistant complet (avec les blocs tool_use)
            messages.append({"role": "assistant", "content": content_blocks})
            tool_results = []
            for block in content_blocks:
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                tinput = block.get("input", {}) or {}
                tool_id = block.get("id", "")
                output = _run_tool(name, tinput)
                tool_steps.append({
                    "tool": name,
                    "input": tinput,
                    "output": output[:4000],
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": output or "(vide)",
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Réponse finale (texte)
        parts = [
            b.get("text", "")
            for b in content_blocks
            if b.get("type") == "text"
        ]
        answer = "\n".join(p for p in parts if p).strip()
        return (answer or "(reponse vide)", tool_steps)

    # Boucle épuisée : on force une réponse texte SANS outils pour ne jamais
    # perdre le travail effectué (l'utilisateur voit les tool_steps + un résumé).
    try:
        data = await _call_anthropic(messages, use_tools=False)
        parts = [
            b.get("text", "")
            for b in data.get("content", [])
            if b.get("type") == "text"
        ]
        final = "\n".join(p for p in parts if p).strip()
    except HTTPException:
        final = ""
    if not final:
        final = (
            "⚠️ La tâche a nécessité beaucoup d'étapes d'outils et n'a pas pu "
            "être finalisée automatiquement. Consulte les étapes ci-dessus ; "
            "tu peux me demander de continuer."
        )
    return (final, tool_steps)


# =========================================================================
# Routeur de providers + cascade de bascule automatique
# =========================================================================
# Catalogues de modeles decouverts dynamiquement : {pid: (timestamp, [ids])}
_CATALOG_CACHE: dict[str, tuple[float, list[str]]] = {}
_CATALOG_TTL = float(_env("MODEL_CATALOG_TTL", "3600"))

FREE_PROVIDER_IDS = ("groq", "cerebras", "sambanova", "nvidia", "openrouter", "proxy")

PROVIDER_IDS = (
    "claude", "opencode", "gemini", "ollama_cloud",
    *FREE_PROVIDER_IDS, "ollama",
)

PROVIDER_LABELS = {
    "claude": "Claude",
    "gemini": "Gemini",
    "ollama_cloud": "Ollama Cloud",
    "opencode": "OpenCode Zen",
    "ollama": "Ollama (Local)",
    "groq": "Groq",
    "cerebras": "Cerebras",
    "sambanova": "SambaNova",
    "nvidia": "NVIDIA NIM",
    "openrouter": "OpenRouter",
    "proxy": "Proxy (OpenAI)",
}


def _provider_model(pid: str) -> str:
    """
    Modele configure pour un provider. Pour les providers gratuits, une valeur
    vide signifie "premier modele decouvert dynamiquement" (resolu plus tard
    par `_resolve_free_model`, qui a acces au catalogue).
    """
    if pid in FREE_PROVIDER_IDS:
        conf = settings.free_providers[pid]
        if conf["model"]:
            return conf["model"]
        cached = _CATALOG_CACHE.get(pid)
        if cached and cached[1]:
            return cached[1][0]
        return "(auto-découvert)"
    return {
        "claude": settings.claude_model,
        "gemini": settings.gemini_model,
        "ollama_cloud": settings.ollama_cloud_model,
        "opencode": settings.opencode_model,
        "ollama": settings.ollama_model,
    }.get(pid, pid)


def _provider_available(pid: str) -> bool:
    """Detection dynamique : un provider sans cle configuree est ignore."""
    if pid in FREE_PROVIDER_IDS:
        conf = settings.free_providers[pid]
        # Le provider proxy local n'a pas besoin de cle : seul l'URL compte.
        if pid == "proxy":
            return bool(conf["base_url"])
        return bool(conf["api_key"] and conf["base_url"])
    if pid == "claude":
        return bool(settings.claude_token)
    if pid == "gemini":
        return bool(settings.gemini_api_key)
    if pid == "ollama_cloud":
        return bool(settings.ollama_cloud_api_key and settings.ollama_cloud_url)
    if pid == "opencode":
        return bool(settings.opencode_api_key and settings.opencode_base_url)
    if pid == "ollama":
        return bool(settings.ollama_url)
    return False


def _free_provider_headers(pid: str) -> dict:
    conf = settings.free_providers[pid]
    headers = {"Content-Type": "application/json"}
    if conf.get("api_key"):
        headers["Authorization"] = f"Bearer {conf['api_key']}"
    if pid == "openrouter":
        if settings.openrouter_referer:
            headers["HTTP-Referer"] = settings.openrouter_referer
        headers["X-Title"] = settings.openrouter_title
    return headers


async def _resolve_free_model(pid: str, model_override: Optional[str]) -> str:
    """
    Modele a utiliser : override explicite > variable d'env > premier modele
    decouvert dynamiquement. Aucun nom de modele n'est code en dur.
    """
    if model_override:
        return model_override
    conf = settings.free_providers[pid]
    if conf["model"]:
        return conf["model"]
    catalog = await _fetch_catalog(pid)
    if not catalog:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Aucun modele decouvert chez {PROVIDER_LABELS[pid]} : cle "
                "invalide, quota epuise ou endpoint /models injoignable."
            ),
        )
    return catalog[0]


def _openai_messages(
    history: list[dict], text: str, images: Optional[list[dict]]
) -> list[dict]:
    """Corps de messages au format OpenAI, avec images eventuelles."""
    messages = _plain_messages(history, text)
    if images:
        messages[-1]["content"] = [
            {"type": "text", "text": text or "(image)"},
            *[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{i.get('mime') or 'image/png'};base64,{i['data']}"
                    },
                }
                for i in images
            ],
        ]
    if forge_system_prompt():
        messages = [
            {"role": "system", "content": forge_system_prompt()}
        ] + messages
    return messages


async def _generate_openai_compat(
    pid: str,
    history: list[dict],
    text: str,
    images: Optional[list[dict]] = None,
    model_override: Optional[str] = None,
) -> tuple[str, list[dict], str]:
    """Adaptateur unifie pour tout endpoint compatible OpenAI (non-stream),
    avec boucle de tool-calling (`tools`/`tool_calls`) identique a Claude."""
    if not _provider_available(pid):
        raise HTTPException(
            status_code=503,
            detail=f"{pid.upper()}_API_KEY absent dans backend/.env.",
        )
    conf = settings.free_providers[pid]
    model_name = await _resolve_free_model(pid, model_override)
    messages = _openai_messages(history, text, images)
    tool_steps: list[dict] = []

    has_streamed_text = False
    had_tools = False
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(conf["timeout"], connect=10.0)
    ) as http:
        for _ in range(MAX_TOOL_ITERS):
            payload = {
                "model": model_name,
                "messages": messages,
                "max_tokens": settings.claude_max_tokens,
            }
            # Item 8 : effort de raisonnement, uniquement si le modele le supporte.
            _effort = _resolve_reasoning_effort(model_name)
            if _effort:
                payload["reasoning_effort"] = _effort
            if settings.enable_tools:
                payload["tools"] = OPENAI_TOOLS
            resp = await http.post(
                f"{conf['base_url']}/chat/completions",
                json=payload,
                headers=_free_provider_headers(pid),
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Erreur {PROVIDER_LABELS[pid]}: {_http_error_detail(resp)}",
                )
            data = resp.json()
            if data.get("error"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Erreur {PROVIDER_LABELS[pid]}: {_http_error_detail(resp)}",
                )
            choices = data.get("choices") or []
            if not choices:
                return ("(reponse vide)", tool_steps, model_name)
            msg = choices[0].get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            if tool_calls and settings.enable_tools:
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                })
                # 1 seul outil par tour (evite le batching d'outils)
                tool_calls = (tool_calls or [])[:1]
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    try:
                        tinput = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        tinput = {}
                    output = _run_tool(name, tinput)
                    tool_steps.append({"tool": name, "input": tinput, "output": output[:4000]})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": output or "(vide)",
                    })
                continue

            answer = (msg.get("content") or "").strip()
            return (answer or "(reponse vide)", tool_steps, model_name)

    return ("⚠️ Trop d'etapes d'outils, reponse non finalisee.", tool_steps, model_name)


async def _stream_openai_turn(
    http: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload: dict,
) -> AsyncIterator[dict]:
    """
    Un tour de streaming SSE compatible OpenAI, partage par les providers
    gratuits et le transport /chat/completions d'OpenCode.

    Emet {"delta": "..."} pour chaque fragment de texte, puis un
    {"final": {"content": str, "tool_calls": [...]}} qui reconstitue les
    tool_calls fragmentes (accumules par `index`, comme le fait l'API OpenAI).
    """
    content = ""
    tool_calls: dict[int, dict] = {}
    async with http.stream("POST", url, json=payload, headers=headers) as resp:
        if resp.status_code >= 400:
            body = (await resp.aread()).decode("utf-8", "replace")
            raise HTTPException(status_code=502, detail=f"Erreur: {body[:400]}")
        async for ev in _sse_events(resp):
            if ev.get("error"):
                raise HTTPException(
                    status_code=502, detail=f"Erreur: {str(ev['error'])[:400]}"
                )
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    content += piece
                    yield {"delta": piece}
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
    ordered = [tool_calls[k] for k in sorted(tool_calls)]
    yield {"final": {"content": content, "tool_calls": ordered}}


async def _stream_openai_compat(
    pid: str,
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    model_override: Optional[str],
    state: dict,
) -> AsyncIterator[dict]:
    """Adaptateur unifie compatible OpenAI, en SSE (`stream: true`), avec
    boucle de tool-calling (memes evenements `tool` que Claude en flux)."""
    if not _provider_available(pid):
        raise HTTPException(
            status_code=503,
            detail=f"{pid.upper()}_API_KEY absent dans backend/.env.",
        )
    conf = settings.free_providers[pid]
    model_name = await _resolve_free_model(pid, model_override)
    state["model"] = model_name
    messages = _openai_messages(history, text, images)
    headers = _free_provider_headers(pid)
    url = f"{conf['base_url']}/chat/completions"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(conf["timeout"], connect=10.0)
    ) as http:
        for _ in range(MAX_TOOL_ITERS):
            payload = {
                "model": model_name,
                "messages": messages,
                "max_tokens": settings.claude_max_tokens,
                "stream": True,
            }
            # Item 8 : effort de raisonnement, uniquement si le modele le supporte.
            _effort = _resolve_reasoning_effort(model_name)
            if _effort:
                payload["reasoning_effort"] = _effort
            if settings.enable_tools:
                payload["tools"] = OPENAI_TOOLS
            final = None
            async for item in _stream_openai_turn(http, url, headers, payload):
                if "delta" in item:
                    has_streamed_text = True
                    yield item
                else:
                    final = item["final"]
            if not final or not final["tool_calls"]:
                if had_tools and not has_streamed_text:
                    yield {"delta": "\n\n*Actions terminées.*"}
                return
            had_tools = True
            messages.append({
                "role": "assistant",
                "content": final["content"] or "",
                "tool_calls": [
                    {
                        "id": tc["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
                    }
                    for i, tc in enumerate(final["tool_calls"])
                ],
            })
            for i, tc in enumerate(final["tool_calls"]):
                try:
                    tinput = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    tinput = {}
                output = await asyncio.to_thread(_run_tool, tc["name"], tinput)
                yield {"tool": {"tool": tc["name"], "input": tinput, "output": output}}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"] or f"call_{i}",
                    "content": output,
                })


def _estimate_tokens(messages: list[dict]) -> int:
    """Estimation grossiere mais suffisante : ~4 caracteres par token."""
    chars = sum(len(m.get("content") or "") for m in messages)
    return chars // 4


SUMMARY_INSTRUCTION = (
    "Tu resumes un historique de conversation pour liberer de la fenetre de "
    "contexte. Produis un compte rendu dense, en francais, qui PRESERVE "
    "absolument : les instructions et preferences donnees par l'utilisateur, "
    "les decisions prises, les faits techniques etablis (noms de fichiers, "
    "chemins, versions, identifiants, valeurs), et les problemes encore "
    "ouverts. Supprime les politesses et les redites. Pas d'introduction ni de "
    "conclusion, uniquement le contenu utile sous forme de puces courtes."
)


async def _summarize_messages(older: list[dict], previous: str = "") -> str:
    """Condense des messages anciens en un compte rendu factuel."""
    transcript = "\n\n".join(
        f"{'UTILISATEUR' if m.get('role') == 'user' else 'ASSISTANT'}: "
        f"{(m.get('content') or '')[:4000]}"
        for m in older
    )
    prompt = SUMMARY_INSTRUCTION
    if previous:
        prompt += (
            "\n\nVoici le resume deja etabli des echanges encore plus anciens, "
            "a fusionner avec les nouveaux echanges :\n" + previous
        )
    prompt += "\n\nEchanges a resumer :\n" + transcript
    # `history=[]` : pas de recursion possible sur la condensation.
    answer, _, meta = await generate_ai_response(
        history=[], text=prompt, provider="auto"
    )
    logger.info(
        "Historique condense par %s (%s) : %d messages -> %d caracteres",
        meta["provider"], meta["model"], len(older), len(answer),
    )
    return answer[: settings.summary_max_chars]


async def load_history_docs(
    database, conversation_id: str, exclude_id: Optional[str] = None
) -> list[dict]:
    """
    Charge l'historique utile a la generation : au plus `history_load_limit`
    derniers messages NON encore resumes, en ordre chronologique. Les messages
    plus anciens ne sont ni supprimes ni relus : ils sont deja couverts par le
    resume persiste de la conversation.
    """
    conv = await database.conversations.find_one(
        {"id": conversation_id}, {"_id": 0, "summarized_ids": 1}
    )
    skip_ids = list((conv or {}).get("summarized_ids") or [])
    if exclude_id:
        skip_ids.append(exclude_id)
    flt: dict = {"conversation_id": conversation_id}
    if skip_ids:
        flt["id"] = {"$nin": skip_ids}
    docs = (
        await database.messages.find(
            flt,
            {"_id": 0, "image_b64": 0, "file_text": 0, "images_b64": 0, "prompt_override": 0},
        )
        .sort("created_at", -1)
        .to_list(max(1, settings.history_load_limit))
    )
    docs.reverse()
    return docs


async def prepare_history(
    database, conversation_id: str, history: list[dict]
) -> list[dict]:
    """
    Reduit un historique trop long : les messages anciens sont remplaces par un
    resume persiste dans la conversation, les plus recents restent intacts.

    Le resume est incremental : on ne re-resume que ce qui a ete ajoute depuis
    le dernier passage, et il est stocke dans le document conversation.
    """
    if not settings.summary_enabled or not history:
        return history[-settings.history_turns :]

    conv = await database.conversations.find_one({"id": conversation_id})
    summary = (conv or {}).get("summary") or ""
    summarized_ids = set((conv or {}).get("summarized_ids") or [])

    # Messages deja couverts par le resume : on les retire du contexte brut.
    pending = [m for m in history if m.get("id") not in summarized_ids]

    if _estimate_tokens(pending) <= settings.summary_threshold_tokens:
        recent = pending[-settings.history_turns :]
        return _with_summary(summary, recent)

    keep = max(2, settings.summary_keep_recent)
    older, recent = pending[:-keep], pending[-keep:]
    if not older:
        return _with_summary(summary, recent)

    try:
        summary = await _summarize_messages(older, summary)
    except Exception as e:  # noqa: BLE001
        # Un echec de condensation ne doit jamais bloquer la conversation :
        # on retombe sur la troncature simple.
        logger.warning("Condensation impossible, troncature simple : %s", str(e)[:200])
        return _with_summary(summary, pending[-settings.history_turns :])

    summarized_ids.update(m["id"] for m in older if m.get("id"))
    await database.conversations.update_one(
        {"id": conversation_id},
        {"$set": {
            "summary": summary,
            "summarized_ids": list(summarized_ids),
            "summarized_at": now_iso(),
        }},
    )
    return _with_summary(summary, recent)


def _with_summary(summary: str, recent: list[dict]) -> list[dict]:
    """Prefixe l'historique recent par le resume, en respectant l'alternance."""
    if not summary:
        return recent
    return [
        {
            "role": "user",
            "content": (
                "[Resume des echanges precedents de cette conversation]\n"
                + summary
            ),
        },
        {
            "role": "assistant",
            "content": "Compris, je garde ce contexte en memoire.",
        },
        *recent,
    ]


def _classify_error(msg: str) -> str:
    """Categorise une erreur provider pour le log de bascule."""
    low = (msg or "").lower()
    if any(k in low for k in ("payment method", "usage credits", "add credits",
                              "creditserror", "insufficient")):
        return "credits"
    if any(k in low for k in ("regionerror", "only available hosted in",
                              "requires explicit opt in")):
        return "region"
    if any(k in low for k in ("missingsessionid", "x-opencode-session")):
        return "session"
    if any(k in low for k in ("free usage", "free tier", "not included in your")):
        return "freetier"
    if any(k in low for k in ("quota", "429", "resource_exhausted", "rate limit")):
        return "quota"
    if any(k in low for k in ("401", "403", "api key", "unauthenticated",
                              "permission_denied", "invalid_api_key", "unauthorized")):
        return "auth"
    if any(k in low for k in ("timeout", "timed out", "read timeout")):
        return "timeout"
    if any(k in low for k in ("connect", "dns", "network", "injoignable",
                              "unreachable")):
        return "network"
    # L'indisponibilite est testee avant le bucket "modele" : un message du
    # type "model unavailable" releve d'une panne, pas d'un mauvais nom.
    if any(k in low for k in ("503", "unavailable", "overloaded", "high demand",
                              "500", "502", "internal")):
        return "unavailable"
    if any(k in low for k in ("not found", "introuvable", "model", "404",
                              "unsupported")):
        return "model"
    return "unknown"


def _build_chain(requested: str) -> list[str]:
    """
    Chaine de providers a essayer.

    Un choix EXPLICITE (provider != "auto") n'a JAMAIS de bascule silencieuse :
    si le provider demande echoue, l'erreur precise est renvoyee telle quelle
    au frontend. Seul le mode "auto" beneficie de la cascade de secours
    multi-providers (Ollama local toujours en tout dernier recours).
    """
    if requested != "auto":
        return [requested]

    priority = [p for p in settings.provider_priority if p in PROVIDER_IDS]
    for p in PROVIDER_IDS:
        if p not in priority:
            priority.append(p)

    chain = list(priority) if settings.enable_fallback else priority[:1]

    if "ollama" in chain[1:]:
        chain = [p for p in chain if p != "ollama"] + ["ollama"]
    return chain


async def _dispatch_provider(
    pid: str,
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    session_id: Optional[str] = None,
    model_override: Optional[str] = None,
) -> tuple[str, list[dict], str]:
    """Retourne (texte, tool_steps, modele_reellement_utilise)."""
    if pid == "claude":
        if model_override:
            _current_claude_model.set(model_override)
        answer, steps = await _generate_claude(history, text, images)
        return answer, steps, _resolve_claude_model()
    if pid == "gemini":
        return await _generate_gemini(history, text, images)
    if pid == "ollama_cloud":
        return await _generate_ollama_cloud(
            history, text, images, model_override
        )
    if pid == "opencode":
        return await _generate_opencode(
            history, text, images, session_id, model_override
        )
    if pid == "ollama":
        answer, steps = await _generate_ollama(history, text)
        return answer, steps, settings.ollama_model
    if pid in FREE_PROVIDER_IDS:
        return await _generate_openai_compat(
            pid, history, text, images, model_override
        )
    raise HTTPException(status_code=400, detail=f"provider inconnu: {pid}")


async def generate_ai_response(
    history: list[dict],
    text: str,
    images: Optional[list[dict]] = None,
    provider: str = "claude",
    session_id: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[str, list[dict], dict]:
    """
    Route la generation vers le provider demande (ou le meilleur disponible en
    mode `auto`), avec bascule automatique sur le suivant en cas d'echec.

    `model` force un modele precis, uniquement sur le provider explicitement
    demande (les providers de secours gardent leur modele configure).

    Retourne (texte, tool_steps, meta) ou meta trace le provider/modele ayant
    reellement repondu et l'historique des tentatives.
    """
    requested = provider if provider in PROVIDER_IDS or provider == "auto" else "claude"
    chain = _build_chain(requested)
    single = len(chain) == 1  # choix explicite : aucune bascule, erreur brute renvoyee
    attempts: list[dict] = []

    for pid in chain:
        if not _provider_available(pid):
            detail = (
                f"{PROVIDER_LABELS.get(pid, pid)} n'est pas configure "
                "(cle API absente dans backend/.env)."
            )
            if single:
                raise HTTPException(status_code=503, detail=detail)
            attempts.append({
                "provider": pid,
                "model": _provider_model(pid),
                "kind": "unconfigured",
                "error": "cle / configuration absente",
            })
            logger.info("Routeur: %s ignore (non configure)", pid)
            continue

        try:
            answer, steps, used_model = await _dispatch_provider(
                pid, history, text, images, session_id,
                model if pid == requested else None,
            )
        except HTTPException as e:
            if single:
                raise
            detail = str(e.detail)
            kind = _classify_error(detail)
            attempts.append({
                "provider": pid, "model": _provider_model(pid),
                "kind": kind, "error": detail[:400],
            })
            logger.warning(
                "Routeur: bascule — %s (%s) a echoue [%s]: %s",
                pid, _provider_model(pid), kind, detail[:200],
            )
            continue
        except Exception as e:  # noqa: BLE001
            if single:
                raise HTTPException(status_code=502, detail=f"Erreur {PROVIDER_LABELS.get(pid, pid)}: {e}")
            kind = _classify_error(str(e))
            attempts.append({
                "provider": pid, "model": _provider_model(pid),
                "kind": kind, "error": str(e)[:400],
            })
            logger.warning(
                "Routeur: bascule — %s a leve une exception [%s]: %s",
                pid, kind, str(e)[:200],
            )
            continue

        meta = {
            "requested_provider": requested,
            "provider": pid,
            "model": used_model,
            "fallback_used": bool(attempts),
            "attempts": attempts,
        }
        if attempts:
            logger.info(
                "Routeur: reponse servie par %s (%s) apres %d bascule(s)",
                pid, used_model, len(attempts),
            )
        return answer, steps, meta

    tried = ", ".join(
        f"{a['provider']}({a['kind']})" for a in attempts
    ) or "aucun"
    logger.error("Routeur: tous les providers ont echoue. Tentatives: %s", tried)
    raise HTTPException(
        status_code=503,
        detail=(
            "Aucun moteur IA n'a pu repondre. Tentatives : " + tried + ". "
            "Verifie tes cles dans backend/.env ou lance Ollama en local."
        ),
    )


def _gemini_contents(history, text, images):
    from google.genai import types
    contents = []
    for h in history:
        role = "user" if h.get("role") == "user" else "model"
        c = (h.get("content") or "").strip()
        if c:
            contents.append(types.Content(role=role, parts=[types.Part(text=c)]))
    parts = []
    for img in images or []:
        parts.append(types.Part(
            inline_data=types.Blob(
                mime_type=img.get("mime") or "image/png",
                data=base64.b64decode(img["data"]),
            )
        ))
    parts.append(types.Part(text=text or "(image)"))
    contents.append(types.Content(role="user", parts=parts))
    return contents


def _gemini_function_calls(resp) -> list:
    """Extrait les `function_call` presents dans les parts d'une reponse Gemini."""
    calls = []
    try:
        for cand in (resp.candidates or []):
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    calls.append(fc)
    except Exception:  # noqa: BLE001
        pass
    return calls


async def _gemini_call_with_retry(client, model_name: str, contents: list, config):
    """Un appel Gemini avec retry/backoff sur erreurs transitoires (503/429/500)."""
    last_err: Optional[Exception] = None
    for attempt in range(4):
        try:
            return await asyncio.to_thread(
                lambda: client.models.generate_content(
                    model=model_name, contents=contents, config=config
                )
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e)
            transient = any(
                k in msg
                for k in ("503", "UNAVAILABLE", "overloaded", "high demand",
                          "RESOURCE_EXHAUSTED", "429", "500", "INTERNAL")
            )
            if transient and attempt < 3:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
    raise last_err  # pragma: no cover


async def _gemini_tool_loop(client, model_name: str, contents: list, config) -> tuple[str, list[dict]]:
    """Boucle agentique Gemini : function calling natif, memes tool_steps que
    Claude. `contents` est mute (reinjection des tours function_call/response)."""
    from google.genai import types as gtypes

    tool_steps: list[dict] = []
    for _ in range(MAX_TOOL_ITERS):
        resp = await _gemini_call_with_retry(client, model_name, contents, config)
        calls = _gemini_function_calls(resp) if config.tools else []
        if calls:
            contents.append(resp.candidates[0].content)
            response_parts = []
            # 1 seul outil par tour (evite le batching d'outils)
            calls = (calls or [])[:1]
            for fc in calls:
                args = dict(fc.args or {})
                output = _run_tool(fc.name, args)
                tool_steps.append({"tool": fc.name, "input": args, "output": output[:4000]})
                response_parts.append(
                    gtypes.Part.from_function_response(
                        name=fc.name, response={"result": output}
                    )
                )
            contents.append(gtypes.Content(role="user", parts=response_parts))
            continue
        answer = (getattr(resp, "text", None) or "").strip()
        return (answer or "(reponse vide)", tool_steps)
    return ("⚠️ Trop d'etapes d'outils, reponse non finalisee.", tool_steps)


async def _generate_gemini(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
) -> tuple[str, list[dict], str]:
    """Generation via Google Gemini (cle API GEMINI_API_KEY, SDK google-genai),
    avec function calling natif (memes outils que Claude)."""
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "GEMINI_API_KEY absent. Ajoute ta clé Google AI Studio dans "
                "backend/.env (GEMINI_API_KEY=...)."
            ),
        )
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(api_key=settings.gemini_api_key)
    base_contents = _gemini_contents(history, text, images)
    config = gtypes.GenerateContentConfig(
        system_instruction=forge_system_prompt(),
        max_output_tokens=settings.claude_max_tokens,
        tools=[_gemini_tool_declaration()] if settings.enable_tools else None,
    )

    # Modèle principal + fallback (utile quand un modèle est saturé).
    model_candidates = [settings.gemini_model]
    if settings.gemini_fallback_model and settings.gemini_fallback_model != settings.gemini_model:
        model_candidates.append(settings.gemini_fallback_model)

    last_err: Optional[Exception] = None
    for model_name in model_candidates:
        try:
            answer, tool_steps = await _gemini_tool_loop(
                client, model_name, list(base_contents), config
            )
            return (answer, tool_steps, model_name)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    # Échec après retries : message clair selon le type d'erreur.
    msg = str(last_err or "")
    logger.error("Gemini indisponible: %s", msg)
    if any(k in msg for k in ("503", "UNAVAILABLE", "overloaded", "high demand")):
        raise HTTPException(
            status_code=503,
            detail=(
                "Gemini est temporairement surchargé côté Google (503 high demand). "
                "Ta clé n'est PAS en cause. Réessaie dans un instant, ou bascule sur "
                "Claude/Ollama."
            ),
        )
    if any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "quota")):
        raise HTTPException(
            status_code=429,
            detail="Quota Gemini atteint (429). Réessaie plus tard ou change de provider.",
        )
    if any(
        k in msg
        for k in ("API key not valid", "API_KEY_INVALID", "PERMISSION_DENIED",
                  "401", "403", "invalid", "Unauthenticated")
    ):
        raise HTTPException(
            status_code=401,
            detail="Clé Gemini invalide ou non autorisée. Vérifie GEMINI_API_KEY.",
        )
    raise HTTPException(status_code=502, detail=f"Erreur Gemini: {msg}")


# =========================================================================
# Pieces jointes (images, texte, code, PDF)
# =========================================================================
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json",
    ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".xml", ".html", ".htm", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx",
    ".py", ".rb", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".php", ".sh", ".bash", ".zsh", ".fish", ".sql", ".graphql",
    ".vue", ".svelte", ".swift", ".lua", ".pl", ".r", ".jl", ".dart",
    ".dockerfile", ".gitignore", ".patch", ".diff", ".srt", ".vtt", ".tex",
}

_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
    ".tsx": "tsx", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".sh": "bash", ".bash": "bash", ".sql": "sql", ".html": "html",
    ".css": "css", ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".rb": "ruby", ".php": "php", ".md": "markdown",
    ".xml": "xml", ".toml": "toml", ".csv": "csv",
}


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        try:
            content = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            content = ""
        if content:
            pages.append(f"--- page {i} ---\n{content}")
    return "\n\n".join(pages)


def parse_attachment(filename: str, content_type: str, data: bytes) -> dict:
    """
    Classe une piece jointe et en extrait ce qui est exploitable.

    Retourne {kind, name, mime, size, image_b64?, text?} avec
    kind = "image" (envoye tel quel aux modeles vision) ou "text"
    (contenu extrait puis injecte dans le prompt, compatible tous providers).
    """
    name = (filename or "fichier").strip()
    mime = (content_type or "").split(";")[0].strip().lower()
    ext = os.path.splitext(name)[1].lower()
    base = {"name": name, "mime": mime, "size": len(data)}

    if mime.startswith("image/"):
        if len(data) > settings.max_image_mb * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"Image trop lourde (max {settings.max_image_mb} Mo).",
            )
        return {
            **base,
            "kind": "image",
            "image_b64": base64.b64encode(data).decode("utf-8"),
        }

    if mime == "application/pdf" or ext == ".pdf":
        extracted = _pdf_to_text(data)
        if not extracted:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Aucun texte extractible de '{name}'. C'est probablement un "
                    "PDF scanne (image). Envoie une capture d'ecran a la place."
                ),
            )
        return {**base, "kind": "text", "text": extracted}

    is_texty = (
        ext in _TEXT_EXTS
        or mime.startswith("text/")
        or mime in ("application/json", "application/xml",
                    "application/javascript", "application/x-yaml",
                    "application/x-sh", "application/sql")
    )
    if is_texty or not mime:
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                decoded = data.decode("latin-1")
            except Exception:  # noqa: BLE001
                decoded = ""
        if decoded.strip():
            return {**base, "kind": "text", "text": decoded}

    raise HTTPException(
        status_code=400,
        detail=(
            f"Type de fichier non gere : '{name}' ({mime or 'inconnu'}). "
            "Sont acceptes : images, PDF (texte), et tout fichier texte ou code "
            "(txt, md, csv, json, yaml, py, js, sql, log...). Pour un .docx ou "
            ".xlsx, exporte-le en PDF, CSV ou texte."
        ),
    )


def build_attachment_prompt(text: str, att: dict) -> str:
    """Injecte le contenu d'un fichier texte dans le prompt utilisateur."""
    return build_attachments_prompt(text, [att])


def build_attachments_prompt(text: str, atts: list[dict]) -> str:
    """
    Injecte le contenu de N fichiers texte dans le prompt utilisateur.

    Le budget `MAX_FILE_CHARS` est partage entre les fichiers pour ne pas
    exploser la fenetre de contexte quand on depose un dossier entier.
    """
    if not atts:
        return text
    budget = max(2000, settings.max_file_chars // max(1, len(atts)))
    blocks = []
    for att in atts:
        content = att.get("text") or ""
        truncated = len(content) > budget
        if truncated:
            content = content[:budget]
        lang = _LANG_BY_EXT.get(os.path.splitext(att["name"])[1].lower(), "")
        note = (
            f"\n\n[... tronque : le fichier depasse {budget} caracteres ...]"
            if truncated else ""
        )
        header = f"Fichier joint : {att['name']} ({att['size']} octets)"
        blocks.append(f"{header}\n```{lang}\n{content}{note}\n```")
    joined = "\n\n".join(blocks)
    return f"{joined}\n\n{text}".strip() if text else joined


async def process_uploads(uploads: list, text: str) -> dict:
    """
    Lit et classe N pieces jointes.

    Retourne {images, attachments, prompt_text, label} ou `images` part vers les
    modeles vision et les fichiers texte sont injectes dans `prompt_text`.
    """
    if not uploads:
        return {"images": [], "attachments": [], "prompt_text": text, "label": ""}
    if len(uploads) > settings.max_attachments:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Trop de fichiers ({len(uploads)}). Maximum "
                f"{settings.max_attachments} par message."
            ),
        )

    images: list[dict] = []
    attachments: list[dict] = []
    text_atts: list[dict] = []
    total = 0
    for up in uploads:
        raw = await up.read()
        total += len(raw)
        if total > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Pieces jointes trop lourdes au total (max "
                    f"{settings.max_upload_mb} Mo)."
                ),
            )
        att = parse_attachment(
            up.filename or "fichier", up.content_type or "", raw
        )
        attachments.append({
            "name": att["name"], "kind": att["kind"],
            "size": att["size"], "mime": att["mime"],
        })
        if att["kind"] == "image":
            images.append({
                "data": att["image_b64"],
                "mime": att["mime"] or "image/png",
            })
        else:
            text_atts.append(att)

    names = ", ".join(a["name"] for a in attachments)
    return {
        "images": images,
        "attachments": attachments,
        "prompt_text": build_attachments_prompt(text, text_atts),
        "label": f"({len(attachments)} fichiers : {names})"
        if len(attachments) > 1 else f"(fichier : {names})",
    }


def _images_from_message(msg: dict) -> list[dict]:
    """Images d'un message, avec compatibilite des anciens documents."""
    stored = msg.get("images_b64")
    if stored:
        return stored
    if msg.get("image_b64"):
        return [{
            "data": msg["image_b64"],
            "mime": msg.get("image_mime") or "image/png",
        }]
    return []


def _plain_messages(history: list[dict], text: str) -> list[dict]:
    """Historique au format texte simple (OpenAI / Ollama)."""
    msgs: list[dict] = []
    for h in history:
        role = "user" if h.get("role") == "user" else "assistant"
        content = (h.get("content") or "").strip()
        if content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": text or "(vide)"})
    return msgs


def _http_error_detail(resp) -> str:
    """Extrait un message lisible d'une reponse HTTP en erreur."""
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return (resp.text or "")[:500]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or err)[:500]
        if isinstance(err, str):
            return err[:500]
        if data.get("message"):
            return str(data["message"])[:500]
    return str(data)[:500]


async def _generate_ollama_cloud(
    history: list[dict],
    text: str,
    images: Optional[list[dict]] = None,
    model_override: Optional[str] = None,
) -> tuple[str, list[dict], str]:
    """
    Generation via Ollama Cloud (https://ollama.com/api), auth Bearer, avec
    tool-calling (schema `tools` OpenAI). Essaie le modele principal puis le
    modele de secours du meme provider.
    """
    if not settings.ollama_cloud_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "OLLAMA_CLOUD_API_KEY absent. Ajoute ta cle Ollama Cloud dans "
                "backend/.env."
            ),
        )

    base_messages = _plain_messages(history, text)
    if images:
        base_messages[-1]["images"] = [i["data"] for i in images]
    if forge_system_prompt():
        base_messages = [
            {"role": "system", "content": forge_system_prompt()}
        ] + base_messages

    candidates = [model_override or settings.ollama_cloud_model]
    if (
        settings.ollama_cloud_fallback_model
        and settings.ollama_cloud_fallback_model not in candidates
    ):
        candidates.append(settings.ollama_cloud_fallback_model)

    last_detail = ""
    headers = {
        "Authorization": f"Bearer {settings.ollama_cloud_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.ollama_cloud_timeout, connect=10.0)
    ) as http:
        for model_name in candidates:
            messages = list(base_messages)
            tool_steps: list[dict] = []
            failed = False
            answer = None
            for _ in range(MAX_TOOL_ITERS):
                payload = {"model": model_name, "messages": messages, "stream": False}
                if settings.enable_tools:
                    payload["tools"] = OPENAI_TOOLS
                try:
                    logger.info("Appel Ollama Cloud (modele %s)...", model_name)
                    resp = await http.post(
                        f"{settings.ollama_cloud_url}/chat",
                        json=payload,
                        headers=headers,
                    )
                except httpx.HTTPError as e:
                    last_detail = f"reseau: {e}"
                    failed = True
                    break

                if resp.status_code >= 400:
                    last_detail = _http_error_detail(resp)
                    logger.warning(
                        "Ollama Cloud %s -> HTTP %s: %s",
                        model_name, resp.status_code, last_detail[:200],
                    )
                    failed = True
                    break

                data = resp.json()
                # L'API renvoie parfois une erreur applicative avec un HTTP 200.
                if isinstance(data, dict) and data.get("error"):
                    last_detail = str(data["error"])[:500]
                    logger.warning(
                        "Ollama Cloud %s -> erreur applicative: %s",
                        model_name, last_detail[:200],
                    )
                    failed = True
                    break

                msg = data.get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                if tool_calls and settings.enable_tools:
                    messages.append({
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                    })
                    # 1 seul outil par tour (evite le batching d'outils)
                    tool_calls = (tool_calls or [])[:1]
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        name = fn.get("name", "")
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args or "{}")
                            except json.JSONDecodeError:
                                args = {}
                        args = args or {}
                        output = _run_tool(name, args)
                        tool_steps.append({"tool": name, "input": args, "output": output[:4000]})
                        messages.append({
                            "role": "tool", "content": output or "(vide)", "tool_name": name,
                        })
                    continue

                answer = (msg.get("content") or "").strip()
                break

            if failed:
                continue
            return (answer or "(reponse vide)", tool_steps, model_name)

    raise HTTPException(
        status_code=502,
        detail=f"Erreur Ollama Cloud: {last_detail or 'echec inconnu'}",
    )


def _opencode_transport(model: str) -> str:
    """
    OpenCode expose trois familles d'endpoints selon le modele :
    - /chat/completions (format OpenAI) : deepseek, glm, kimi, mimo, hy, grok...
    - /messages         (format Anthropic) : minimax-*, qwen3.*
    - /responses        (OpenAI Responses) : gpt-5.6-luna, muse-spark-*
    """
    forced = settings.opencode_transport
    if forced in ("chat", "messages", "responses"):
        return forced
    m = (model or "").lower()
    if m.startswith(("minimax-", "qwen3")):
        return "messages"
    if m.startswith(("gpt-", "muse-spark")):
        return "responses"
    return "chat"


def _opencode_convert_image(messages: list[dict], transport: str) -> list[dict]:
    """Traduit la partie image (format OpenAI) vers le format du transport."""
    if transport == "chat":
        return messages
    out = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        parts = []
        for part in content:
            if part.get("type") != "image_url":
                parts.append(part)
                continue
            url = (part.get("image_url") or {}).get("url", "")
            if transport == "responses":
                parts.append({"type": "input_image", "image_url": url})
            else:  # messages (format Anthropic)
                header, _, b64 = url.partition(",")
                mime = header.replace("data:", "").replace(";base64", "")
                parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime or "image/png",
                        "data": b64,
                    },
                })
        out.append({**m, "content": parts})
    return out


def _opencode_payload(
    transport: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
    tools_enabled: bool = True,
) -> tuple[str, dict]:
    """Construit (chemin, payload) pour le transport demande."""
    messages = _opencode_convert_image(messages, transport)

    if transport == "messages":
        # Format Anthropic : le system est un champ a part, tools = meme
        # schema que Claude (deja au format Anthropic).
        body = {
            "model": model,
            "max_tokens": settings.claude_max_tokens,
            "messages": messages,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools_enabled:
            body["tools"] = TOOLS
        return "/messages", body

    if transport == "responses":
        # Format OpenAI Responses : `input` + `instructions`, tools au schema plat.
        body = {
            "model": model,
            "input": messages,
            "max_output_tokens": settings.claude_max_tokens,
        }
        if system_prompt:
            body["instructions"] = system_prompt
        if tools_enabled:
            body["tools"] = RESPONSES_TOOLS
        return "/responses", body

    body = {"model": model, "max_tokens": settings.claude_max_tokens}
    if system_prompt:
        body["messages"] = [
            {"role": "system", "content": system_prompt}
        ] + messages
    else:
        body["messages"] = messages
    if tools_enabled:
        body["tools"] = OPENAI_TOOLS
    return "/chat/completions", body


def _opencode_extract_turn(transport: str, data: dict) -> dict:
    """
    Extrait {"text", "tool_calls": [{"id","name","arguments"(dict)}], ...} de
    la reponse non-stream, quel que soit le transport OpenCode. Les champs
    additionnels ("blocks"/"output"/"message") servent a reinjecter le tour
    assistant dans la conversation pour la suite de la boucle d'outils.
    """
    if transport == "messages":
        blocks = data.get("content") or []
        text = "".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ).strip()
        tool_calls = [
            {"id": b.get("id", ""), "name": b.get("name", ""), "arguments": b.get("input") or {}}
            for b in blocks if b.get("type") == "tool_use"
        ]
        return {"text": text, "tool_calls": tool_calls, "blocks": blocks}

    if transport == "responses":
        out = data.get("output") or []
        text_parts = []
        tool_calls = []
        for item in out:
            if item.get("type") == "message":
                for block in item.get("content") or []:
                    if block.get("type") in ("output_text", "text"):
                        text_parts.append(block.get("text", ""))
            elif item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": item.get("call_id", ""), "name": item.get("name", ""), "arguments": args,
                })
        return {"text": "".join(text_parts).strip(), "tool_calls": tool_calls, "output": out}

    choices = data.get("choices") or []
    if not choices:
        return {"text": "", "tool_calls": [], "message": {}}
    msg = choices[0].get("message") or {}
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args})
    return {"text": (msg.get("content") or "").strip(), "tool_calls": tool_calls, "message": msg}


# Codes/erreurs indiquant un mauvais endpoint plutot qu'un vrai refus :
# on reessaie alors le meme modele sur un autre transport.
_OPENCODE_WRONG_TRANSPORT = (400, 401, 404, 405, 415, 422)
_OPENCODE_FORMAT_HINTS = (
    "not supported for format",
    "unsupported format",
    "invalid format",
    "modelerror",
    "is not supported",
)


def _opencode_wrong_transport(status: int, detail: str) -> bool:
    low = (detail or "").lower()
    if any(h in low for h in _OPENCODE_FORMAT_HINTS):
        return True
    # 401 n'est un vrai refus que s'il parle bien d'authentification.
    if status == 401 and not any(
        k in low for k in ("api key", "auth", "unauthorized", "token")
    ):
        return True
    return status in _OPENCODE_WRONG_TRANSPORT and status != 401


async def _generate_opencode(
    history: list[dict],
    text: str,
    images: Optional[list[dict]] = None,
    session_id: Optional[str] = None,
    model_override: Optional[str] = None,
) -> tuple[str, list[dict], str]:
    """
    Generation via OpenCode Zen / Go.

    Gere les TROIS transports de la passerelle (/chat/completions au format
    OpenAI, /messages au format Anthropic, /responses au format OpenAI
    Responses), detectes automatiquement d'apres l'id du modele et reessayes
    entre eux si l'endpoint ne correspond pas. Chaque transport a sa propre
    boucle de tool-calling (schema natif au transport), sinon un modele comme
    Qwen3 (transport /messages) qui recoit `tools` sans jamais voir de
    `tool_use` traite ecrit sa syntaxe d'appel d'outil en texte brut au lieu
    de l'executer.

    OpenCode Go impose par ailleurs :
    - un User-Agent identifiable (pas un nom de lib HTTP) ;
    - un identifiant de session stable par conversation dans
      `x-opencode-session` (sinon erreur MissingSessionID).
    L'endpoint /messages s'authentifie via `x-api-key` (style Anthropic), les
    deux autres via `Authorization: Bearer` : on envoie les deux.
    """
    if not settings.opencode_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENCODE_API_KEY absent. Ajoute ta cle OpenCode dans "
                "backend/.env."
            ),
        )

    base_messages = _plain_messages(history, text)
    if images:
        base_messages[-1]["content"] = [
            {"type": "text", "text": text or "(image)"},
            *[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{i.get('mime') or 'image/png'};base64,{i['data']}"
                    },
                }
                for i in images
            ],
        ]

    candidates = [model_override or settings.opencode_model]
    if (
        settings.opencode_fallback_model
        and settings.opencode_fallback_model not in candidates
    ):
        candidates.append(settings.opencode_fallback_model)

    last_detail = ""
    headers = {
        "Authorization": f"Bearer {settings.opencode_api_key}",
        "x-api-key": settings.opencode_api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "User-Agent": settings.opencode_user_agent,
        # Session stable = id de conversation, pour le routage et le cache prompt.
        "x-opencode-session": f"ses_forge_{session_id or uuid.uuid4().hex}",
    }
    system_prompt = forge_system_prompt()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.opencode_timeout, connect=10.0)
    ) as http:
        for model_name in candidates:
            primary = _opencode_transport(model_name)
            transports = [primary] + [
                t for t in ("chat", "messages", "responses") if t != primary
            ]
            for transport in transports:
                messages = list(base_messages)
                tool_steps: list[dict] = []
                answer = None
                failed = False
                retry_other_transport = False

                for iteration in range(MAX_TOOL_ITERS):
                    path, payload = _opencode_payload(
                        transport, model_name, messages, system_prompt,
                        settings.enable_tools,
                    )
                    try:
                        logger.info(
                            "Appel OpenCode (modele %s, transport %s, iter %d)...",
                            model_name, transport, iteration,
                        )
                        resp = await http.post(
                            f"{settings.opencode_base_url}{path}",
                            json=payload,
                            headers=headers,
                        )
                    except httpx.HTTPError as e:
                        last_detail = f"reseau: {e}"
                        failed = True
                        break

                    try:
                        data = resp.json() if resp.content else {}
                    except Exception:  # noqa: BLE001
                        data = {}

                    if resp.status_code >= 400 or (
                        isinstance(data, dict) and data.get("error")
                    ):
                        last_detail = _http_error_detail(resp)
                        logger.warning(
                            "OpenCode %s/%s -> HTTP %s: %s",
                            model_name, transport, resp.status_code, last_detail[:200],
                        )
                        failed = True
                        if iteration == 0 and _opencode_wrong_transport(resp.status_code, last_detail):
                            retry_other_transport = True
                        break

                    turn = _opencode_extract_turn(transport, data)
                    if turn["tool_calls"] and settings.enable_tools:
                        outputs = []
                        for tc in turn["tool_calls"]:
                            output = _run_tool(tc["name"], tc["arguments"])
                            tool_steps.append({
                                "tool": tc["name"], "input": tc["arguments"], "output": output[:4000],
                            })
                            outputs.append(output or "(vide)")

                        if transport == "messages":
                            messages.append({"role": "assistant", "content": turn["blocks"]})
                            messages.append({
                                "role": "user",
                                "content": [
                                    {"type": "tool_result", "tool_use_id": tc["id"], "content": out}
                                    for tc, out in zip(turn["tool_calls"], outputs)
                                ],
                            })
                        elif transport == "responses":
                            messages = messages + (turn.get("output") or []) + [
                                {"type": "function_call_output", "call_id": tc["id"], "output": out}
                                for tc, out in zip(turn["tool_calls"], outputs)
                            ]
                        else:  # chat
                            msg = turn.get("message") or {}
                            messages.append({
                                "role": "assistant",
                                "content": msg.get("content") or "",
                                "tool_calls": msg.get("tool_calls") or [],
                            })
                            for tc, out in zip(turn["tool_calls"], outputs):
                                messages.append({
                                    "role": "tool", "tool_call_id": tc["id"], "content": out,
                                })
                        continue

                    answer = turn["text"]
                    break

                if failed:
                    if retry_other_transport:
                        continue  # essaie un autre transport
                    break  # vrai refus (region, quota, auth...) : modele suivant

                if answer is None:
                    answer = "⚠️ Trop d'etapes d'outils, reponse non finalisee."
                return (answer or "(reponse vide)", tool_steps, model_name)

    raise HTTPException(
        status_code=502,
        detail=f"Erreur OpenCode: {last_detail or 'echec inconnu'}",
    )


async def _call_anthropic(messages: list[dict], use_tools: bool = True) -> dict:
    """Un appel à l'API Messages d'Anthropic (avec outils si activés)."""
    payload = {
        "model": _resolve_claude_model(),
        "max_tokens": settings.claude_max_tokens,
        "system": (
            [
                {"type": "text", "text": CLAUDE_CODE_IDENTITY},
                {"type": "text", "text": forge_system_prompt()},
            ]
            if _current_provider.get() == "claude"
            else [{"type": "text", "text": forge_system_prompt()}]
        ),
        "messages": messages,
    }
    if settings.enable_tools and use_tools:
        payload["tools"] = TOOLS

    headers = {
        "authorization": f"Bearer {settings.claude_token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20,claude-code-20250219",
        "content-type": "application/json",
        "user-agent": "claude-cli/1.0.0 (external, cli)",
        "x-app": "cli",
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as http:
            resp = await http.post(ANTHROPIC_URL, headers=headers, json=payload)
    except httpx.HTTPError as e:
        logger.exception("Appel Anthropic impossible")
        raise HTTPException(status_code=502, detail=f"Claude injoignable: {e}")

    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail=(
                "Jeton d'abonnement Claude refuse (401). Il a peut-etre expire : "
                "regenere-le avec `claude setup-token`."
            ),
        )
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("error", {}).get("message", detail)
        except Exception:
            pass
        logger.error("Anthropic %s: %s", resp.status_code, detail)
        raise HTTPException(status_code=502, detail=f"Erreur Claude: {detail}")

    return resp.json()


# =========================================================================
# Streaming (SSE) : generation mot a mot + arret immediat
# =========================================================================
ANTHROPIC_STREAM_HEADERS = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "oauth-2025-04-20,claude-code-20250219",
    "content-type": "application/json",
    "user-agent": "claude-cli/1.0.0 (external, cli)",
    "x-app": "cli",
}


async def _sse_events(resp) -> AsyncIterator[dict]:
    """Decoupe un flux SSE en objets JSON (ignore les lignes de commentaire)."""
    async for raw in resp.aiter_lines():
        line = (raw or "").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


async def _stream_anthropic_turn(
    http: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload: dict,
) -> AsyncIterator[dict]:
    """
    Joue UN tour de conversation au format SSE Anthropic.

    Emet {"delta": "..."} pour chaque fragment de texte, puis un
    {"final": {"stop_reason": ..., "blocks": [...]}} reconstituant les blocs de
    la reponse (texte + tool_use), necessaires pour enchainer un appel d'outil.
    """
    blocks: list[dict] = []
    stop_reason = None
    async with http.stream("POST", url, headers=headers, json=payload) as resp:
        if resp.status_code >= 400:
            body = (await resp.aread()).decode("utf-8", "replace")
            detail = body
            try:
                detail = json.loads(body).get("error", {}).get("message", body)
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=resp.status_code if resp.status_code == 401 else 502,
                detail=f"Erreur Claude: {detail[:400]}",
            )

        async for ev in _sse_events(resp):
            etype = ev.get("type")
            if etype == "content_block_start":
                cb = ev.get("content_block") or {}
                if cb.get("type") == "text":
                    blocks.append({"type": "text", "text": ""})
                elif cb.get("type") == "tool_use":
                    blocks.append({
                        "type": "tool_use",
                        "id": cb.get("id"),
                        "name": cb.get("name"),
                        "_json": "",
                    })
            elif etype == "content_block_delta":
                d = ev.get("delta") or {}
                if not blocks:
                    continue
                if d.get("type") == "text_delta":
                    blocks[-1]["text"] = blocks[-1].get("text", "") + d.get("text", "")
                    yield {"delta": d.get("text", "")}
                elif d.get("type") == "input_json_delta":
                    blocks[-1]["_json"] = blocks[-1].get("_json", "") + d.get(
                        "partial_json", ""
                    )
            elif etype == "message_delta":
                stop_reason = (ev.get("delta") or {}).get("stop_reason") or stop_reason
            elif etype == "error":
                msg = (ev.get("error") or {}).get("message", "erreur de flux")
                raise HTTPException(status_code=502, detail=f"Erreur Claude: {msg}")

    for b in blocks:
        if b.get("type") == "tool_use":
            try:
                b["input"] = json.loads(b.pop("_json") or "{}")
            except json.JSONDecodeError:
                b["input"] = {}
                b.pop("_json", None)
    yield {"final": {"stop_reason": stop_reason, "blocks": blocks}}


async def _stream_claude(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    state: dict,
) -> AsyncIterator[dict]:
    """Claude en streaming, boucle d'outils incluse."""
    if not settings.claude_token:
        raise HTTPException(
            status_code=503, detail="CLAUDE_CODE_OAUTH_TOKEN absent."
        )
    state["model"] = _resolve_claude_model()
    messages = _build_messages(history, text, images)
    headers = {
        "authorization": f"Bearer {settings.claude_token}",
        **ANTHROPIC_STREAM_HEADERS,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as http:
        for _ in range(MAX_TOOL_ITERS):
            payload = {
                "model": _resolve_claude_model(),
                "max_tokens": settings.claude_max_tokens,
                "system": (
                    [
                        {"type": "text", "text": CLAUDE_CODE_IDENTITY},
                        {"type": "text", "text": forge_system_prompt()},
                    ]
                    if _current_provider.get() == "claude"
                    else [{"type": "text", "text": forge_system_prompt()}]
                ),
                "messages": messages,
                "stream": True,
            }
            if settings.enable_tools:
                payload["tools"] = TOOLS

            final = None
            async for item in _stream_anthropic_turn(
                http, ANTHROPIC_URL, headers, payload
            ):
                if "delta" in item:
                    yield item
                else:
                    final = item["final"]

            if not final or final["stop_reason"] != "tool_use":
                return

            # Un ou plusieurs outils a executer : on les joue puis on relance.
            messages.append({"role": "assistant", "content": final["blocks"]})
            results = []
            for b in final["blocks"]:
                if b.get("type") != "tool_use":
                    continue
                output = await asyncio.to_thread(
                    _run_tool, b.get("name", ""), b.get("input") or {}
                )
                yield {
                    "tool": {
                        "tool": b.get("name"),
                        "input": b.get("input") or {},
                        "output": output,
                    }
                }
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b.get("id"),
                    "content": output,
                })
            messages.append({"role": "user", "content": results})


async def _stream_gemini(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    state: dict,
) -> AsyncIterator[dict]:
    """Gemini en streaming, boucle d'outils incluse (function calling natif)."""
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY absent.")
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(api_key=settings.gemini_api_key)
    contents = _gemini_contents(history, text, images)
    config = gtypes.GenerateContentConfig(
        system_instruction=forge_system_prompt(),
        max_output_tokens=settings.claude_max_tokens,
        tools=[_gemini_tool_declaration()] if settings.enable_tools else None,
    )
    state["model"] = settings.gemini_model

    for _ in range(MAX_TOOL_ITERS):
        stream = await client.aio.models.generate_content_stream(
            model=settings.gemini_model, contents=contents, config=config
        )
        calls = []
        last_chunk = None
        async for chunk in stream:
            last_chunk = chunk
            piece = getattr(chunk, "text", None)
            if piece:
                yield {"delta": piece}
            calls.extend(_gemini_function_calls(chunk))
        if not calls or not last_chunk:
            return
        contents.append(last_chunk.candidates[0].content)
        parts = []
        # 1 seul outil par tour (evite le batching d'outils)
        calls = (calls or [])[:1]
        for fc in calls:
            args = dict(fc.args or {})
            output = await asyncio.to_thread(_run_tool, fc.name, args)
            yield {"tool": {"tool": fc.name, "input": args, "output": output}}
            parts.append(
                gtypes.Part.from_function_response(name=fc.name, response={"result": output})
            )
        contents.append(gtypes.Content(role="user", parts=parts))


async def _stream_ndjson_ollama(
    url: str,
    headers: dict,
    payload: dict,
    timeout: float,
) -> AsyncIterator[dict]:
    """Flux Ollama (local ou cloud) : une ligne JSON par fragment. Emet
    {"delta":...} pour le texte et {"tool_calls":[...]} si le modele en produit
    (Ollama les renvoie complets, sans fragmentation JSON progressive)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0)
    ) as http:
        async with http.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise HTTPException(
                    status_code=502, detail=f"Erreur Ollama: {body[:400]}"
                )
            async for line in resp.aiter_lines():
                if not (line or "").strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise HTTPException(
                        status_code=502,
                        detail=f"Erreur Ollama: {str(data['error'])[:400]}",
                    )
                msg = data.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    yield {"delta": piece}
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    yield {"tool_calls": tool_calls}


async def _stream_ollama(
    history: list[dict], text: str, state: dict
) -> AsyncIterator[dict]:
    """Ollama local en streaming, boucle d'outils incluse."""
    state["model"] = settings.ollama_model
    messages = _plain_messages(history, text)
    if forge_system_prompt():
        messages = [{"role": "system", "content": forge_system_prompt()}] + messages

    for _ in range(MAX_TOOL_ITERS):
        payload = {
            "model": settings.ollama_model,
            "messages": messages,
            "stream": True,
            "keep_alive": settings.ollama_keep_alive,
            "options": {
                "num_ctx": settings.ollama_num_ctx,
                "num_predict": settings.ollama_num_predict,
                "num_thread": settings.ollama_num_thread,
            },
        }
        if settings.enable_tools:
            payload["tools"] = OPENAI_TOOLS
        calls = None
        async for item in _stream_ndjson_ollama(
            f"{settings.ollama_url}/api/chat", {}, payload, settings.ollama_timeout
        ):
            if "delta" in item:
                yield item
            elif "tool_calls" in item:
                calls = item["tool_calls"]
        if not calls:
            return
        messages.append({"role": "assistant", "tool_calls": calls})
        # 1 seul outil par tour (evite le batching d'outils)
        calls = (calls or [])[:1]
        for tc in calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {}
            args = args or {}
            output = await asyncio.to_thread(_run_tool, name, args)
            yield {"tool": {"tool": name, "input": args, "output": output}}
            messages.append({"role": "tool", "content": output or "(vide)", "tool_name": name})


async def _stream_ollama_cloud(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    model_override: Optional[str],
    state: dict,
) -> AsyncIterator[dict]:
    """Ollama Cloud en streaming, boucle d'outils incluse."""
    if not settings.ollama_cloud_api_key:
        raise HTTPException(status_code=503, detail="OLLAMA_CLOUD_API_KEY absent.")

    candidates = [model_override or settings.ollama_cloud_model]
    if (
        settings.ollama_cloud_fallback_model
        and settings.ollama_cloud_fallback_model not in candidates
    ):
        candidates.append(settings.ollama_cloud_fallback_model)

    base_messages = _plain_messages(history, text)
    if images:
        base_messages[-1]["images"] = [i["data"] for i in images]
    if forge_system_prompt():
        base_messages = [
            {"role": "system", "content": forge_system_prompt()}
        ] + base_messages

    last_err: Optional[Exception] = None
    for model_name in candidates:
        state["model"] = model_name
        messages = list(base_messages)
        produced = False
        try:
            for _ in range(MAX_TOOL_ITERS):
                payload = {"model": model_name, "messages": messages, "stream": True}
                if settings.enable_tools:
                    payload["tools"] = OPENAI_TOOLS
                calls = None
                async for item in _stream_ndjson_ollama(
                    f"{settings.ollama_cloud_url}/chat",
                    {"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
                    payload,
                    settings.ollama_cloud_timeout,
                ):
                    if "delta" in item:
                        produced = True
                        yield item
                    elif "tool_calls" in item:
                        calls = item["tool_calls"]
                if not calls:
                    return
                messages.append({"role": "assistant", "tool_calls": calls})
                # 1 seul outil par tour (evite le batching d'outils)
                calls = (calls or [])[:1]
                for tc in calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args or "{}")
                        except json.JSONDecodeError:
                            args = {}
                    args = args or {}
                    output = await asyncio.to_thread(_run_tool, name, args)
                    produced = True
                    yield {"tool": {"tool": name, "input": args, "output": output}}
                    messages.append({"role": "tool", "content": output or "(vide)", "tool_name": name})
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if produced:
                raise
            last_err = e
            logger.warning(
                "Flux Ollama Cloud %s indisponible: %s",
                model_name, str(getattr(e, "detail", e))[:180],
            )
            continue
    raise last_err or HTTPException(
        status_code=502, detail="Erreur Ollama Cloud: echec inconnu"
    )


async def _stream_opencode(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    session_id: Optional[str],
    model_override: Optional[str],
    state: dict,
) -> AsyncIterator[dict]:
    """
    OpenCode en streaming, boucle d'outils incluse pour /chat/completions
    (SSE OpenAI) et /messages (SSE Anthropic, via `_stream_anthropic_turn`
    partage avec Claude). /responses retombe en non-stream (boucle d'outils
    geree par `_generate_opencode`).
    """
    if not settings.opencode_api_key:
        raise HTTPException(status_code=503, detail="OPENCODE_API_KEY absent.")
    model_name = model_override or settings.opencode_model
    state["model"] = model_name
    transport = _opencode_transport(model_name)

    if transport == "responses":
        answer, steps, used = await _generate_opencode(
            history, text, images, session_id, model_override
        )
        state["model"] = used
        for s in steps:
            yield {"tool": s}
        yield {"delta": answer}
        return

    messages = _plain_messages(history, text)
    if images:
        messages[-1]["content"] = [
            {"type": "text", "text": text or "(image)"},
            *[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{i.get('mime') or 'image/png'};base64,{i['data']}"
                    },
                }
                for i in images
            ],
        ]
    headers = {
        "Authorization": f"Bearer {settings.opencode_api_key}",
        "x-api-key": settings.opencode_api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "User-Agent": settings.opencode_user_agent,
        "x-opencode-session": f"ses_forge_{session_id or uuid.uuid4().hex}",
    }
    system_prompt = forge_system_prompt()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.opencode_timeout, connect=10.0)
    ) as http:
        if transport == "messages":
            for _ in range(MAX_TOOL_ITERS):
                path, payload = _opencode_payload(
                    transport, model_name, messages, system_prompt, settings.enable_tools
                )
                payload["stream"] = True
                final = None
                async for item in _stream_anthropic_turn(
                    http, f"{settings.opencode_base_url}{path}", headers, payload
                ):
                    if "delta" in item:
                        yield item
                    else:
                        final = item["final"]
                if not final or final["stop_reason"] != "tool_use":
                    return
                messages.append({"role": "assistant", "content": final["blocks"]})
                results = []
                for b in final["blocks"]:
                    if b.get("type") != "tool_use":
                        continue
                    output = await asyncio.to_thread(
                        _run_tool, b.get("name", ""), b.get("input") or {}
                    )
                    yield {"tool": {"tool": b.get("name"), "input": b.get("input") or {}, "output": output}}
                    results.append({
                        "type": "tool_result", "tool_use_id": b.get("id"), "content": output,
                    })
                messages.append({"role": "user", "content": results})
            return

        # transport == "chat"
        for _ in range(MAX_TOOL_ITERS):
            path, payload = _opencode_payload(
                transport, model_name, messages, system_prompt, settings.enable_tools
            )
            payload["stream"] = True
            final = None
            async for item in _stream_openai_turn(
                http, f"{settings.opencode_base_url}{path}", headers, payload
            ):
                if "delta" in item:
                    yield item
                else:
                    final = item["final"]
            if not final or not final["tool_calls"]:
                return
            messages.append({
                "role": "assistant",
                "content": final["content"] or "",
                "tool_calls": [
                    {
                        "id": tc["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
                    }
                    for i, tc in enumerate(final["tool_calls"])
                ],
            })
            for i, tc in enumerate(final["tool_calls"]):
                try:
                    tinput = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    tinput = {}
                output = await asyncio.to_thread(_run_tool, tc["name"], tinput)
                yield {"tool": {"tool": tc["name"], "input": tinput, "output": output}}
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"] or f"call_{i}", "content": output,
                })


async def _stream_provider(
    pid: str,
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    session_id: Optional[str],
    model_override: Optional[str],
    state: dict,
) -> AsyncIterator[dict]:
    if pid == "claude":
        if model_override:
            _current_claude_model.set(model_override)
        async for i in _stream_claude(history, text, images, state):
            yield i
    elif pid == "gemini":
        async for i in _stream_gemini(history, text, images, state):
            yield i
    elif pid == "ollama_cloud":
        async for i in _stream_ollama_cloud(
            history, text, images, model_override, state
        ):
            yield i
    elif pid == "opencode":
        async for i in _stream_opencode(
            history, text, images, session_id, model_override, state
        ):
            yield i
    elif pid == "ollama":
        async for i in _stream_ollama(history, text, state):
            yield i
    elif pid in FREE_PROVIDER_IDS:
        async for i in _stream_openai_compat(
            pid, history, text, images, model_override, state
        ):
            yield i
    else:
        raise HTTPException(status_code=400, detail=f"provider inconnu: {pid}")


async def stream_ai_response(
    history: list[dict],
    text: str,
    images: Optional[list[dict]] = None,
    provider: str = "claude",
    session_id: Optional[str] = None,
    model: Optional[str] = None,
) -> AsyncIterator[dict]:
    """
    Meme routeur/cascade que `generate_ai_response`, mais en flux.

    La bascule sur le provider suivant n'est possible que TANT QU'AUCUN texte
    n'a ete emis : une fois des mots envoyes au client, on ne peut plus repartir
    de zero, l'erreur est donc remontee telle quelle.
    """
    requested = provider if provider in PROVIDER_IDS or provider == "auto" else "claude"
    chain = _build_chain(requested)
    single = len(chain) == 1  # choix explicite : aucune bascule, erreur brute renvoyee
    attempts: list[dict] = []

    for pid in chain:
        if not _provider_available(pid):
            detail = (
                f"{PROVIDER_LABELS.get(pid, pid)} n'est pas configure "
                "(cle API absente dans backend/.env)."
            )
            if single:
                yield {"type": "error", "detail": detail, "provider": pid}
                return
            attempts.append({
                "provider": pid, "model": _provider_model(pid),
                "kind": "unconfigured", "error": "cle / configuration absente",
            })
            continue

        state: dict = {"model": _provider_model(pid)}
        produced = False
        try:
            async for item in _stream_provider(
                pid, history, text, images, session_id,
                model if pid == requested else None, state,
            ):
                if not produced:
                    yield {
                        "type": "start",
                        "provider": pid,
                        "model": state.get("model"),
                    }
                if "delta" in item:
                    produced = True
                    yield {"type": "delta", "text": item["delta"]}
                elif "tool" in item:
                    produced = True
                    yield {"type": "tool", "step": item["tool"]}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            detail = str(getattr(e, "detail", e))
            kind = _classify_error(detail)
            if produced or single:
                logger.error("Flux interrompu sur %s [%s]: %s", pid, kind, detail[:200])
                yield {"type": "error", "detail": detail[:400], "provider": pid}
                return
            attempts.append({
                "provider": pid, "model": state.get("model"),
                "kind": kind, "error": detail[:400],
            })
            logger.warning(
                "Routeur (flux): bascule — %s a echoue [%s]: %s",
                pid, kind, detail[:200],
            )
            continue

        yield {
            "type": "done",
            "provider": pid,
            "model": state.get("model"),
            "requested_provider": requested,
            "fallback_used": bool(attempts),
            "attempts": attempts,
        }
        return

    tried = ", ".join(f"{a['provider']}({a['kind']})" for a in attempts) or "aucun"
    yield {
        "type": "error",
        "detail": (
            "Aucun moteur IA n'a pu repondre. Tentatives : " + tried + "."
        ),
    }


# =========================================================================
# Rate limiting leger en memoire — protection de /auth/login contre le
# bruteforce (sans dependance externe type Redis : un dict en RAM suffit pour
# une instance mono-processus). Cle = IP cliente, valeur = timestamps des
# echecs recents. Purge periodique pour eviter toute fuite memoire.
# =========================================================================
_LOGIN_RATE_LIMIT_MAX = 5
_LOGIN_RATE_LIMIT_WINDOW = 60.0  # secondes
_LOGIN_RATE_LIMIT_PURGE_INTERVAL = 300.0  # purge des IP inactives

_login_failures: dict[str, list[float]] = {}
_login_rate_limit_last_purge = time.monotonic()


def _login_rate_limit_purge(now: float) -> None:
    """Supprime les IP sans echec recent : evite une croissance illimitee du
    dict en memoire au fil du temps."""
    global _login_rate_limit_last_purge
    if now - _login_rate_limit_last_purge < _LOGIN_RATE_LIMIT_PURGE_INTERVAL:
        return
    _login_rate_limit_last_purge = now
    cutoff = now - _LOGIN_RATE_LIMIT_WINDOW
    stale = [ip for ip, ts in _login_failures.items() if not ts or max(ts) < cutoff]
    for ip in stale:
        _login_failures.pop(ip, None)


def _login_rate_limit_check(ip: str) -> None:
    """Leve HTTP 429 si l'IP a deja atteint le seuil d'echecs dans la fenetre."""
    now = time.monotonic()
    _login_rate_limit_purge(now)
    cutoff = now - _LOGIN_RATE_LIMIT_WINDOW
    attempts = [ts for ts in _login_failures.get(ip, []) if ts >= cutoff]
    _login_failures[ip] = attempts
    if len(attempts) >= _LOGIN_RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Trop de tentatives de connexion échouées. Veuillez patienter une minute.",
        )


def _login_rate_limit_record_failure(ip: str) -> None:
    """Enregistre un echec d'authentification pour cette IP."""
    now = time.monotonic()
    _login_failures.setdefault(ip, []).append(now)


def _login_rate_limit_reset(ip: str) -> None:
    """Reinitialise le compteur d'echecs de l'IP (mot de passe valide soumis)."""
    _login_failures.pop(ip, None)


# =========================================================================
# Auth
# =========================================================================
@api_router.post("/auth/register")
async def register(payload: RegisterRequest, response: Response):
    if not settings.allow_registration:
        raise HTTPException(
            status_code=403, detail="Registration is disabled on this instance."
        )

    database = get_db()
    email = payload.email.lower().strip()

    if await database.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())
    doc = {
        "id": user_id,
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name or email.split("@")[0],
        "role": "user",
        "created_at": now_iso(),
    }
    await database.users.insert_one(doc)

    token = create_access_token(user_id, email)
    set_auth_cookie(response, token)
    return {
        "id": user_id,
        "email": email,
        "name": doc["name"],
        "role": "user",
        "token": token,
    }


@api_router.post("/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    database = get_db()
    email = payload.email.lower().strip()
    client_ip = request.client.host if request.client else "unknown"

    # Rate limiting : max 5 echecs / 60s / IP, avant meme de toucher la DB.
    _login_rate_limit_check(client_ip)

    user = await database.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        _login_rate_limit_record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Mot de passe valide : on reinitialise le compteur d'echecs de cette IP.
    _login_rate_limit_reset(client_ip)

    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name"),
        "role": user.get("role", "user"),
        "token": token,
    }


@api_router.post("/auth/logout")
async def logout(response: Response, current_user: dict = Depends(get_current_user)):
    clear_auth_cookie(response)
    return {"ok": True}


@api_router.get("/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "name": current_user.get("name"),
        "role": current_user.get("role", "user"),
    }


# =========================================================================
# Conversations
# =========================================================================
@api_router.get("/conversations")
async def list_conversations(current_user: dict = Depends(get_current_user)):
    database = get_db()
    return (
        await database.conversations.find(
            {"user_id": current_user["id"]},
            # summary / summarized_ids ne servent qu'a la generation : les
            # renvoyer alourdissait la liste a chaque resume.
            {"_id": 0, "summary": 0, "summarized_ids": 0},
        )
        .sort("updated_at", -1)
        .to_list(500)
    )


@api_router.post("/conversations")
async def create_conversation(
    payload: CreateConversationRequest,
    current_user: dict = Depends(get_current_user),
):
    database = get_db()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "title": payload.title or "New Chat",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    if payload.project:
        name = _valid_project_name(payload.project)
        _ensure_project_dir(name)
        await _assign_project_meta(current_user["id"], name)
        doc["project"] = name
    await database.conversations.insert_one(doc)
    # Fetcher : consolidation de la memoire du projet. JAMAIS bloquante : elle
    # scanne le disque et ecrit en base, ce qui allongeait artificiellement le
    # temps de reponse a l'ouverture d'un projet. On la lance en tache de fond
    # (le snapshot est de toute facon deja en cache si le hub l'a affiche).
    if doc.get("project"):
        uid = current_user["id"]
        project_name = doc["project"]

        async def _bg_fetch(_uid=uid, _name=project_name):
            try:
                await fetch_and_store_project(_uid, _name, "system")
            except Exception:  # noqa: BLE001
                logger.warning("Fetcher: consolidation a l'ouverture impossible")

        try:
            asyncio.get_running_loop().create_task(_bg_fetch())
        except RuntimeError:
            pass
    doc.pop("_id", None)
    return doc


MESSAGES_PAGE_DEFAULT = 150
MESSAGES_PAGE_MAX = 500


@api_router.get("/conversations/{conv_id}/messages")
async def get_messages(
    conv_id: str,
    limit: int = Query(MESSAGES_PAGE_DEFAULT, ge=1, le=MESSAGES_PAGE_MAX),
    before: Optional[str] = Query(None, max_length=64),
    current_user: dict = Depends(get_current_user),
):
    """
    Renvoie les `limit` derniers messages (ordre chronologique). Rien n'est
    supprime : `before` (created_at du plus ancien message deja affiche)
    permet de remonter page par page dans l'historique.
    """
    database = get_db()
    flt: dict = {"conversation_id": conv_id}
    if before:
        flt["created_at"] = {"$lt": before}
    # Verification d'appartenance et lecture des messages en parallele (au lieu
    # de deux aller-retours Mongo successifs). Rien n'est renvoye si la
    # conversation n'est pas a l'utilisateur.
    conv, docs = await asyncio.gather(
        database.conversations.find_one(
            {"id": conv_id, "user_id": current_user["id"]}, {"_id": 1}
        ),
        database.messages.find(
            flt,
            {"_id": 0, "image_b64": 0, "file_text": 0, "images_b64": 0, "prompt_override": 0},
        )
        .sort("created_at", -1)
        .to_list(limit),
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    docs.reverse()
    return docs


@api_router.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: str,
    payload: RenameRequest,
    current_user: dict = Depends(get_current_user),
):
    database = get_db()
    res = await database.conversations.update_one(
        {"id": conv_id, "user_id": current_user["id"]},
        {"$set": {"title": payload.title, "updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@api_router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: str, current_user: dict = Depends(get_current_user)
):
    database = get_db()
    res = await database.conversations.delete_one(
        {"id": conv_id, "user_id": current_user["id"]}
    )
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await database.messages.delete_many({"conversation_id": conv_id})
    return {"ok": True}


@api_router.delete("/conversations/{conv_id}/messages/{message_id}")
async def delete_message(
    conv_id: str, message_id: str, current_user: dict = Depends(get_current_user)
):
    """Supprime definitivement un seul message de la conversation."""
    database = get_db()
    conv = await database.conversations.find_one(
        {"id": conv_id, "user_id": current_user["id"]}, {"id": 1}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    res = await database.messages.delete_one(
        {"id": message_id, "conversation_id": conv_id}
    )
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    await database.conversations.update_one(
        {"id": conv_id}, {"$set": {"updated_at": now_iso()}}
    )
    return {"ok": True, "deleted": message_id}


# =========================================================================
# Chat
# =========================================================================
@api_router.post("/chat/send")
async def chat_send(
    conversation_id: str = Form(...),
    text: str = Form(""),
    image: Optional[UploadFile] = File(None),
    file: Optional[UploadFile] = File(None),
    files: list[UploadFile] = File(default=[]),
    provider: str = Form("claude"),
    model: Optional[str] = Form(None),
    reasoning_effort: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    database = get_db()

    conv = await database.conversations.find_one(
        {"id": conversation_id, "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Cadrage systeme : le LLM est cloisonne au projet de cette conversation.
    set_current_project(conv.get("project"))
    set_current_provider(provider)
    set_current_reasoning_effort(reasoning_effort)

    if provider not in PROVIDER_IDS and provider != "auto":
        raise HTTPException(status_code=400, detail="provider invalide")

    text = (text or "").strip()
    uploads = [u for u in (files or []) if u is not None]
    # `file` et `image` restent acceptes pour compatibilite.
    for legacy in (file, image):
        if legacy is not None:
            uploads.insert(0, legacy)
    if not text and not uploads:
        raise HTTPException(status_code=400, detail="Empty message")

    # --- Pieces jointes (images, texte/code, PDF) ---
    bundle = await process_uploads(uploads, text)
    images = bundle["images"]
    attachments = bundle["attachments"]
    prompt_text = bundle["prompt_text"]

    # --- Message utilisateur ---
    user_msg_id = str(uuid.uuid4())
    user_msg_doc = {
        "id": user_msg_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": text or bundle["label"],
        "has_image": bool(images),
        "attachments": attachments,
        "images_b64": images,
        "prompt_override": prompt_text if prompt_text != text else None,
        "created_at": now_iso(),
    }
    await database.messages.insert_one(user_msg_doc)

    # --- Historique (hors message courant) ---
    history = await load_history_docs(database, conversation_id, user_msg_id)
    history = await prepare_history(database, conversation_id, history)

    # --- Fetcher de contexte : memoire persistante du projet -------------
    # Pre-resout le bloc de contexte (snapshot + faits) du tour courant et le
    # range dans le ContextVar lu par forge_system_prompt(). Capture ensuite les
    # informations IMPORTANTES du message utilisateur (priorisees, anti-bruit).
    await prepare_turn_context(current_user["id"], conv.get("project"))
    if conv.get("project"):
        try:
            n_new = await capture_user_facts(
                current_user["id"], conv.get("project"), text
            )
            if n_new:
                logger.info(
                    "Fetcher: %d fait(s) memorise(s) depuis le message utilisateur",
                    n_new,
                )
        except Exception:  # noqa: BLE001
            logger.warning("Fetcher: capture utilisateur impossible")

    # --- Generation ---
    try:
        ai_response, tool_steps, meta = await generate_ai_response(
            history=history,
            text=prompt_text,
            images=images,
            provider=provider,
            session_id=conversation_id,
            model=(model or "").strip() or None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Echec de la generation de reponse")
        raise HTTPException(status_code=500, detail="Une erreur est survenue lors de la generation IA. Consultez les logs du serveur.")

    # --- Message assistant ---
    ai_msg_doc = {
        "id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": redact_secrets(ai_response),
        "has_image": False,
        "tool_steps": tool_steps,
        "provider": meta["provider"],
        "model": meta["model"],
        "requested_provider": meta["requested_provider"],
        "fallback_used": meta["fallback_used"],
        "routing": meta["attempts"],
        "created_at": now_iso(),
    }
    await database.messages.insert_one(ai_msg_doc)

    # --- Titre automatique au premier echange ---
    msg_count = await database.messages.count_documents(
        {"conversation_id": conversation_id}
    )
    update_fields = {"updated_at": now_iso()}
    if msg_count <= 2 and conv.get("title") in (None, "", "New Chat"):
        fallback_title = (
            f"Fichier : {attachments[0]['name']}" if attachments else "Image chat"
        )
        update_fields["title"] = (text or fallback_title)[:50]
    await database.conversations.update_one(
        {"id": conversation_id}, {"$set": update_fields}
    )

    for heavy in ("image_b64", "file_text", "images_b64", "prompt_override", "_id"):
        user_msg_doc.pop(heavy, None)
    ai_msg_doc.pop("_id", None)
    return {"user_message": user_msg_doc, "ai_message": ai_msg_doc}


def _summarize_turn(content: str, tools: list[dict], stopped: bool) -> str:
    """Produit un resume court et lisible en francais d'un tour de l'assistant."""
    n_chars = len(content or "")
    n_tools = len(tools)
    state = "interrompue (deconnexion)" if stopped else "terminee"
    if n_tools == 0:
        if n_chars:
            return f"Reponse {state} : {n_chars} caracteres produits, aucun outil execute."
        return f"Reponse {state} : aucun texte ni outil."
    names = ", ".join(dict.fromkeys(str(t.get("tool")) for t in tools))[:120]
    return (
        f"Reponse {state} : {n_tools} outil(s) execute(s) ({names}), "
        f"{n_chars} caracteres produits."
    )


@api_router.get("/chat/status")
async def chat_status(current_user: dict = Depends(get_current_user)):
    """
    Resume lisible des derniers tours, utile quand le client se deconnecte en
    plein traitement : au retour, il retrouve ce qui a ete fait ou pas fait.
    """
    database = get_db()
    convs = (
        await database.conversations.find(
            {"user_id": current_user["id"]},
            {"id": 1, "title": 1, "project": 1, "updated_at": 1},
        )
        .sort("updated_at", -1)
        .to_list(20)
    )
    turns = []
    for conv in convs:
        last = await database.messages.find_one(
            {"conversation_id": conv["id"], "role": "assistant"},
            sort=[("created_at", -1)],
        )
        if not last:
            continue
        tools = last.get("tool_steps") or []
        content = last.get("content") or ""
        turns.append(
            {
                "conversation_id": conv["id"],
                "title": conv.get("title") or "Sans titre",
                "project": conv.get("project"),
                "updated_at": conv.get("updated_at"),
                "created_at": last.get("created_at"),
                "summary": _summarize_turn(content, tools, last.get("stopped")),
                "tool_count": len(tools),
                "tool_names": [t.get("tool") for t in tools],
                "char_count": len(content),
                "stopped": bool(last.get("stopped")),
            }
        )
    # Executions encore en cours cote serveur (mode autonome) : le client peut
    # ainsi afficher "en cours" s'il revient pendant que ca tourne.
    running = [
        {
            "conversation_id": cid,
            "started_at": info.get("started_at"),
            "tool_count": info.get("tools", 0),
        }
        for cid, info in list(_ACTIVE_RUNS.items())
    ]
    return {"turns": turns, "running": running}


@api_router.get("/context/{project}")
async def get_context(
    project: str, current_user: dict = Depends(get_current_user)
):
    """
    Memoire persistante d'un projet (Fetcher de contexte).

    Renvoie le snapshot structurel (stack, arborescence, git) et les faits
    memorises, classes par priorite. Utile pour l'UI et pour l'utilisateur qui
    veut voir ce que la Forge retient de son projet.
    """
    name = _valid_project_name(project)
    snap = fetch_project_snapshot(name)
    facts = await recall_context_facts_ui(current_user["id"], name)
    total = await count_context_facts(current_user["id"], name)
    return {
        "project": name,
        "exists": snap.get("exists", False),
        "snapshot": {
            "stack": snap.get("stack", []),
            "git": snap.get("git", {}),
            "tree": snap.get("tree", []),
            "readme": snap.get("readme", ""),
            "rules": snap.get("rules", ""),
            "fetched_at": snap.get("fetched_at"),
        },
        "facts": [
            {
                "text": f.get("text"),
                "kind": f.get("kind"),
                "priority": f.get("priority", 0),
                "source": f.get("source"),
                "confirmations": f.get("confirmations", 1),
                "updated_at": f.get("updated_at"),
            }
            for f in facts
        ],
        # Total REEL en base (independant du plafond de la liste transmise).
        "count": total,
        "shown": len(facts),
    }


@api_router.post("/context/{project}/refresh")
async def refresh_context(
    project: str, current_user: dict = Depends(get_current_user)
):
    """
    Force un nouveau FETCH complet du projet (re-scan + consolidation).

    A utiliser quand les fichiers du projet ont change : le snapshot et les
    faits issus de la structure (stack, git, regles, README) sont refaits.
    """
    name = _valid_project_name(project)
    result = await fetch_and_store_project(current_user["id"], name, "system")
    return result


@api_router.get("/screenshots/{name}")
async def get_screenshot(name: str, current_user: dict = Depends(get_current_user)):
    """Sert une capture produite par l'outil screenshot_url."""
    if not re.fullmatch(r"[0-9a-f]{32}\.png", name):
        raise HTTPException(status_code=400, detail="nom invalide")
    path = SCREENSHOT_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="capture introuvable")
    return FileResponse(path, media_type="image/png")


@api_router.post("/stt")
async def speech_to_text(
    audio: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Transcription audio, utilisee comme repli quand le navigateur n'expose pas
    la Web Speech API (Firefox, certains WebView). S'appuie sur l'endpoint
    Whisper compatible OpenAI du premier provider gratuit configure.
    """
    provider = next(
        (p for p in ("groq",) + FREE_PROVIDER_IDS if _provider_available(p)),
        None,
    )
    if not provider:
        raise HTTPException(
            status_code=503,
            detail=(
                "Aucune cle de transcription disponible. Renseigne GROQ_API_KEY "
                "dans backend/.env, ou utilise un navigateur qui gere la dictee "
                "native (Chrome, Edge, Safari)."
            ),
        )

    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Audio vide.")
    if len(raw) > settings.stt_max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"Audio trop lourd (max {settings.stt_max_mb} Mo).",
        )

    conf = settings.free_providers[provider]
    headers = {k: v for k, v in _free_provider_headers(provider).items()
               if k != "Content-Type"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as http:
            resp = await http.post(
                f"{conf['base_url']}/audio/transcriptions",
                headers=headers,
                files={
                    "file": (
                        audio.filename or "dictee.webm",
                        raw,
                        audio.content_type or "audio/webm",
                    )
                },
                data={"model": settings.stt_model, "response_format": "json"},
            )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Transcription indisponible: {e}")

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Erreur de transcription: {_http_error_detail(resp)}",
        )
    return {
        "text": (resp.json().get("text") or "").strip(),
        "provider": provider,
        "model": settings.stt_model,
    }


@api_router.post("/chat/stream")
async def chat_stream(
    conversation_id: str = Form(...),
    text: str = Form(""),
    file: Optional[UploadFile] = File(None),
    files: list[UploadFile] = File(default=[]),
    provider: str = Form("claude"),
    model: Optional[str] = Form(None),
    reasoning_effort: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Meme chose que /chat/send mais en flux SSE : les mots arrivent au fur et a
    mesure et fermer la connexion arrete la generation immediatement.

    Evenements emis : `delta` (fragment de texte), `tool` (outil execute),
    `done` (message assistant complet + routage), `error`.
    """
    database = get_db()

    conv = await database.conversations.find_one(
        {"id": conversation_id, "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Cadrage systeme : le LLM est cloisonne au projet de cette conversation.
    set_current_project(conv.get("project"))
    set_current_provider(provider)
    set_current_reasoning_effort(reasoning_effort)
    if provider not in PROVIDER_IDS and provider != "auto":
        raise HTTPException(status_code=400, detail="provider invalide")

    text = (text or "").strip()
    uploads = [u for u in (files or []) if u is not None]
    if file is not None:
        uploads.insert(0, file)
    if not text and not uploads:
        raise HTTPException(status_code=400, detail="Empty message")

    bundle = await process_uploads(uploads, text)
    images = bundle["images"]
    attachments = bundle["attachments"]
    prompt_text = bundle["prompt_text"]

    user_msg_id = str(uuid.uuid4())
    user_msg_doc = {
        "id": user_msg_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": text or bundle["label"],
        "has_image": bool(images),
        "attachments": attachments,
        "images_b64": images,
        "prompt_override": prompt_text if prompt_text != text else None,
        "created_at": now_iso(),
    }
    await database.messages.insert_one(user_msg_doc)

    # --- Fetcher : capture des informations importantes de l'utilisateur ---
    if conv.get("project"):
        try:
            await capture_user_facts(current_user["id"], conv.get("project"), text)
        except Exception:  # noqa: BLE001
            logger.warning("Fetcher: capture utilisateur impossible (stream)")

    history = await load_history_docs(database, conversation_id, user_msg_id)
    history = await prepare_history(database, conversation_id, history)

    public_user_msg = {
        k: v for k, v in user_msg_doc.items()
        if k not in ("_id", "image_b64", "file_text", "images_b64", "prompt_override")
    }

    async def event_source():
        # Mode autonome : la generation tourne dans une tache de fond, totalement
        # decouplee de cette connexion SSE. Si le client se deconnecte, le runner
        # va jusqu'au bout et persiste le message final (stopped=False). On
        # publie immediatement l'echo du message utilisateur.
        yield _sse("user_message", public_user_msg)

        channel = _SSEQueue()
        project = conv.get("project")
        _ACTIVE_RUNS[conversation_id] = {
            "started_at": now_iso(),
            "queue": channel,
            "tools": 0,
            "chars": 0,
        }
        _spawn(
            _run_generation(
                database,
                conversation_id,
                history,
                prompt_text,
                images,
                attachments,
                provider,
                model,
                project,
                channel,
                current_user["id"],
            )
        )
        try:
            async for ev in channel.stream(ping_interval=4.0):
                if ev["type"] == "__ping__":
                    # Commentaire SSE : maintient la connexion ouverte sans
                    # etre interprete comme un evenement par le client.
                    yield ": ping\n\n"
                elif ev["type"] == "delta":
                    yield _sse("delta", {"text": ev["text"]})
                elif ev["type"] == "start":
                    yield _sse("start", ev)
                elif ev["type"] == "step_start":
                    yield _sse("step_start", {"index": ev["index"]})
                elif ev["type"] == "tool":
                    yield _sse("tool", ev["step"])
                elif ev["type"] == "step_done":
                    # Evenement de fin d'etape : le client cloture le cadre de
                    # l'etape courante (evite les coupures de flux tronquees).
                    yield _sse("step_done", {"step": ev["step"]})
                elif ev["type"] == "error":
                    yield _sse("error", {"detail": ev["detail"]})
                elif ev["type"] == "done":
                    yield _sse("done", ev["doc"])
        except asyncio.CancelledError:
            # Client deconnecte : le runner de fond continue sa route. On ne
            # marque SURTOUT pas la generation comme arretee.
            logger.info(
                "Client deconnecte (conv %s) : generation poursuivie en fond",
                conversation_id,
            )
            raise

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Indispensable derriere Nginx : sans ca le flux est tamponne.
            "X-Accel-Buffering": "no",
        },
    )


def _purge_old_screenshots() -> None:
    """Supprime les captures expirees pour ne pas remplir le disque."""
    if not SCREENSHOT_DIR.exists():
        return
    cutoff = time.time() - settings.screenshot_retention_hours * 3600
    removed = 0
    for f in SCREENSHOT_DIR.glob("*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Captures expirees supprimees : %d", removed)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# Taches detachees : on garde une reference pour qu'elles ne soient pas
# ramassees par le GC avant la fin.
_BG_TASKS: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _persist_stopped(
    database, conversation_id: str, content: str, tool_steps: list[dict], meta: dict
) -> None:
    """Sauvegarde la reponse partielle d'une generation interrompue."""
    try:
        await _persist_assistant(
            database, conversation_id, content, tool_steps, meta, True
        )
        await database.conversations.update_one(
            {"id": conversation_id}, {"$set": {"updated_at": now_iso()}}
        )
        logger.info(
            "Generation arretee : %d caracteres conserves (conv %s)",
            len(content), conversation_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Sauvegarde de la reponse partielle impossible")


# ---------------------------------------------------------------------------
# Mode autonome : la generation survit a la deconnexion du client.
#
# Chaque requete /chat/stream lance un *runner* en tache de fond qui consomme
# stream_ai_response et persiste le resultat final. Le generateur SSE ne fait
# plus que relayer les evenements : s'il est ferme (client parti), le runner
# continue jusqu'a la fin et marque le message `stopped: False`.
# ---------------------------------------------------------------------------
_ACTIVE_RUNS: dict = {}   # conversation_id -> {started_at, queue, tools, chars}


class _SSEQueue:
    """File d'evenements d'un tour, partagee entre le runner et le(s) client(s).

    Le runner y publie chaque evenement. Tant qu'au moins un client est abonne,
    il les recoit en direct. Si le client se deconnecte, le runner continue :
    les evenements restent dans la file (bornee) et sont ignores.
    """

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()
        self._closed = False

    async def put(self, ev: dict) -> None:
        await self._q.put(ev)

    def publish(self, ev: dict) -> None:
        """Version synchrone : ne bloque jamais le runner."""
        try:
            self._q.put_nowait(ev)
        except asyncio.QueueFull:  # pragma: no cover - file non bornee ici
            pass

    def close(self) -> None:
        self._closed = True
        self._q.put_nowait({"type": "__end__"})

    async def stream(self, ping_interval: float = 0.0):
        """Genere les evenements jusqu'a la fermeture. Utilisable par le SSE.

        Si `ping_interval` > 0, un evenement {"type": "__ping__"} est emis
        apres chaque periode d'inactivite. C'est un HEARTBEAT : il empeche les
        intermediaires (Nginx, Cloudflare, tunnels, mise en veille d'onglet)
        de fermer un flux SSE reste silencieux trop longtemps — cause classique
        du message navigateur brut "Error in input stream" qui coupait la
        reponse en plein vol.
        """
        while True:
            if ping_interval > 0:
                try:
                    ev = await asyncio.wait_for(
                        self._q.get(), timeout=ping_interval
                    )
                except asyncio.TimeoutError:
                    yield {"type": "__ping__"}
                    continue
            else:
                ev = await self._q.get()
            if ev.get("type") == "__end__":
                return
            yield ev



# ---------------------------------------------------------------------------
# Etapes autonomes : chaque etape = un texte d'intention SUIVI de son groupe
# d'outils. Une des qu'une etape est terminee (ses outils sont executes), elle
# est persiste en base immediatement. Les sorties brutes des etapes validees
# sont tronquees pour eviter de saturer la fenetre de contexte du modele.
# ---------------------------------------------------------------------------
def _truncate_step_logs(step: dict) -> dict:
    """Retourne une copie de l'etape avec les sorties d'outils tronquees.

    On garde un en-tete lisible (tete de la sortie) : le detail complet reste
    disponible dans le document Mongo avant troncature.
    """
    tools = []
    for t in step.get("tools") or []:
        out = t.get("output") or ""
        if len(out) > STEP_LOG_TRUNC:
            out = out[:STEP_LOG_TRUNC] + (
                f"\n... [sortie tronquee : {len(out)} caracteres au total]"
            )
        tools.append({**t, "output": out, "truncated": len(t.get("output") or "") > STEP_LOG_TRUNC})
    return {**step, "tools": tools}


def _new_step(index: int, intention: str) -> dict:
    return {
        "index": index,
        "intention": (intention or "").strip(),
        "tools": [],
        "status": "running",
        "created_at": now_iso(),
    }


async def _ensure_assistant_doc(database, conversation_id: str) -> dict:
    """Cree (une fois) le document assistant du tour, pret a recevoir ses etapes."""
    doc = {
        "id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": "",
        "has_image": False,
        "tool_steps": [],
        "steps": [],
        "provider": None,
        "model": None,
        "requested_provider": None,
        "fallback_used": False,
        "routing": [],
        "stopped": False,
        "created_at": now_iso(),
    }
    await database.messages.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _persist_step(database, message_id: str, step: dict) -> dict:
    """Persiste immediatement une etape terminee dans le document du tour."""
    finished = _truncate_step_logs({**step, "status": "done", "finished_at": now_iso()})
    truncated_tools = [
        {**t, "output": (t.get("output") or "")[:4000]} for t in step.get("tools") or []
    ]
    await database.messages.update_one(
        {"id": message_id},
        {
            "$push": {
                "steps": finished,
                "tool_steps": {"$each": truncated_tools},
            }
        },
    )
    return finished



async def _run_generation(
    database,
    conversation_id: str,
    history: list,
    prompt_text: str,
    images: list,
    attachments: list,
    provider: str,
    model: Optional[str],
    project: Optional[str],
    channel: "_SSEQueue",
    user_id: Optional[str] = None,
) -> None:
    """Execute une generation complete, independente de toute connexion client.

    Persiste toujours le resultat : `stopped=False` si la generation est allee
    a son terme, `stopped=True` seulement en cas d'erreur fatale.
    """
    # Le contexte projet/provider doit etre reaffirme dans la tache de fond :
    # les ContextVar ne sont PAS heritees par asyncio.create_task.
    set_current_project(project)
    set_current_provider(provider)
    _current_claude_model.set(model or "")
    # Fetcher de contexte : on re-resout la memoire du projet DANS cette tache
    # de fond (le ContextVar du bloc de contexte n'est pas herite non plus).
    await prepare_turn_context(user_id, project)

    chunks: list = []
    tool_steps: list = []          # liste plate (tronquee) : Fetcher + resume
    full_tool_steps: list = []     # sorties completes : extractions systeme
    meta: Optional[dict] = None
    stopped = False
    error_detail: Optional[str] = None

    # Document assistant cree a la volee : chaque etape terminee y est ajoutee
    # immediatement (persistance incrementale par etape).
    assist_doc: Optional[dict] = None
    message_id: Optional[str] = None

    # Etape courante : son texte d'intention + son groupe d'outils.
    step_index = 0
    current: Optional[dict] = None

    async def _finalize_step(final_status: str = "done") -> None:
        """Persiste en base l'etape courante et previent le client."""
        nonlocal current
        if current is None:
            return
        has_content = bool(current.get("intention")) or bool(current.get("tools"))
        if not has_content:
            current = None
            return
        if message_id:
            finished = await _persist_step(database, message_id, current)
            # On emet l'evenement de fin d'etape : le client cloture le cadre
            # et un event `step_done` empeche toute coupure partielle du flux.
            channel.publish({"type": "step_done", "step": finished})
        current = None

    def _start_step(intention: str = "") -> None:
        nonlocal current, step_index
        current = _new_step(step_index, intention)
        channel.publish({"type": "step_start", "index": step_index})
        step_index += 1

    try:
        async for ev in stream_ai_response(
            history=history,
            text=prompt_text,
            images=images,
            provider=provider,
            session_id=conversation_id,
            model=(model or "").strip() or None,
        ):
            if ev["type"] == "start":
                meta = {
                    "provider": ev["provider"],
                    "model": ev["model"],
                    "requested_provider": provider,
                    "fallback_used": False,
                    "attempts": [],
                }
                if assist_doc is None:
                    assist_doc = await _ensure_assistant_doc(database, conversation_id)
                    message_id = assist_doc["id"]
                channel.publish(ev)
            elif ev["type"] == "delta":
                chunks.append(ev["text"])
                # Un texte apres des outils = nouveau groupe : on cloture
                # l'etape precedente (deja persistee) et on en ouvre une autre.
                if current is None:
                    _start_step()
                elif current.get("tools") and not current.get("_sealed"):
                    # du texte arrive apres une salve d'outils : on scelle
                    await _finalize_step()
                    _start_step()
                current["intention"] = (current.get("intention") or "") + ev["text"]
                channel.publish(ev)
            elif ev["type"] == "tool":
                if assist_doc is None:
                    assist_doc = await _ensure_assistant_doc(database, conversation_id)
                    message_id = assist_doc["id"]
                if current is None:
                    _start_step()
                current["tools"].append(ev["step"])
                # Une fois qu'une salve d'outils est ouverte, le texte qui
                # suivra appartiendra a l'etape SUIVANTE (scellement).
                if current.get("intention"):
                    current["_sealed"] = True
                tool_steps.append(_truncate_step_logs(
                    {"tools": [ev["step"]]}
                )["tools"][0])
                full_tool_steps.append(ev["step"])
                _ACTIVE_RUNS.get(conversation_id, {})["tools"] = len(tool_steps)
                channel.publish(ev)
            elif ev["type"] == "error":
                stopped = True
                error_detail = ev["detail"]
                channel.publish(ev)
                break
            elif ev["type"] == "done":
                meta = ev

        # Fin de flux : on cloture et persiste l'etape en cours.
        await _finalize_step("done")

        if error_detail and not chunks and not tool_steps:
            # Rien a conserver : on laisse le client afficher l'erreur.
            channel.close()
            return

        if assist_doc is None:
            # Aucune etape : on cree tout de meme le document final (reponse
            # vide ou erreur) pour ne pas perdre le tour.
            assist_doc = await _ensure_assistant_doc(database, conversation_id)
            message_id = assist_doc["id"]

        final_meta = meta or {
            "provider": provider,
            "model": model or provider,
            "requested_provider": provider,
            "fallback_used": False,
            "attempts": [],
        }
        ai_doc = await _finalize_assistant(
            database, message_id, "".join(chunks), final_meta, stopped
        )
        conv = await database.conversations.find_one({"id": conversation_id})
        if conv:
            await _autotitle(database, conv, conversation_id, prompt_text, attachments)
        # Fetcher : les faits SYSTEME importants (chemins systeme, variables
        # d'environnement, decisions techniques) reveles par les outils de ce
        # tour sont memorises pour les prochaines conversations.
        if project and user_id:
            try:
                sys_facts = _extract_system_facts(full_tool_steps)
                if sys_facts:
                    await remember_context_facts(user_id, project, sys_facts, "system")
            except Exception:  # noqa: BLE001
                logger.warning("Fetcher: capture systeme impossible")
        channel.publish({"type": "done", "doc": ai_doc})
    except asyncio.CancelledError:  # pragma: no cover
        if current is not None:
            try:
                await _finalize_step("done")
            except Exception:  # noqa: BLE001
                pass
        if message_id and (chunks or tool_steps):
            try:
                await _finalize_assistant(
                    database, message_id, "".join(chunks), meta or {}, True
                )
            except Exception:  # noqa: BLE001
                pass
        raise
    except Exception:  # noqa: BLE001
        logger.exception("Generation en tache de fond interrompue")
        if current is not None:
            try:
                await _finalize_step("done")
            except Exception:  # noqa: BLE001
                pass
        if message_id and (chunks or tool_steps):
            try:
                await _finalize_assistant(
                    database, message_id, "".join(chunks),
                    meta or {
                        "provider": provider,
                        "model": model or provider,
                        "requested_provider": provider,
                        "fallback_used": False,
                        "attempts": [],
                    },
                    True,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Sauvegarde de secours impossible")
    finally:
        _ACTIVE_RUNS.pop(conversation_id, None)
        channel.close()


async def _finalize_assistant(
    database,
    message_id: str,
    content: str,
    meta: dict,
    stopped: bool,
) -> dict:
    """Cloture le document assistant : contenu final, meta, statut."""
    update = {
        "content": redact_secrets(content) or "(reponse vide)",
        "provider": meta.get("provider"),
        "model": meta.get("model"),
        "requested_provider": meta.get("requested_provider"),
        "fallback_used": meta.get("fallback_used", False),
        "routing": meta.get("attempts", []),
        "stopped": stopped,
    }
    await database.messages.update_one({"id": message_id}, {"$set": update})
    doc = await database.messages.find_one({"id": message_id}, {"_id": 0})
    return doc or {"id": message_id, **update}


async def _persist_assistant(
    database,
    conversation_id: str,
    content: str,
    tool_steps: list[dict],
    meta: dict,
    stopped: bool,
) -> dict:
    doc = {
        "id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": redact_secrets(content) or "(reponse vide)",
        "has_image": False,
        "tool_steps": tool_steps,
        "provider": meta.get("provider"),
        "model": meta.get("model"),
        "requested_provider": meta.get("requested_provider"),
        "fallback_used": meta.get("fallback_used", False),
        "routing": meta.get("attempts", []),
        "stopped": stopped,
        "created_at": now_iso(),
    }
    await database.messages.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _autotitle(database, conv, conversation_id, text, attachment) -> None:
    msg_count = await database.messages.count_documents(
        {"conversation_id": conversation_id}
    )
    update_fields = {"updated_at": now_iso()}
    if msg_count <= 2 and conv.get("title") in (None, "", "New Chat"):
        fallback = (
            f"Fichier : {attachment[0]['name']}" if attachment else "Image chat"
        )
        update_fields["title"] = (text or fallback)[:50]
    await database.conversations.update_one(
        {"id": conversation_id}, {"$set": update_fields}
    )


@api_router.get("/opencode/usage")
async def opencode_usage(current_user: dict = Depends(get_current_user)):
    """Consommation du forfait OpenCode Go (fenetres 5 h / semaine / mois)."""
    if not _provider_available("opencode"):
        return {"available": False, "reason": "OPENCODE_API_KEY absent"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as http:
            resp = await http.get(
                f"{settings.opencode_base_url}/usage",
                headers={
                    "Authorization": f"Bearer {settings.opencode_api_key}",
                    "User-Agent": settings.opencode_user_agent,
                },
            )
        if resp.status_code >= 400:
            return {
                "available": False,
                "reason": _http_error_detail(resp)[:200],
            }
        return {"available": True, **resp.json()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Usage OpenCode indisponible: %s", str(e)[:150])
        return {"available": False, "reason": str(e)[:200]}


@api_router.post("/chat/regenerate")
async def chat_regenerate(
    payload: RegenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """Regénère la DERNIÈRE réponse de l'assistant avec le même prompt utilisateur."""
    database = get_db()

    conv = await database.conversations.find_one(
        {"id": payload.conversation_id, "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Cadrage systeme : le LLM est cloisonne au projet de cette conversation.
    set_current_project(conv.get("project"))
    set_current_provider(payload.provider)
    set_current_reasoning_effort(payload.reasoning_effort)
    _current_claude_model.set(payload.model or "")

    # Seuls les derniers messages sont utiles (les plus anciens sont deja
    # couverts par le resume persiste) : inutile de relire 2000 documents.
    msgs = (
        await database.messages.find({"conversation_id": payload.conversation_id})
        .sort("created_at", -1)
        .to_list(max(2, settings.history_load_limit))
    )
    msgs.reverse()
    if not msgs or msgs[-1].get("role") != "assistant":
        raise HTTPException(
            status_code=400, detail="Aucune réponse assistant à régénérer."
        )

    old_assistant = msgs[-1]
    prior = msgs[:-1]
    if not prior or prior[-1].get("role") != "user":
        raise HTTPException(
            status_code=400, detail="Aucun message utilisateur à régénérer."
        )

    prompt_msg = prior[-1]
    history = await prepare_history(
        database, payload.conversation_id, prior[:-1]
    )
    raw_text = prompt_msg.get("content", "") or ""
    # Les libelles automatiques "(image)" / "(fichier : x)" ne sont pas du prompt.
    is_placeholder = raw_text.startswith("(") and raw_text.endswith(")") and (
        raw_text == "(image)" or "fichier" in raw_text
    )
    text = "" if is_placeholder else raw_text
    images = _images_from_message(prompt_msg)

    # Pieces jointes texte : on reinjecte le prompt complet du premier envoi.
    if prompt_msg.get("prompt_override"):
        text = prompt_msg["prompt_override"]
    elif prompt_msg.get("file_kind") == "text" and prompt_msg.get("file_text"):
        text = build_attachment_prompt(
            text,
            {
                "name": prompt_msg.get("file_name") or "fichier",
                "size": prompt_msg.get("file_size") or 0,
                "text": prompt_msg["file_text"],
            },
        )

    try:
        ai_response, tool_steps, meta = await generate_ai_response(
            history=history,
            text=text,
            images=images,
            provider=payload.provider,
            session_id=payload.conversation_id,
            model=(payload.model or "").strip() or None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Echec de la regeneration")
        raise HTTPException(status_code=500, detail="Une erreur est survenue lors de la generation IA. Consultez les logs du serveur.")

    new_doc = {
        "id": str(uuid.uuid4()),
        "conversation_id": payload.conversation_id,
        "role": "assistant",
        "content": redact_secrets(ai_response),
        "has_image": False,
        "tool_steps": tool_steps,
        "provider": meta["provider"],
        "model": meta["model"],
        "requested_provider": meta["requested_provider"],
        "fallback_used": meta["fallback_used"],
        "routing": meta["attempts"],
        "created_at": now_iso(),
    }
    await database.messages.delete_one({"id": old_assistant["id"]})
    await database.messages.insert_one(new_doc)
    await database.conversations.update_one(
        {"id": payload.conversation_id}, {"$set": {"updated_at": now_iso()}}
    )
    new_doc.pop("_id", None)
    return {"ai_message": new_doc}


@api_router.patch("/messages/{message_id}/feedback")
async def set_feedback(
    message_id: str,
    payload: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    """Enregistre un feedback (pouce haut/bas) sur un message assistant."""
    database = get_db()
    if payload.feedback not in (None, "up", "down"):
        raise HTTPException(status_code=400, detail="Invalid feedback value")

    msg = await database.messages.find_one({"id": message_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    conv = await database.conversations.find_one(
        {"id": msg["conversation_id"], "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Message not found")

    await database.messages.update_one(
        {"id": message_id}, {"$set": {"feedback": payload.feedback}}
    )
    return {"ok": True, "feedback": payload.feedback}


# =========================================================================
# Sante
# =========================================================================
async def _fetch_catalog(pid: str) -> list[str]:
    """Liste des modeles disponibles chez un provider distant (cache 10 min)."""
    cached = _CATALOG_CACHE.get(pid)
    if cached and (time.time() - cached[0]) < _CATALOG_TTL:
        return cached[1]

    # Claude : catalogue decouvert DYNAMIQUEMENT via l'API Anthropic
    # (GET /v1/models). Cet endpoint accepte le Bearer OAuth d'abonnement
    # (verifie en direct : le message d'erreur est "OAuth access token is
    # invalid", pas "x-api-key header is required"). En cas d'echec (reseau,
    # jeton expire), on retombe sur la liste statique CLAUDE_MODELS : aucune
    # regression possible.
    if pid == "claude":
        ids = list(CLAUDE_MODELS)
        if settings.claude_token:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(15.0, connect=5.0)
                ) as http:
                    resp = await http.get(
                        "https://api.anthropic.com/v1/models",
                        headers={
                            "authorization": f"Bearer {settings.claude_token}",
                            "anthropic-version": "2023-06-01",
                            "anthropic-beta": (
                                "oauth-2025-04-20,claude-code-20250219"
                            ),
                            "user-agent": "claude-cli/1.0.0 (external, cli)",
                            "x-app": "cli",
                        },
                        params={"limit": 100},
                    )
                    resp.raise_for_status()
                    discovered = [
                        m["id"]
                        for m in resp.json().get("data", [])
                        if m.get("id")
                    ]
                if discovered:
                    ids = discovered
                    logger.info(
                        "Catalogue Claude decouvert : %d modele(s)", len(ids)
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Catalogue Claude indisponible, repli statique: %s",
                    str(e)[:150],
                )
        # Le modele configure (CLAUDE_MODEL) doit toujours etre proposable,
        # meme s'il sort de la liste decouverte (ex: variante recente).
        configured = (settings.claude_model or "").strip()
        if configured and configured not in ids:
            ids.insert(0, configured)
        _CATALOG_CACHE[pid] = (time.time(), ids)
        return ids

    ids: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as http:
            if pid == "opencode":
                resp = await http.get(
                    f"{settings.opencode_base_url}/models",
                    headers={
                        "Authorization": f"Bearer {settings.opencode_api_key}",
                        "User-Agent": settings.opencode_user_agent,
                    },
                )
                resp.raise_for_status()
                ids = [m["id"] for m in resp.json().get("data", []) if m.get("id")]
            elif pid in FREE_PROVIDER_IDS:
                conf = settings.free_providers[pid]
                resp = await http.get(
                    f"{conf['base_url']}/models",
                    headers=_free_provider_headers(pid),
                )
                resp.raise_for_status()
                body = resp.json()
                raw = body.get("data") if isinstance(body, dict) else body
                ids = [m["id"] for m in (raw or []) if m.get("id")]
                if pid == "openrouter" and settings.openrouter_free_only:
                    # Ne garder que les modeles gratuits.
                    ids = [i for i in ids if ":free" in i]
            elif pid == "ollama_cloud":
                resp = await http.get(
                    f"{settings.ollama_cloud_url}/tags",
                    headers={
                        "Authorization": f"Bearer {settings.ollama_cloud_api_key}"
                    },
                )
                resp.raise_for_status()
                ids = [
                    m["name"] for m in resp.json().get("models", []) if m.get("name")
                ]
    except Exception as e:  # noqa: BLE001
        logger.warning("Catalogue %s indisponible: %s", pid, str(e)[:150])
        return cached[1] if cached else []

    ids.sort()
    _CATALOG_CACHE[pid] = (time.time(), ids)
    return ids


@api_router.get("/models")
async def list_models(current_user: dict = Depends(get_current_user)):
    """Providers detectes dynamiquement + modele reel et catalogue de chacun."""
    catalogs: dict[str, list[str]] = {}
    # Claude expose un catalogue statique : on l'inclut explicitement pour que
    # le selecteur de modele precis s'affiche aussi pour ce provider.
    for pid in ("claude", "opencode", "ollama_cloud", *FREE_PROVIDER_IDS):
        if _provider_available(pid):
            catalogs[pid] = await _fetch_catalog(pid)

    providers = [
        {
            "id": pid,
            "label": PROVIDER_LABELS[pid],
            "model": _provider_model(pid),
            "available": _provider_available(pid),
            "local": pid == "ollama",
            "models": catalogs.get(pid, []),
        }
        for pid in PROVIDER_IDS
    ]
    chain = [p for p in _build_chain("auto") if _provider_available(p)]
    return {
        "providers": providers,
        "auto": {
            "id": "auto",
            "label": "Auto (meilleur dispo)",
            "chain": chain,
            "available": bool(chain),
        },
        "priority": settings.provider_priority,
        "enable_fallback": settings.enable_fallback,
        "enable_tools": settings.enable_tools,
    }


# =========================================================================
# GitHub — sauvegarde du workspace sur un depot de l'utilisateur
# =========================================================================
GITHUB_API = "https://api.github.com"


class GithubTokenRequest(BaseModel):
    token: str


class GithubPushRequest(BaseModel):
    repo: str  # "owner/name"
    branch: str
    message: Optional[str] = None
    project: Optional[str] = None
    conversation_id: Optional[str] = None
    create_if_missing: bool = False
    private: bool = True


# =========================================================================
# Chiffrement au repos du jeton GitHub (E4)
# =========================================================================
# Cle symetrique derivee de JWT_SECRET (deja presente dans .env) : aucune
# variable d'environnement supplementaire a gerer, aucune dependance lourde.
# Fernet == AES-128-CBC + HMAC-SHA256, standard et suffisant pour ce cas.
_fernet_instance: Optional[Fernet] = None


def _github_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        base = (settings.jwt_secret or "insecure-fallback-key").encode("utf-8")
        derived = hashlib.sha256(base).digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(derived))
    return _fernet_instance


def _encrypt_github_token(token: str) -> str:
    return _github_fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def _decrypt_github_token(enc: str) -> str:
    try:
        return _github_fernet().decrypt(enc.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Jeton corrompu/ancien format : on force un re-enregistrement propre.
        return ""


async def _github_token(user_id: str) -> tuple[str, str]:
    """Jeton a utiliser + origine ('ui' | 'env' | 'none')."""
    doc = await get_db().settings.find_one(
        {"key": "github_token", "user_id": user_id}
    )
    enc_token = (doc or {}).get("token") or ""
    token = _decrypt_github_token(enc_token) if enc_token else ""
    if token:
        return token, "ui"
    if settings.github_pat:
        return settings.github_pat, "env"
    return "", "none"


async def _github_token_last4(user_id: str) -> str:
    """4 derniers caracteres du jeton stocke, sans jamais exposer le jeton complet."""
    doc = await get_db().settings.find_one(
        {"key": "github_token", "user_id": user_id}
    )
    if doc and doc.get("token_last4"):
        return doc["token_last4"]
    if settings.github_pat and len(settings.github_pat) >= 4:
        return settings.github_pat[-4:]
    return ""


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _git(args: list[str], cwd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    # safe.directory=* : le workspace peut appartenir a un autre utilisateur
    # que celui qui fait tourner le service.
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def _workspace_root() -> Optional[Path]:
    root = Path(settings.workspace_root)
    return root if root.is_dir() else None


def list_workspace_projects() -> list[dict]:
    """Sous-dossiers de WORKSPACE_ROOT (un par projet), les plus recents d'abord."""
    root = _workspace_root()
    out: list[dict] = []
    if root:
        for d in sorted(
            [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            out.append({"name": d.name, "path": str(d), "is_git_repo": (d / ".git").exists()})
    elif Path(settings.workspace_dir).is_dir():
        # Repli mono-projet (dev local / sandbox).
        d = Path(settings.workspace_dir)
        out.append({"name": d.name, "path": str(d), "is_git_repo": (d / ".git").exists()})
    return out


async def resolve_project_dir(
    project: Optional[str], conversation_id: Optional[str], user_id: str
) -> tuple[str, Optional[str]]:
    """Repertoire cible : projet demande > projet lie a la conversation > repli."""
    name = (project or "").strip()
    if not name and conversation_id:
        conv = await get_db().conversations.find_one(
            {"id": conversation_id, "user_id": user_id}, {"project": 1}
        )
        name = ((conv or {}).get("project") or "").strip()

    projects = list_workspace_projects()
    known = {p["name"]: p["path"] for p in projects}
    if name:
        if name not in known:
            raise HTTPException(
                status_code=400,
                detail=f"Projet inconnu dans {settings.workspace_root} : {name}",
            )
        return known[name], name
    if len(projects) == 1:
        return projects[0]["path"], projects[0]["name"]
    raise HTTPException(
        status_code=409,
        detail="Aucun projet associe a cette session. Choisis le dossier cible.",
    )


async def _gh_api(token: str, path: str, params: Optional[dict] = None):
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(
            f"{GITHUB_API}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Jeton GitHub refuse (401). Verifie qu'il est valide et non expire.",
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502, detail=f"GitHub {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _valid_project_name(name: str) -> str:
    n = (name or "").strip().strip("/")
    if not n or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", n) or n.startswith("."):
        raise HTTPException(
            status_code=400,
            detail="Nom de projet invalide (lettres, chiffres, . _ - uniquement).",
        )
    return n


def _ensure_project_dir(name: str) -> Path:
    root = Path(settings.workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    d = root / name
    d.mkdir(exist_ok=True)
    return d


def _default_preview_url(name: str) -> str:
    """URL HTTPS automatique du projet (wildcard DNS deja en place)."""
    return f"https://{name}.{settings.preview_domain}" if settings.preview_domain else ""


_preview_map_lock = asyncio.Lock()
_preview_map_pending = False
_preview_manager: Optional[PreviewManager] = None
# Dernier resultat reel de la regeneration de la map (interrogeable par l'API :
# un echec silencieux etait la cause des previews en 503).
_preview_map_last: dict = {"at": None, "ok": None, "code": None, "output": "", "hint": ""}

_READONLY_HINT = (
    "Ecriture refusee : le service est monte en lecture seule. Ajoute "
    "/etc/nginx /run /var/log/nginx a ReadWritePaths= et passe "
    "NoNewPrivileges=false dans forge-backend.service (ou lance "
    "`sudo bash deploy/fix-previews.sh`), puis `systemctl daemon-reload && "
    "systemctl restart forge-backend`. Voir "
    "deploy/KNOWN_ISSUE_preview_map_readonly.md"
)


async def _exec_preview_map_cmd() -> dict:
    """Execute la commande de regeneration et retourne son resultat reel."""
    global _preview_map_last
    cmd = settings.preview_map_refresh_cmd
    res = {"at": now_iso(), "ok": False, "code": None, "output": "", "hint": "", "command": cmd}
    if not cmd:
        res["hint"] = "PREVIEW_MAP_REFRESH_CMD n'est pas configure."
        _preview_map_last = res
        return res
    try:
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(cmd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(
                proc.communicate(), timeout=settings.preview_map_refresh_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            res["output"] = f"timeout apres {settings.preview_map_refresh_timeout}s"
            res["hint"] = "la commande ne rend jamais la main"
        else:
            res["code"] = proc.returncode
            res["output"] = (out or b"").decode("utf-8", "replace")[-2000:]
            res["ok"] = proc.returncode == 0
            low = res["output"]
            if not res["ok"]:
                if "Read-only file system" in low or "Permission denied" in low:
                    res["hint"] = _READONLY_HINT
                elif "no new privileges" in low.lower() or "sudo:" in low:
                    res["hint"] = (
                        "sudo refuse : NoNewPrivileges=true dans le service, ou regle "
                        "sudoers absente (cf. deploy/fix-previews.sh)."
                    )
    except FileNotFoundError:
        res["output"] = f"commande introuvable : {cmd}"
        res["hint"] = "verifie PREVIEW_MAP_REFRESH_CMD et le chemin du script."
    except Exception as exc:  # noqa: BLE001
        res["output"] = f"erreur inattendue : {exc}"

    if res["ok"]:
        logger.info("Preview map Nginx regeneree avec succes.")
    else:
        logger.warning(
            "Preview map : ECHEC (code %s) %s | %s",
            res["code"],
            res["hint"],
            res["output"][-400:],
        )
    _preview_map_last = res
    return res


async def _run_preview_map_refresh() -> None:
    """Regenere la map Nginx des previews. Ne leve jamais : log uniquement."""
    global _preview_map_pending
    if not settings.preview_map_refresh_cmd:
        return
    if _preview_map_lock.locked():
        # Une execution est deja en cours : on demande juste un re-run apres.
        _preview_map_pending = True
        return
    async with _preview_map_lock:
        while True:
            _preview_map_pending = False
            await _exec_preview_map_cmd()
            if not _preview_map_pending:
                return


def _schedule_preview_map_refresh() -> None:
    """Declenche la regeneration de la map Nginx en tache de fond (non bloquant)."""
    if not settings.preview_map_auto_refresh:
        return
    try:
        asyncio.get_running_loop().create_task(_run_preview_map_refresh())
    except RuntimeError:
        logger.warning("Preview map: aucune boucle asyncio active, refresh ignore.")


async def _assign_project_meta(
    user_id: str, name: str, preview_url: Optional[str] = None
) -> dict:
    """Cree/complete les metadonnees : URL de preview + port interne dedie."""
    database = get_db()
    doc = await database.projects.find_one({"user_id": user_id, "name": name}, {"_id": 0})
    if doc and doc.get("preview_port") and (doc.get("preview_url") or not preview_url):
        if preview_url and preview_url != doc.get("preview_url"):
            await database.projects.update_one(
                {"user_id": user_id, "name": name},
                {"$set": {"preview_url": preview_url, "updated_at": now_iso()}},
            )
            doc["preview_url"] = preview_url
            _schedule_preview_map_refresh()
        return doc

    port = (doc or {}).get("preview_port")
    if not port:
        used = {
            d.get("preview_port")
            for d in await database.projects.find({}, {"preview_port": 1}).to_list(500)
            if d.get("preview_port")
        }
        port = next(
            (
                p
                for p in range(settings.preview_port_base, settings.preview_port_max + 1)
                if p not in used
            ),
            settings.preview_port_base,
        )

    url = preview_url or (doc or {}).get("preview_url") or _default_preview_url(name)
    await database.projects.update_one(
        {"user_id": user_id, "name": name},
        {
            "$set": {"preview_url": url, "preview_port": port, "updated_at": now_iso()},
            "$setOnInsert": {"created_at": now_iso()},
        },
        upsert=True,
    )
    # Nouveau projet / nouveau port / nouvelle URL -> map Nginx a regenerer.
    _schedule_preview_map_refresh()
    return {"name": name, "preview_url": url, "preview_port": port}


async def _projects_meta(user_id: str) -> dict:
    docs = await get_db().projects.find({"user_id": user_id}, {"_id": 0}).to_list(500)
    return {d["name"]: d for d in docs}


@api_router.get("/workspace/projects")
async def workspace_projects(current_user: dict = Depends(get_current_user)):
    """Projets du workspace + URL de preview et nb de conversations.

    Performance : les N projets etaient traites en boucle SEQUENTIELLE (2-3
    appels `await` chacun : meta, count Mongo, statut preview), ce qui donnait
    un temps de reponse proportionnel au nombre de projets. On regroupe
    desormais les appels par type et on les parallelise via asyncio.gather.
    """
    database = get_db()
    uid = current_user["id"]
    meta = await _projects_meta(uid)
    projects = list_workspace_projects()

    # 1) Metadonnees manquantes : on ne complete (et n'ecrit) que si necessaire,
    #    en parallele. C'est la seule etape susceptible d'ecrire en base.
    to_assign = [
        p["name"] for p in projects
        if not (meta.get(p["name"]) or {}).get("preview_url")
        or not (meta.get(p["name"]) or {}).get("preview_port")
    ]
    if to_assign:
        assigned = await asyncio.gather(
            *(_assign_project_meta(uid, n) for n in to_assign)
        )
        for n, m in zip(to_assign, assigned):
            meta[n] = m

    # 2) Compteurs de conversations : un seul gather pour tous les projets.
    counts = await asyncio.gather(
        *(
            database.conversations.count_documents(
                {"user_id": uid, "project": p["name"]}
            )
            for p in projects
        )
    )

    # 3) Assemblage (statut preview : lecture synchrone en memoire, sans I/O).
    for p, conv_count in zip(projects, counts):
        m = meta.get(p["name"]) or {}
        p["preview_url"] = m.get("preview_url") or ""
        p["preview_port"] = m.get("preview_port")
        p["conversations"] = conv_count
        if p["preview_port"]:
            st = preview_mgr().status(p["name"], p["preview_port"])
            p["preview_phase"] = st["phase"]
            p["preview_message"] = st["message"]
    return {"root": settings.workspace_root, "projects": projects}


@api_router.post("/workspace/projects")
async def create_project(
    payload: CreateProjectRequest, current_user: dict = Depends(get_current_user)
):
    """Cree le sous-dossier du projet dans WORKSPACE_ROOT."""
    name = _valid_project_name(payload.name)
    d = _ensure_project_dir(name)
    invalidate_project_snapshot(name)
    meta = await _assign_project_meta(
        current_user["id"], name, (payload.preview_url or "").strip() or None
    )
    # Fetcher : ouverture d'un nouveau projet -> premier snapshot memorise.
    try:
        await fetch_and_store_project(current_user["id"], name, "system")
    except Exception:  # noqa: BLE001
        logger.warning("Fetcher: snapshot a la creation impossible")
    return {
        "name": name,
        "path": str(d),
        "preview_url": meta.get("preview_url", ""),
        "preview_port": meta.get("preview_port"),
        "is_git_repo": (d / ".git").exists(),
        "conversations": 0,
    }


@api_router.post("/workspace/projects/import")
async def import_project(
    file: UploadFile = File(...),
    name: str = Form(""),
    current_user: dict = Depends(get_current_user),
):
    """Importe un projet depuis une archive .zip (upload direct, export GitHub,
    ou backup genere par /export). Deballe intelligemment un eventuel dossier
    racine unique (cas des exports GitHub type ``repo-main/``), detecte le nom
    du projet depuis le champ ``name`` ou depuis le nom du fichier, et cree les
    metadonnees necessaires pour que la preview soit immediatement disponible."""
    orig_filename = file.filename or "projet.zip"
    if not orig_filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Seules les archives .zip sont acceptees.")

    base_name = (name or "").strip() or re.sub(r"\.zip$", "", orig_filename, flags=re.IGNORECASE)
    base_name = _valid_project_name(base_name)

    root = Path(settings.workspace_root)
    root.mkdir(parents=True, exist_ok=True)

    # Nom unique : si le dossier existe deja, on suffixe (jamais d'ecrasement silencieux).
    pname = base_name
    n = 1
    while (root / pname).exists():
        n += 1
        pname = f"{base_name}-{n}"
        _valid_project_name(pname)

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = tmp.name
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()

        if not zipfile.is_zipfile(tmp_path):
            raise HTTPException(status_code=400, detail="Fichier .zip invalide ou corrompu.")

        project_dir = root / pname
        with zipfile.ZipFile(tmp_path) as zf:
            names = [n for n in zf.namelist() if n and not n.endswith("/")]
            if not names:
                raise HTTPException(status_code=400, detail="L'archive .zip est vide.")

            # Protection anti zip-slip : aucun chemin ne doit sortir de l'archive.
            for member in zf.namelist():
                mpath = Path(member)
                if mpath.is_absolute() or ".." in mpath.parts:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Archive invalide (chemin dangereux) : {member}",
                    )

            # Deballage intelligent : si tous les fichiers partagent un meme
            # dossier racine (cas des exports GitHub "repo-main/..."), on
            # l'ignore pour ne pas ajouter un niveau de dossier inutile.
            top_dirs = {n.split("/", 1)[0] for n in names if "/" in n}
            single_root = (
                top_dirs.pop() if len(top_dirs) == 1 and all("/" in n for n in names)
                else None
            )

            project_dir.mkdir(parents=True, exist_ok=True)
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                rel = member
                if single_root and rel.startswith(single_root + "/"):
                    rel = rel[len(single_root) + 1 :]
                if not rel:
                    continue
                target = project_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src_f, open(target, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Import impossible : {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    meta = await _assign_project_meta(current_user["id"], pname)
    return {
        "name": pname,
        "path": str(root / pname),
        "preview_url": meta.get("preview_url", ""),
        "preview_port": meta.get("preview_port"),
        "is_git_repo": (root / pname / ".git").exists(),
        "conversations": 0,
    }


@api_router.put("/workspace/projects/{name}")
async def update_project(
    name: str,
    payload: ProjectUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """Met a jour l'URL de preview du projet (http(s)://... ou vide)."""
    pname = _valid_project_name(name)
    url = (payload.preview_url or "").strip()
    if url and not re.match(r"^https?://[^\s]+$", url):
        raise HTTPException(
            status_code=400,
            detail="URL de preview invalide (attendu http://... ou https://...).",
        )
    meta = await _assign_project_meta(
        current_user["id"], pname, url or _default_preview_url(pname)
    )
    return {
        "name": pname,
        "preview_url": meta.get("preview_url", ""),
        "preview_port": meta.get("preview_port"),
    }


# Dossiers/fichiers exclus des exports .zip : dependances reinstallables ou
# volumineuses, jamais des donnees utiles au projet.
_EXPORT_EXCLUDE_DIRS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv", "env",
    "dist", "build", ".next", ".turbo", ".cache",
}


@api_router.get("/workspace/projects/{name}/export")
async def export_project(
    name: str,
    current_user: dict = Depends(get_current_user),
):
    """Exporte le dossier du projet en .zip (backup telechargeable).
    Exclut les dependances reinstallables (node_modules, venv, __pycache__...)
    pour garder l'archive legere."""
    pname = _valid_project_name(name)
    root = Path(settings.workspace_root)
    project_dir = root / pname
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Projet inconnu : {pname}")

    try:
        project_dir.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Chemin de projet invalide.")

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(project_dir):
                dirnames[:] = [d for d in dirnames if d not in _EXPORT_EXCLUDE_DIRS]
                for fname in filenames:
                    fpath = Path(dirpath) / fname
                    arcname = Path(pname) / fpath.relative_to(project_dir)
                    try:
                        zf.write(fpath, arcname)
                    except OSError as exc:  # noqa: BLE001
                        logger.warning("Export %s : fichier ignore (%s): %s", pname, fpath, exc)
    except Exception as exc:  # noqa: BLE001
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Export impossible : {exc}")

    def _cleanup():
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"{pname}.zip",
        background=BackgroundTask(_cleanup),
    )


@api_router.delete("/workspace/projects/{name}")
async def delete_project(
    name: str,
    current_user: dict = Depends(get_current_user),
):
    """Supprime definitivement un projet : arrete sa preview, efface son
    dossier sur disque et purge ses conversations/messages/metadonnees.
    Action irreversible, confirmee cote frontend."""
    pname = _valid_project_name(name)
    root = Path(settings.workspace_root)
    project_dir = root / pname
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Projet inconnu : {pname}")

    # Securite : le dossier resolu doit bien rester sous WORKSPACE_ROOT.
    try:
        project_dir.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Chemin de projet invalide.")

    database = get_db()
    meta = await database.projects.find_one(
        {"user_id": current_user["id"], "name": pname}, {"_id": 0, "preview_port": 1}
    )
    if meta and meta.get("preview_port"):
        try:
            await preview_mgr().stop(pname, meta["preview_port"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Arret preview avant suppression de %s : %s", pname, exc)

    try:
        shutil.rmtree(project_dir)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Suppression du dossier impossible : {exc}"
        )
    invalidate_project_snapshot(pname)

    conv_ids = [
        c["id"]
        async for c in database.conversations.find(
            {"user_id": current_user["id"], "project": pname}, {"id": 1}
        )
    ]
    if conv_ids:
        await database.messages.delete_many({"conversation_id": {"$in": conv_ids}})
        await database.conversations.delete_many(
            {"user_id": current_user["id"], "project": pname}
        )
    await database.projects.delete_one({"user_id": current_user["id"], "name": pname})
    _schedule_preview_map_refresh()
    return {"ok": True, "name": pname}


def preview_mgr() -> PreviewManager:
    """Gestionnaire de previews (un seul par racine de workspace)."""
    global _preview_manager
    if (
        _preview_manager is None
        or str(_preview_manager.workspace_root) != settings.workspace_root
    ):
        _preview_manager = PreviewManager(settings.workspace_root)
    return _preview_manager


async def _project_port(user_id: str, name: str) -> tuple[str, dict]:
    pname = _valid_project_name(name)
    if not (Path(settings.workspace_root) / pname).is_dir():
        raise HTTPException(status_code=404, detail=f"Projet inconnu : {pname}")
    meta = await _assign_project_meta(user_id, pname)
    return pname, meta


def _preview_payload(meta: dict, status: dict) -> dict:
    return {**status, "preview_url": meta.get("preview_url", "")}


@api_router.post("/workspace/projects/{name}/preview/start")
async def preview_start(
    name: str,
    restart: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Demarre (ou redemarre) l'application du projet sur son port dedie."""
    pname, meta = await _project_port(current_user["id"], name)
    status = await preview_mgr().start(pname, meta["preview_port"], restart=restart)
    # Le projet doit aussi figurer dans la map Nginx, sinon 503 malgre un
    # serveur de dev qui tourne.
    _schedule_preview_map_refresh()
    await get_db().projects.update_one(
        {"user_id": current_user["id"], "name": pname},
        {"$set": {"preview_running": status["phase"] != "error", "updated_at": now_iso()}},
    )
    return _preview_payload(meta, status)


@api_router.post("/workspace/projects/{name}/preview/restart")
async def preview_restart(name: str, current_user: dict = Depends(get_current_user)):
    return await preview_start(name, restart=True, current_user=current_user)


@api_router.post("/workspace/projects/{name}/preview/stop")
async def preview_stop(name: str, current_user: dict = Depends(get_current_user)):
    """Arrete l'application du projet (libere le port et la RAM)."""
    pname, meta = await _project_port(current_user["id"], name)
    status = await preview_mgr().stop(pname, meta["preview_port"])
    await get_db().projects.update_one(
        {"user_id": current_user["id"], "name": pname},
        {"$set": {"preview_running": False, "updated_at": now_iso()}},
    )
    return _preview_payload(meta, status)


@api_router.get("/workspace/projects/{name}/preview/status")
async def preview_status(name: str, current_user: dict = Depends(get_current_user)):
    """Etat de la preview : stopped / installing / starting / running / error.
    `targets` detaille le frontend (port de preview) et le backend (port + 100)."""
    pname, meta = await _project_port(current_user["id"], name)
    preview_mgr().touch(pname)
    status = preview_mgr().status(pname, meta["preview_port"])
    return _preview_payload(meta, status)


@api_router.get("/workspace/projects/{name}/preview/logs")
async def preview_logs(
    name: str, target: str = "", current_user: dict = Depends(get_current_user)
):
    """Journal du process de preview (derniers Ko). target = web | api | app."""
    pname, _ = await _project_port(current_user["id"], name)
    preview_mgr().touch(pname)
    return {
        "project": pname,
        "target": target,
        "logs": preview_mgr().logs(pname, target),
    }


async def _reap_idle_previews() -> None:
    """Arrete les previews sans activite (aucun start/status/logs recu) depuis
    PREVIEW_IDLE_TIMEOUT secondes. 0 = desactive."""
    limit = settings.preview_idle_timeout
    if limit <= 0:
        return
    while True:
        await asyncio.sleep(60)
        try:
            mgr = preview_mgr()
            for name in mgr.idle_projects(limit):
                doc = await db.projects.find_one({"name": name}, {"preview_port": 1})
                port = (doc or {}).get("preview_port")
                if not port:
                    continue
                await mgr.stop(name, port)
                await db.projects.update_many(
                    {"name": name},
                    {"$set": {"preview_running": False, "updated_at": now_iso()}},
                )
                logger.info("Preview %s arretee (inactive > %ss)", name, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reaper previews : %s", exc)


async def _autostart_previews() -> None:
    """Relance au demarrage du backend les previews qui tournaient (anti-502
    apres reboot ou `systemctl restart forge-backend`)."""
    if not settings.preview_autostart:
        return
    try:
        docs = await db.projects.find(
            {"preview_running": True}, {"name": 1, "preview_port": 1}
        ).to_list(200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Autostart previews : base illisible (%s)", exc)
        return
    mgr = preview_mgr()
    for doc in docs:
        name, port = doc.get("name"), doc.get("preview_port")
        if not name or not port:
            continue
        if not (Path(settings.workspace_root) / name).is_dir():
            continue
        try:
            st = await mgr.start(name, port)
            logger.info("Autostart preview %s (port %s) : %s", name, port, st["phase"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Autostart preview %s impossible : %s", name, exc)


@api_router.post("/workspace/preview-map/refresh")
async def refresh_preview_map(current_user: dict = Depends(get_current_user)):
    """Regenere la map Nginx et RETOURNE LE RESULTAT REEL (plus de 200 muet)."""
    if not settings.preview_map_refresh_cmd:
        raise HTTPException(
            status_code=400,
            detail="PREVIEW_MAP_REFRESH_CMD n'est pas configure.",
        )
    async with _preview_map_lock:
        res = await _exec_preview_map_cmd()
    if not res["ok"]:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Regeneration de la map echouee (code {res['code']}). "
                f"{res['hint']} Sortie : {res['output'][-600:]}"
            ),
        )
    return res


@api_router.get("/workspace/preview-map")
async def get_preview_map(current_user: dict = Depends(get_current_user)):
    """Diagnostic anti-503 : dernier resultat de regeneration + derive entre la
    map Nginx et la collection `projects`."""
    map_file = os.environ.get("PREVIEW_MAP_FILE", "/etc/nginx/forge-preview-ports.map")
    mapped: dict[str, int] = {}
    readable = False
    try:
        with open(map_file, encoding="utf-8") as fh:
            readable = True
            for line in fh:
                line = line.strip().rstrip(";")
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit():
                    mapped[parts[0]] = int(parts[1])
    except OSError:
        pass

    docs = await get_db().projects.find(
        {"user_id": current_user["id"]}, {"name": 1, "preview_port": 1}
    ).to_list(500)
    expected = {
        d["name"]: d.get("preview_port")
        for d in docs
        if d.get("preview_port") and (Path(settings.workspace_root) / d["name"]).is_dir()
    }
    missing = [n for n, p in expected.items() if mapped.get(n) != p]
    return {
        "map_file": os.path.basename(map_file),
        "readable": readable,
        "mapped": mapped,
        "expected": expected,
        "missing": missing,
        "in_sync": not missing,
        "last_refresh": _preview_map_last,
        "command": settings.preview_map_refresh_cmd,
    }


@api_router.get("/audit/export-pdf")
async def export_audit_pdf(current_user: dict = Depends(get_current_user)):
    """Genere et renvoie AUDIT_SECURITE.md au format PDF (conversion Markdown
    -> PDF via reportlab, sans dependance systeme externe)."""
    audit_path = ROOT_DIR.parent / "AUDIT_SECURITE.md"
    if not audit_path.is_file():
        raise HTTPException(status_code=404, detail="AUDIT_SECURITE.md introuvable.")

    try:
        md_text = audit_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.exception("Lecture AUDIT_SECURITE.md impossible")
        raise HTTPException(status_code=500, detail="Lecture du rapport d'audit impossible.")

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
        from reportlab.lib.enums import TA_LEFT

        styles = getSampleStyleSheet()
        style_h1 = ParagraphStyle("AuditH1", parent=styles["Heading1"], fontSize=16, spaceAfter=10)
        style_h2 = ParagraphStyle("AuditH2", parent=styles["Heading2"], fontSize=13, spaceBefore=12, spaceAfter=6)
        style_h3 = ParagraphStyle("AuditH3", parent=styles["Heading3"], fontSize=11, spaceBefore=8, spaceAfter=4)
        style_body = ParagraphStyle("AuditBody", parent=styles["BodyText"], fontSize=9.5, leading=13, alignment=TA_LEFT)
        style_bullet = ParagraphStyle("AuditBullet", parent=style_body, leftIndent=0)

        def esc(txt: str) -> str:
            txt = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
            txt = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", txt)
            return txt

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            topMargin=1.8 * cm, bottomMargin=1.8 * cm,
            leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        )
        flow = []
        bullets: list[str] = []

        def flush_bullets():
            if bullets:
                items = [ListItem(Paragraph(esc(b), style_bullet)) for b in bullets]
                flow.append(ListFlowable(items, bulletType="bullet", leftIndent=14))
                bullets.clear()

        for raw_line in md_text.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                flush_bullets()
                flow.append(Spacer(1, 4))
                continue
            if line.startswith("### "):
                flush_bullets()
                flow.append(Paragraph(esc(line[4:]), style_h3))
            elif line.startswith("## "):
                flush_bullets()
                flow.append(Paragraph(esc(line[3:]), style_h2))
            elif line.startswith("# "):
                flush_bullets()
                flow.append(Paragraph(esc(line[2:]), style_h1))
            elif line.strip().startswith(("- ", "* ")):
                bullets.append(line.strip()[2:])
            elif line.strip() == "---":
                flush_bullets()
                flow.append(Spacer(1, 6))
            else:
                flush_bullets()
                flow.append(Paragraph(esc(line), style_body))
        flush_bullets()

        doc.build(flow)
        pdf_bytes = buf.getvalue()
        buf.close()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Generation PDF de l'audit impossible")
        raise HTTPException(status_code=500, detail="Generation du PDF impossible. Consultez les logs du serveur.")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="AUDIT_SECURITE.pdf"'},
    )


@api_router.post("/conversations/{conv_id}/project")
async def link_conversation_project(
    conv_id: str,
    payload: CreateProjectRequest,
    current_user: dict = Depends(get_current_user),
):
    """Rattache une conversation a un projet (cree le dossier si besoin)."""
    name = _valid_project_name(payload.name)
    _ensure_project_dir(name)
    invalidate_project_snapshot(name)
    meta = await _assign_project_meta(current_user["id"], name)
    res = await get_db().conversations.update_one(
        {"id": conv_id, "user_id": current_user["id"]},
        {"$set": {"project": name, "updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Fetcher : rattachement conversation<->projet = moment d'ouverture.
    try:
        await fetch_and_store_project(current_user["id"], name, "system")
    except Exception:  # noqa: BLE001
        logger.warning("Fetcher: consolidation au rattachement impossible")
    # Toujours resynchroniser la map, meme si le projet existait deja : un lien
    # conversation<->projet fait hors du flux normal laissait la map perimee.
    _schedule_preview_map_refresh()
    return {"id": conv_id, "project": name, "preview_url": meta.get("preview_url", "")}


async def _gh_post(token: str, path: str, payload: dict):
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"{GITHUB_API}{path}",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Jeton GitHub refuse (401).")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502, detail=f"GitHub {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


async def _ensure_repo(token: str, repo: str, private: bool) -> dict:
    """Retourne le depot, en le creant s'il n'existe pas encore."""
    owner, name = repo.split("/", 1)
    try:
        return await _gh_api(token, f"/repos/{owner}/{name}")
    except HTTPException as e:
        if e.status_code == 401:
            raise
    me = await _gh_api(token, "/user")
    login = (me or {}).get("login") or ""
    payload = {"name": name, "private": private, "auto_init": False}
    if owner.lower() == login.lower():
        created = await _gh_post(token, "/user/repos", payload)
    else:
        # Depot sous une organisation.
        created = await _gh_post(token, f"/orgs/{owner}/repos", payload)
    return created


@api_router.get("/github/status")
async def github_status(
    project: Optional[str] = None,
    conversation_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    token, source = await _github_token(current_user["id"])
    out = {
        "configured": bool(token),
        "source": source,
        "token_last4": await _github_token_last4(current_user["id"]),
        "root": settings.workspace_root,
        "projects": [p["name"] for p in list_workspace_projects()],
        "project": None,
        "workspace": None,
        "login": None,
        "branch": None,
        "changes": 0,
        "is_git_repo": False,
    }
    if token:
        try:
            out["login"] = (await _gh_api(token, "/user")).get("login")
        except HTTPException as e:
            out["error"] = str(e.detail)
            out["configured"] = False

    try:
        work, name = await resolve_project_dir(
            project, conversation_id, current_user["id"]
        )
        out["workspace"] = work
        out["project"] = name
        # Depot propre au projet uniquement : on ne remonte jamais vers un
        # depot parent (sinon git status du parent fausserait tout).
        if not (Path(work) / ".git").exists():
            return out
        head = _git(["rev-parse", "--abbrev-ref", "HEAD"], work, timeout=15)
        if head.returncode == 0:
            out["is_git_repo"] = True
            out["branch"] = head.stdout.strip()
        st = _git(["status", "--porcelain"], work, timeout=60)
        if st.returncode == 0:
            out["changes"] = len([l for l in st.stdout.splitlines() if l.strip()])
    except HTTPException as e:
        out["needs_project"] = True
        out["error"] = str(e.detail)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"git indisponible: {str(e)[:200]}"
    return out


@api_router.post("/github/token")
async def github_set_token(
    payload: GithubTokenRequest, current_user: dict = Depends(get_current_user)
):
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Jeton vide.")
    login = (await _gh_api(token, "/user")).get("login")
    encrypted = _encrypt_github_token(token)
    await get_db().settings.update_one(
        {"key": "github_token", "user_id": current_user["id"]},
        {
            "$set": {
                "token": encrypted,
                "token_last4": token[-4:] if len(token) >= 4 else token,
                "updated_at": now_iso(),
            }
        },
        upsert=True,
    )
    return {"ok": True, "login": login, "source": "ui", "last4": token[-4:] if len(token) >= 4 else token}


@api_router.delete("/github/token")
async def github_clear_token(current_user: dict = Depends(get_current_user)):
    await get_db().settings.delete_one(
        {"key": "github_token", "user_id": current_user["id"]}
    )
    return {"ok": True, "source": "env" if settings.github_pat else "none"}


@api_router.get("/github/repos")
async def github_repos(current_user: dict = Depends(get_current_user)):
    token, _ = await _github_token(current_user["id"])
    if not token:
        raise HTTPException(
            status_code=400,
            detail="Aucun jeton GitHub. Renseigne GITHUB_PAT dans backend/.env "
            "ou colle ton jeton dans la fenetre.",
        )
    repos = await _gh_api(
        token,
        "/user/repos",
        {"per_page": 100, "sort": "pushed", "affiliation": "owner,collaborator"},
    )
    return [
        {
            "full_name": r.get("full_name"),
            "private": r.get("private"),
            "default_branch": r.get("default_branch"),
        }
        for r in repos
        if (r.get("permissions") or {}).get("push", True)
    ]


@api_router.get("/github/branches")
async def github_branches(
    repo: str, current_user: dict = Depends(get_current_user)
):
    token, _ = await _github_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Aucun jeton GitHub.")
    if repo.count("/") != 1:
        raise HTTPException(status_code=400, detail="Format attendu : owner/repo.")
    branches = await _gh_api(token, f"/repos/{repo}/branches", {"per_page": 100})
    return [b.get("name") for b in branches if b.get("name")]


@api_router.post("/github/push")
async def github_push(
    payload: GithubPushRequest, current_user: dict = Depends(get_current_user)
):
    """Commit + push du workspace vers le depot/branche choisis."""
    token, _ = await _github_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Aucun jeton GitHub.")
    repo = payload.repo.strip().removesuffix(".git")
    branch = payload.branch.strip() or "main"
    if "/" not in repo:
        if not payload.create_if_missing:
            raise HTTPException(status_code=400, detail="Format attendu : owner/repo.")
        login = (await _gh_api(token, "/user")).get("login")
        repo = f"{login}/{repo}"
    if repo.count("/") != 1 or not re.fullmatch(r"[A-Za-z0-9._/-]+", repo):
        raise HTTPException(status_code=400, detail="Nom de depot invalide.")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise HTTPException(status_code=400, detail="Nom de branche invalide.")

    created_repo = False
    if payload.create_if_missing:
        owner, name = repo.split("/", 1)
        try:
            await _gh_api(token, f"/repos/{owner}/{name}")
        except HTTPException as e:
            if e.status_code == 401:
                raise
            await _ensure_repo(token, repo, payload.private)
            created_repo = True

    message = (payload.message or "").strip() or (
        f"Sauvegarde depuis Claude Unchained Forge — {now_iso()[:19]}"
    )
    work_dir, project_name = await resolve_project_dir(
        payload.project, payload.conversation_id, current_user["id"]
    )
    # Memorise le projet sur la conversation pour les prochains push.
    if payload.conversation_id and project_name:
        await get_db().conversations.update_one(
            {"id": payload.conversation_id, "user_id": current_user["id"]},
            {"$set": {"project": project_name}},
        )

    def _run() -> dict:
        work = Path(work_dir)
        if not work.is_dir():
            raise HTTPException(
                status_code=500, detail=f"Repertoire projet introuvable: {work_dir}"
            )
        if not (work / ".git").exists():
            init = _git(["init", "-b", branch], work_dir)
            if init.returncode != 0:
                raise HTTPException(
                    status_code=500, detail=f"git init: {init.stderr[:300]}"
                )

        add = _git(["add", "-A"], work_dir)
        if add.returncode != 0:
            raise HTTPException(status_code=500, detail=f"git add: {add.stderr[:300]}")

        # Remote 'origin' sans jeton (le push utilise une URL authentifiee jetable).
        clean_remote = f"https://github.com/{repo}.git"
        has_origin = _git(["remote", "get-url", "origin"], work_dir, timeout=15)
        if has_origin.returncode != 0:
            _git(["remote", "add", "origin", clean_remote], work_dir, timeout=15)
        elif has_origin.stdout.strip() != clean_remote:
            _git(["remote", "set-url", "origin", clean_remote], work_dir, timeout=15)

        staged = _git(["diff", "--cached", "--name-only"], work_dir)
        files = [f for f in staged.stdout.splitlines() if f.strip()]
        commit_sha = ""
        if files:
            commit = _git([
                "-c", f"user.name={settings.git_author_name}",
                "-c", f"user.email={settings.git_author_email}",
                "commit", "-m", message,
            ], work_dir)
            if commit.returncode != 0:
                raise HTTPException(
                    status_code=500, detail=f"git commit: {commit.stderr[:300]}"
                )
        rev = _git(["rev-parse", "--short", "HEAD"], work_dir)
        commit_sha = rev.stdout.strip()

        # Authentification via header HTTP temporaire : le jeton n'apparait
        # jamais dans l'URL du remote ni dans la table des processus (ps).
        basic_auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        push = _git(
            [
                "-c",
                f"http.extraHeader=AUTHORIZATION: basic {basic_auth}",
                "push",
                "origin",
                f"HEAD:refs/heads/{branch}",
            ],
            work_dir,
            timeout=600,
        )
        if push.returncode != 0:
            err = _redact(push.stderr or push.stdout, token)[:600]
            if "rejected" in err or "non-fast-forward" in err:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Push refuse : la branche distante a des commits que le "
                        "workspace n'a pas. Choisis une autre branche (ou cree-en "
                        "une nouvelle) pour ne rien ecraser. Detail : " + err
                    ),
                )
            raise HTTPException(status_code=502, detail=f"git push: {err}")

        return {
            "ok": True,
            "repo": repo,
            "repo_created": created_repo,
            "branch": branch,
            "project": project_name,
            "workspace": work_dir,
            "commit": commit_sha,
            "files_committed": len(files),
            "url": f"https://github.com/{repo}/tree/{branch}",
            "output": _redact(push.stderr or push.stdout, token)[:600],
        }

    return await asyncio.to_thread(_run)


@api_router.post("/conversations/{conv_id}/fork")
async def fork_conversation(
    conv_id: str, current_user: dict = Depends(get_current_user)
):
    """Duplique une conversation (messages compris) dans un nouveau fil."""
    database = get_db()
    conv = await database.conversations.find_one(
        {"id": conv_id, "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    new_id = str(uuid.uuid4())
    base_title = (conv.get("title") or "New Chat")[:80]
    doc = {
        "id": new_id,
        "user_id": current_user["id"],
        "title": f"{base_title} (fork)",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "forked_from": conv_id,
    }
    if conv.get("summary"):
        doc["summary"] = conv["summary"]
        doc["summarized_ids"] = []
    await database.conversations.insert_one(doc)

    msgs = (
        await database.messages.find({"conversation_id": conv_id}, {"_id": 0})
        .sort("created_at", 1)
        .to_list(2000)
    )
    if msgs:
        for m in msgs:
            m["id"] = str(uuid.uuid4())
            m["conversation_id"] = new_id
        await database.messages.insert_many(msgs)

    doc.pop("_id", None)
    return {**doc, "messages_copied": len(msgs)}


# =========================================================================
# Synthese vocale — Kokoro (local, optionnel) ou edge-tts (sans cle)
# =========================================================================
class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    rate: Optional[str] = None
    pitch: Optional[str] = None


# Voix Kokoro (modeles locaux, 100% gratuit). Prefixe "kokoro:" cote API.
KOKORO_FR_VOICES = ["ff_siwis"]

# Voix francaises neurales d'edge-tts (gratuit, sans cle ni quota).
EDGE_FR_VOICES = [
    "fr-FR-DeniseNeural",
    "fr-FR-HenriNeural",
    "fr-FR-VivienneMultilingualNeural",
    "fr-FR-RemyMultilingualNeural",
    "fr-CA-SylvieNeural",
    "fr-CA-AntoineNeural",
    "fr-BE-CharlineNeural",
    "fr-CH-ArianeNeural",
]

EDGE_FALLBACK_VOICE = "fr-FR-DeniseNeural"

_kokoro_instance = None


def _kokoro_ready() -> bool:
    """Modeles presents sur le disque et bibliotheque installee ?"""
    if settings.tts_engine == "edge":
        return False
    if not (
        Path(settings.kokoro_model_path).is_file()
        and Path(settings.kokoro_voices_path).is_file()
    ):
        return False
    try:
        import kokoro_onnx  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _normalize_voice(voice: str) -> str:
    """Nettoie l'identifiant de voix (variantes Azure HD non servies)."""
    v = (voice or "").strip()
    if v.startswith("kokoro:"):
        return v
    if ":" in v:
        base = v.split(":", 1)[0]
        return base if base.lower().endswith("neural") else f"{base}Neural"
    return v


def _kokoro_sync(text: str, voice: str) -> bytes:
    """Synthese locale Kokoro -> WAV (execute dans un thread)."""
    global _kokoro_instance
    import io

    import soundfile as sf
    from kokoro_onnx import Kokoro

    if _kokoro_instance is None:
        _kokoro_instance = Kokoro(
            settings.kokoro_model_path, settings.kokoro_voices_path
        )
    samples, rate = _kokoro_instance.create(text, voice=voice, speed=1.0, lang="fr-fr")
    buf = io.BytesIO()
    sf.write(buf, samples, rate, format="WAV")
    return buf.getvalue()


async def _synth_kokoro(text: str, voice: str) -> bytes:
    v = voice.split("kokoro:", 1)[-1] if voice.startswith("kokoro:") else settings.kokoro_voice
    if v not in KOKORO_FR_VOICES:
        v = settings.kokoro_voice
    audio = await asyncio.wait_for(
        asyncio.to_thread(_kokoro_sync, text, v), timeout=settings.kokoro_timeout
    )
    if not audio:
        raise RuntimeError("Kokoro : audio vide.")
    return audio


_EDGE_RATE_RE = re.compile(r"^[+-]\d+%$")
_EDGE_PITCH_RE = re.compile(r"^[+-]\d+Hz$")


def _sanitize_edge_rate(value) -> str:
    """Debit edge-tts : pourcentage signe obligatoire (ex: "+10%", "-5%"). Repli "+0%"."""
    if isinstance(value, str):
        v = value.strip()
        if re.fullmatch(r"\d+%", v):
            v = "+" + v
        if _EDGE_RATE_RE.match(v):
            return v
    return "+0%"


def _sanitize_edge_pitch(value) -> str:
    """Hauteur edge-tts : Hertz signe obligatoire (ex: "+5Hz", "-10Hz"). Repli "+0Hz".
    Un ancien format en pourcentage ("+0%") est converti en Hertz (valeur conservee)."""
    if isinstance(value, str):
        v = value.strip()
        m = re.fullmatch(r"([+-]?)(\d+)(%|Hz)?", v)
        if m:
            return f"{m.group(1) or '+'}{m.group(2)}Hz"
    return "+0Hz"


async def _synth_edge(text: str, voice: str, rate: str, pitch: str) -> bytes:
    import edge_tts

    rate = _sanitize_edge_rate(rate)
    pitch = _sanitize_edge_pitch(pitch)

    async def _stream(v: str) -> bytes:
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, v, rate=rate, pitch=pitch).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    target = voice if voice in EDGE_FR_VOICES or "-" in voice else settings.tts_voice
    try:
        audio = await _stream(target)
    except Exception:  # voix absente du catalogue edge-tts
        if target == EDGE_FALLBACK_VOICE:
            raise
        logger.warning("Voix %s indisponible sur edge-tts, repli sur %s", target, EDGE_FALLBACK_VOICE)
        audio = await _stream(EDGE_FALLBACK_VOICE)
    if not audio:
        raise HTTPException(status_code=502, detail="edge-tts : audio vide.")
    return audio


async def _edge_voices() -> list[dict]:
    import edge_tts

    return [
        {
            "short_name": v.get("ShortName"),
            "gender": v.get("Gender"),
            "locale": v.get("Locale"),
            "engine": "edge-tts",
        }
        for v in await edge_tts.list_voices()
        if v.get("ShortName")
    ]


@api_router.get("/tts/voices")
async def tts_voices(
    locale: str = "fr", current_user: dict = Depends(get_current_user)
):
    """Catalogue de voix gratuites : Kokoro (local) + edge-tts."""
    kokoro_on = _kokoro_ready()
    voices: list[dict] = [
        {
            "short_name": f"kokoro:{v}",
            "locale": "fr-FR",
            "gender": "Female",
            "engine": "kokoro",
        }
        for v in (KOKORO_FR_VOICES if kokoro_on else [])
    ]
    error = None
    try:
        edge = await _edge_voices()
    except Exception as e:  # noqa: BLE001
        logger.warning("Catalogue edge-tts indisponible: %s", e)
        edge = [
            {"short_name": v, "locale": v[:5], "gender": None, "engine": "edge-tts"}
            for v in EDGE_FR_VOICES
        ]
        error = str(e)[:200]

    if locale:
        low = locale.lower()
        edge = [v for v in edge if (v.get("locale") or "").lower().startswith(low)]
    edge.sort(key=lambda v: v["short_name"])

    out = {
        "provider": "kokoro" if kokoro_on else "edge-tts",
        "engines": (["kokoro"] if kokoro_on else []) + ["edge-tts"],
        "default": settings.tts_voice,
        "voices": voices + edge,
    }
    if error:
        out["error"] = error
    return out


@api_router.post("/tts")
async def tts_speak(
    payload: TTSRequest, current_user: dict = Depends(get_current_user)
):
    """Genere l'audio localement (Kokoro) ou via edge-tts. Aucun service payant."""
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Texte vide.")
    text = text[: settings.tts_max_chars]
    voice = _normalize_voice(payload.voice or settings.tts_voice)
    if len(voice) > 160 or not re.fullmatch(r"[A-Za-z0-9:\-_()]+", voice):
        raise HTTPException(status_code=400, detail="Nom de voix invalide.")
    rate = payload.rate or settings.tts_rate
    pitch = payload.pitch or settings.tts_pitch

    wants_kokoro = voice.startswith("kokoro:") or settings.tts_engine == "kokoro"
    engine = None
    audio = b""

    if (wants_kokoro or settings.tts_engine == "auto") and _kokoro_ready():
        try:
            audio = await _synth_kokoro(text, voice)
            engine = "kokoro"
        except asyncio.TimeoutError:
            logger.warning("Kokoro : delai depasse, bascule sur edge-tts")
        except Exception as e:  # noqa: BLE001
            logger.warning("Kokoro indisponible (%s), bascule sur edge-tts", str(e)[:200])

    if not audio:
        edge_voice = settings.tts_voice if voice.startswith("kokoro:") else voice
        try:
            audio = await _synth_edge(text, edge_voice, rate, pitch)
            engine = "edge-tts"
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Echec edge-tts: %s", e)
            raise HTTPException(
                status_code=502,
                detail=f"Synthese vocale indisponible : {str(e)[:200]}",
            )

    media = "audio/wav" if engine == "kokoro" else "audio/mpeg"
    ext = "wav" if engine == "kokoro" else "mp3"
    return Response(
        content=audio,
        media_type=media,
        headers={
            "Content-Disposition": f'inline; filename="speech.{ext}"',
            "X-TTS-Provider": engine or "none",
            "X-TTS-Voice": voice,
        },
    )


@api_router.get("/")
async def root():
    return {
        "message": "Claude Unchained Forge API",
        "engine": "claude" if settings.claude_token else "claude (jeton absent)",
        "model": settings.claude_model,
        "db": "up" if db is not None else "down",
        "registration_open": settings.allow_registration,
    }


@api_router.get("/health")
async def health():
    checks = {"db": False}
    if db is not None:
        try:
            await mongo_client.admin.command("ping")
            checks["db"] = True
        except Exception:
            checks["db"] = False
    status = "ok" if all(checks.values()) else "degraded"
    return {"status": status, "checks": checks}


app.include_router(api_router)


# =========================================================================
# CORS
# =========================================================================
# allow_credentials=True est incompatible avec allow_origins=["*"] : les
# navigateurs rejettent toute reponse credentialed portant une origine joker.
# On liste donc les origines explicitement. Si FRONTEND_URL est absent, on
# n'autorise rien plutot que de servir une configuration silencieusement cassee.
from starlette.middleware.gzip import GZipMiddleware as _StarletteGZip


class _SafeGZip:
    """GZip pour les reponses JSON/texte, JAMAIS pour les flux SSE.

    Le GZipMiddleware de Starlette met les chunks en tampon : appliqué au
    streaming du chat, il retarderait les evenements. On le contourne donc
    pour les routes de flux et on utilise un niveau 5 (le 9 par defaut coute
    cher en CPU pour un gain negligeable).
    """

    _SKIP_SUFFIXES = ("/chat/stream", "/preview/logs", "/tts")

    def __init__(self, app):
        self.app = app
        self.gzip = _StarletteGZip(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").endswith(self._SKIP_SUFFIXES):
            await self.app(scope, receive, send)
            return
        await self.gzip(scope, receive, send)


app.add_middleware(_SafeGZip)

if settings.frontend_urls:
    # Origines explicites (FRONTEND_URL) + regex restreinte aux hôtes de
    # développement local (localhost/127.0.0.1, port quelconque). Aucun
    # wildcard global "*" n'est utilisé : allow_credentials=True impose des
    # origines précises, jamais un joker universel.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_urls,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
else:
    logger.error(
        "CORS non configure (FRONTEND_URL vide) : les appels navigateur "
        "echoueront. Renseignez FRONTEND_URL dans .env."
    )