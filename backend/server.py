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
import subprocess
import uuid
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, AsyncIterator

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
    UploadFile,
    File,
    Form,
)
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse, FileResponse
from motor.motor_asyncio import AsyncIOMotorClient
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
        ):
            up = pid.upper()
            self.free_providers[pid] = {
                "label": label,
                "base_url": _env(f"{up}_BASE_URL", default_base).rstrip("/"),
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
        await _seed_admin()

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
        "key": "access_token",
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
    kwargs = {"key": "access_token", "path": "/"}
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    response.delete_cookie(**kwargs)


async def get_current_user(request: Request) -> dict:
    database = get_db()

    token = request.cookies.get("access_token")
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

# Les captures sont ecrites sur disque et servies par /api/screenshots/{nom}.
SCREENSHOT_DIR = ROOT_DIR / "static" / "screenshots"

TOOLS = [
    {
        "name": "bash",
        "description": (
            "Exécute une commande shell sur le serveur et renvoie le code de "
            "sortie, stdout et stderr. Timeout de sécurité de 30 secondes. "
            "Utilise cet outil pour lister des fichiers, inspecter le système, "
            "lancer des scripts, etc."
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


def _tool_bash(command: str) -> str:
    if not command:
        return "Erreur: commande vide."
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (result.stdout or "")[:8000]
        err = (result.stderr or "")[:4000]
        return (
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
    Recherche web. Priorite a DuckDuckGo (aucune cle requise) ; bascule sur
    Google Custom Search si GOOGLE_CSE_KEY et GOOGLE_CSE_CX sont renseignes.
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

    try:
        from ddgs import DDGS

        with DDGS(timeout=20) as ddgs:
            results = list(ddgs.text(query, max_results=n))
        if results:
            return _format_search_results(results, query, "DuckDuckGo")
        reason = "aucun resultat"
    except Exception as e:  # noqa: BLE001
        reason = str(e)[:200]
        logger.warning("DuckDuckGo indisponible (%s), repli Wikipedia", reason)

    # Dernier recours sans cle : l'API de recherche Wikipedia. Les moteurs
    # generalistes bloquent souvent les IP de datacenter (429/403) ; Wikipedia
    # reste accessible et suffit pour une question factuelle.
    wiki = _search_wikipedia(query, n)
    if wiki:
        return wiki
    return (
        f"Recherche web indisponible pour « {query} » ({reason}). "
        "Le moteur a refuse la requete depuis cette machine. Configure "
        "GOOGLE_CSE_KEY et GOOGLE_CSE_CX dans backend/.env pour une source fiable."
    )


def _search_wikipedia(query: str, n: int) -> str:
    """Recherche encyclopedique sans cle, utilisee en dernier recours."""
    for lang in ("fr", "en"):
        try:
            resp = httpx.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query", "list": "search", "srsearch": query,
                    "srlimit": n, "format": "json",
                },
                # Wikipedia exige un User-Agent identifiant l'app avec un
                # moyen de contact, sinon 403.
                headers={
                    "User-Agent": (
                        "ClaudeUnchainedForge/1.0 "
                        "(https://github.com/; auto-heberge)"
                    )
                },
                timeout=20.0,
            )
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search") or []
        except Exception:  # noqa: BLE001
            continue
        if not hits:
            continue
        return _format_search_results([
            {
                "title": h.get("title", ""),
                "href": (
                    f"https://{lang}.wikipedia.org/wiki/"
                    + (h.get("title") or "").replace(" ", "_")
                ),
                "body": _TAG_RE.sub("", unescape(h.get("snippet") or "")),
            }
            for h in hits
        ], query, f"Wikipedia ({lang})")
    return ""


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


def _tool_fetch_url(url: str) -> str:
    """Telecharge une page et en extrait le texte, sans dependance lourde."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Erreur: l'URL doit commencer par http:// ou https://."
    try:
        resp = httpx.get(
            url,
            timeout=25.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ClaudeUnchainedForge/1.0)"},
        )
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
    re.compile(r"\b(gh[pousr]_)[A-Za-z0-9]{10,}"),
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
    """Génération via Ollama local (ex: qwen2.5-coder:3b)."""
    messages = []
    for h in history:
        r = "user" if h.get("role") == "user" else "assistant"
        c = (h.get("content") or "").strip()
        if c:
            messages.append({"role": r, "content": c})
    messages.append({"role": "user", "content": text or "(vide)"})

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
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.ollama_timeout, connect=5.0)
    ) as http:
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
        answer = (data.get("message", {}).get("content", "") or "").strip()
        return answer or "(reponse vide)", []


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

FREE_PROVIDER_IDS = ("groq", "cerebras", "sambanova", "nvidia", "openrouter")

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
    headers = {
        "Authorization": f"Bearer {conf['api_key']}",
        "Content-Type": "application/json",
    }
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
    if settings.claude_system_prompt:
        messages = [
            {"role": "system", "content": settings.claude_system_prompt}
        ] + messages
    return messages


async def _generate_openai_compat(
    pid: str,
    history: list[dict],
    text: str,
    images: Optional[list[dict]] = None,
    model_override: Optional[str] = None,
) -> tuple[str, list[dict], str]:
    """Adaptateur unifie pour tout endpoint compatible OpenAI (non-stream)."""
    if not _provider_available(pid):
        raise HTTPException(
            status_code=503,
            detail=f"{pid.upper()}_API_KEY absent dans backend/.env.",
        )
    conf = settings.free_providers[pid]
    model_name = await _resolve_free_model(pid, model_override)
    payload = {
        "model": model_name,
        "messages": _openai_messages(history, text, images),
        "max_tokens": settings.claude_max_tokens,
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(conf["timeout"], connect=10.0)
    ) as http:
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
    answer = ""
    if choices:
        answer = ((choices[0].get("message") or {}).get("content") or "").strip()
    return (answer or "(reponse vide)", [], model_name)


async def _stream_openai_compat(
    pid: str,
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    model_override: Optional[str],
    state: dict,
) -> AsyncIterator[dict]:
    """Adaptateur unifie compatible OpenAI, en SSE (`stream: true`)."""
    if not _provider_available(pid):
        raise HTTPException(
            status_code=503,
            detail=f"{pid.upper()}_API_KEY absent dans backend/.env.",
        )
    conf = settings.free_providers[pid]
    model_name = await _resolve_free_model(pid, model_override)
    state["model"] = model_name
    payload = {
        "model": model_name,
        "messages": _openai_messages(history, text, images),
        "max_tokens": settings.claude_max_tokens,
        "stream": True,
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(conf["timeout"], connect=10.0)
    ) as http:
        async with http.stream(
            "POST",
            f"{conf['base_url']}/chat/completions",
            json=payload,
            headers=_free_provider_headers(pid),
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise HTTPException(
                    status_code=502,
                    detail=f"Erreur {PROVIDER_LABELS[pid]}: {body[:400]}",
                )
            async for ev in _sse_events(resp):
                if ev.get("error"):
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Erreur {PROVIDER_LABELS[pid]}: "
                            f"{str(ev['error'])[:400]}"
                        ),
                    )
                for ch in ev.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content") or ""
                    if piece:
                        yield {"delta": piece}


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
    """Chaine de providers a essayer, Ollama local toujours en dernier recours."""
    priority = [p for p in settings.provider_priority if p in PROVIDER_IDS]
    for p in PROVIDER_IDS:
        if p not in priority:
            priority.append(p)

    if requested == "auto":
        chain = list(priority)
    else:
        chain = [requested] + [p for p in priority if p != requested]

    if not settings.enable_fallback:
        chain = chain[:1]

    # Le moteur local est lent : jamais avant un cloud, sauf s'il est demande.
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
        answer, steps = await _generate_claude(history, text, images)
        return answer, steps, settings.claude_model
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
    attempts: list[dict] = []

    for pid in chain:
        if not _provider_available(pid):
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


async def _generate_gemini(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
) -> tuple[str, list[dict], str]:
    """Generation via Google Gemini (cle API GEMINI_API_KEY, SDK google-genai)."""
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
    contents = _gemini_contents(history, text, images)
    config = gtypes.GenerateContentConfig(
        system_instruction=settings.claude_system_prompt,
        max_output_tokens=settings.claude_max_tokens,
    )

    # Modèle principal + fallback (utile quand un modèle est saturé).
    model_candidates = [settings.gemini_model]
    if settings.gemini_fallback_model and settings.gemini_fallback_model != settings.gemini_model:
        model_candidates.append(settings.gemini_fallback_model)

    last_err: Optional[Exception] = None
    for model_name in model_candidates:
        def _call(m=model_name):
            return client.models.generate_content(
                model=m, contents=contents, config=config
            )
        # Retry avec backoff sur erreurs transitoires (503 / surcharge / 429).
        for attempt in range(4):
            try:
                resp = await asyncio.to_thread(_call)
                answer = (getattr(resp, "text", None) or "").strip()
                return (answer or "(reponse vide)", [], model_name)
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e)
                transient = any(
                    k in msg
                    for k in ("503", "UNAVAILABLE", "overloaded", "high demand",
                              "RESOURCE_EXHAUSTED", "429", "500", "INTERNAL")
                )
                if transient and attempt < 3:
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
                    continue
                break  # non transitoire, ou retries épuisés -> modèle suivant

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
    Generation via Ollama Cloud (https://ollama.com/api), auth Bearer.
    Essaie le modele principal puis le modele de secours du meme provider.
    """
    if not settings.ollama_cloud_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "OLLAMA_CLOUD_API_KEY absent. Ajoute ta cle Ollama Cloud dans "
                "backend/.env."
            ),
        )

    messages = _plain_messages(history, text)
    if images:
        messages[-1]["images"] = [i["data"] for i in images]
    if settings.claude_system_prompt:
        messages = [
            {"role": "system", "content": settings.claude_system_prompt}
        ] + messages

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
            payload = {"model": model_name, "messages": messages, "stream": False}
            try:
                logger.info("Appel Ollama Cloud (modele %s)...", model_name)
                resp = await http.post(
                    f"{settings.ollama_cloud_url}/chat",
                    json=payload,
                    headers=headers,
                )
            except httpx.HTTPError as e:
                last_detail = f"reseau: {e}"
                continue

            if resp.status_code >= 400:
                last_detail = _http_error_detail(resp)
                logger.warning(
                    "Ollama Cloud %s -> HTTP %s: %s",
                    model_name, resp.status_code, last_detail[:200],
                )
                continue

            data = resp.json()
            # L'API renvoie parfois une erreur applicative avec un HTTP 200.
            if isinstance(data, dict) and data.get("error"):
                last_detail = str(data["error"])[:500]
                logger.warning(
                    "Ollama Cloud %s -> erreur applicative: %s",
                    model_name, last_detail[:200],
                )
                continue

            answer = (
                (data.get("message") or {}).get("content", "") or ""
            ).strip()
            return (answer or "(reponse vide)", [], model_name)

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
) -> tuple[str, dict]:
    """Construit (chemin, payload) pour le transport demande."""
    messages = _opencode_convert_image(messages, transport)

    if transport == "messages":
        # Format Anthropic : le system est un champ a part.
        body = {
            "model": model,
            "max_tokens": settings.claude_max_tokens,
            "messages": messages,
        }
        if system_prompt:
            body["system"] = system_prompt
        return "/messages", body

    if transport == "responses":
        # Format OpenAI Responses : `input` + `instructions`.
        body = {
            "model": model,
            "input": messages,
            "max_output_tokens": settings.claude_max_tokens,
        }
        if system_prompt:
            body["instructions"] = system_prompt
        return "/responses", body

    body = {"model": model, "max_tokens": settings.claude_max_tokens}
    if system_prompt:
        body["messages"] = [
            {"role": "system", "content": system_prompt}
        ] + messages
    else:
        body["messages"] = messages
    return "/chat/completions", body


def _opencode_extract(transport: str, data: dict) -> str:
    """Extrait le texte de la reponse selon le transport."""
    if transport == "messages":
        parts = [
            b.get("text", "")
            for b in (data.get("content") or [])
            if b.get("type") == "text"
        ]
        return "".join(parts).strip()

    if transport == "responses":
        out = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for block in item.get("content") or []:
                if block.get("type") in ("output_text", "text"):
                    out.append(block.get("text", ""))
        return "".join(out).strip()

    choices = data.get("choices") or []
    if not choices:
        return ""
    return ((choices[0].get("message") or {}).get("content") or "").strip()


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
    entre eux si l'endpoint ne correspond pas.

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
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.opencode_timeout, connect=10.0)
    ) as http:
        for model_name in candidates:
            primary = _opencode_transport(model_name)
            transports = [primary] + [
                t for t in ("chat", "messages", "responses") if t != primary
            ]
            for transport in transports:
                path, payload = _opencode_payload(
                    transport, model_name, messages, settings.claude_system_prompt
                )
                try:
                    logger.info(
                        "Appel OpenCode (modele %s, transport %s)...",
                        model_name, transport,
                    )
                    resp = await http.post(
                        f"{settings.opencode_base_url}{path}",
                        json=payload,
                        headers=headers,
                    )
                except httpx.HTTPError as e:
                    last_detail = f"reseau: {e}"
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
                    if _opencode_wrong_transport(resp.status_code, last_detail):
                        continue  # mauvais endpoint probable : autre transport
                    break  # vrai refus (region, quota, auth...) : modele suivant

                answer = _opencode_extract(transport, data)
                return (answer or "(reponse vide)", [], model_name)

    raise HTTPException(
        status_code=502,
        detail=f"Erreur OpenCode: {last_detail or 'echec inconnu'}",
    )


async def _call_anthropic(messages: list[dict], use_tools: bool = True) -> dict:
    """Un appel à l'API Messages d'Anthropic (avec outils si activés)."""
    payload = {
        "model": settings.claude_model,
        "max_tokens": settings.claude_max_tokens,
        "system": [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": settings.claude_system_prompt},
        ],
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
    state["model"] = settings.claude_model
    messages = _build_messages(history, text, images)
    headers = {
        "authorization": f"Bearer {settings.claude_token}",
        **ANTHROPIC_STREAM_HEADERS,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as http:
        for _ in range(MAX_TOOL_ITERS):
            payload = {
                "model": settings.claude_model,
                "max_tokens": settings.claude_max_tokens,
                "system": [
                    {"type": "text", "text": CLAUDE_CODE_IDENTITY},
                    {"type": "text", "text": settings.claude_system_prompt},
                ],
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
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY absent.")
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(api_key=settings.gemini_api_key)
    contents = _gemini_contents(history, text, images)
    config = gtypes.GenerateContentConfig(
        system_instruction=settings.claude_system_prompt,
        max_output_tokens=settings.claude_max_tokens,
    )
    state["model"] = settings.gemini_model
    stream = await client.aio.models.generate_content_stream(
        model=settings.gemini_model, contents=contents, config=config
    )
    async for chunk in stream:
        piece = getattr(chunk, "text", None)
        if piece:
            yield {"delta": piece}


async def _stream_ndjson_ollama(
    url: str,
    headers: dict,
    payload: dict,
    timeout: float,
) -> AsyncIterator[dict]:
    """Flux Ollama (local ou cloud) : une ligne JSON par fragment."""
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
                piece = (data.get("message") or {}).get("content") or ""
                if piece:
                    yield {"delta": piece}


async def _stream_ollama(
    history: list[dict], text: str, state: dict
) -> AsyncIterator[dict]:
    state["model"] = settings.ollama_model
    messages = _plain_messages(history, text)
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
    async for item in _stream_ndjson_ollama(
        f"{settings.ollama_url}/api/chat", {}, payload, settings.ollama_timeout
    ):
        yield item


async def _stream_ollama_cloud(
    history: list[dict],
    text: str,
    images: Optional[list[dict]],
    model_override: Optional[str],
    state: dict,
) -> AsyncIterator[dict]:
    if not settings.ollama_cloud_api_key:
        raise HTTPException(status_code=503, detail="OLLAMA_CLOUD_API_KEY absent.")

    candidates = [model_override or settings.ollama_cloud_model]
    if (
        settings.ollama_cloud_fallback_model
        and settings.ollama_cloud_fallback_model not in candidates
    ):
        candidates.append(settings.ollama_cloud_fallback_model)

    messages = _plain_messages(history, text)
    if images:
        messages[-1]["images"] = [i["data"] for i in images]
    if settings.claude_system_prompt:
        messages = [
            {"role": "system", "content": settings.claude_system_prompt}
        ] + messages

    last_err: Optional[Exception] = None
    for model_name in candidates:
        state["model"] = model_name
        payload = {"model": model_name, "messages": messages, "stream": True}
        produced = False
        try:
            async for item in _stream_ndjson_ollama(
                f"{settings.ollama_cloud_url}/chat",
                {"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
                payload,
                settings.ollama_cloud_timeout,
            ):
                produced = True
                yield item
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
    OpenCode en streaming. Les transports /chat/completions (SSE OpenAI) et
    /messages (SSE Anthropic) sont streames ; /responses retombe en non-stream.
    """
    if not settings.opencode_api_key:
        raise HTTPException(status_code=503, detail="OPENCODE_API_KEY absent.")
    model_name = model_override or settings.opencode_model
    state["model"] = model_name
    transport = _opencode_transport(model_name)

    if transport == "responses":
        answer, _, used = await _generate_opencode(
            history, text, images, session_id, model_override
        )
        state["model"] = used
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
    path, payload = _opencode_payload(
        transport, model_name, messages, settings.claude_system_prompt
    )
    payload["stream"] = True
    headers = {
        "Authorization": f"Bearer {settings.opencode_api_key}",
        "x-api-key": settings.opencode_api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "User-Agent": settings.opencode_user_agent,
        "x-opencode-session": f"ses_forge_{session_id or uuid.uuid4().hex}",
    }
    url = f"{settings.opencode_base_url}{path}"

    if transport == "messages":
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.opencode_timeout, connect=10.0)
        ) as http:
            async for item in _stream_anthropic_turn(http, url, headers, payload):
                if "delta" in item:
                    yield item
        return

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.opencode_timeout, connect=10.0)
    ) as http:
        async with http.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise HTTPException(
                    status_code=502, detail=f"Erreur OpenCode: {body[:400]}"
                )
            async for ev in _sse_events(resp):
                if ev.get("error"):
                    raise HTTPException(
                        status_code=502,
                        detail=f"Erreur OpenCode: {str(ev['error'])[:400]}",
                    )
                for ch in ev.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content") or ""
                    if piece:
                        yield {"delta": piece}


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
    attempts: list[dict] = []

    for pid in chain:
        if not _provider_available(pid):
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
            if produced:
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
async def login(payload: LoginRequest, response: Response):
    database = get_db()
    email = payload.email.lower().strip()

    user = await database.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

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
            {"user_id": current_user["id"]}, {"_id": 0}
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
    doc.pop("_id", None)
    return doc


@api_router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: str, current_user: dict = Depends(get_current_user)):
    database = get_db()
    conv = await database.conversations.find_one(
        {"id": conv_id, "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return (
        await database.messages.find(
            {"conversation_id": conv_id},
            {"_id": 0, "image_b64": 0, "file_text": 0, "images_b64": 0, "prompt_override": 0},
        )
        .sort("created_at", 1)
        .to_list(2000)
    )


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
    current_user: dict = Depends(get_current_user),
):
    database = get_db()

    conv = await database.conversations.find_one(
        {"id": conversation_id, "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

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
    history = (
        await database.messages.find(
            {"conversation_id": conversation_id, "id": {"$ne": user_msg_id}},
            {"_id": 0, "image_b64": 0, "file_text": 0, "images_b64": 0, "prompt_override": 0},
        )
        .sort("created_at", 1)
        .to_list(2000)
    )
    history = await prepare_history(database, conversation_id, history)

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
        raise HTTPException(status_code=500, detail=f"AI error: {e}")

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

    history = (
        await database.messages.find(
            {"conversation_id": conversation_id, "id": {"$ne": user_msg_id}},
            {"_id": 0, "image_b64": 0, "file_text": 0, "images_b64": 0, "prompt_override": 0},
        )
        .sort("created_at", 1)
        .to_list(2000)
    )
    history = await prepare_history(database, conversation_id, history)

    public_user_msg = {
        k: v for k, v in user_msg_doc.items()
        if k not in ("_id", "image_b64", "file_text", "images_b64", "prompt_override")
    }

    async def event_source():
        yield _sse("user_message", public_user_msg)
        chunks: list[str] = []
        tool_steps: list[dict] = []
        meta: Optional[dict] = None
        stopped = True
        try:
            async for ev in stream_ai_response(
                history=history,
                text=prompt_text,
                images=images,
                provider=provider,
                session_id=conversation_id,
                model=(model or "").strip() or None,
            ):
                if ev["type"] == "delta":
                    chunks.append(ev["text"])
                    yield _sse("delta", {"text": ev["text"]})
                elif ev["type"] == "start":
                    meta = {
                        "provider": ev["provider"],
                        "model": ev["model"],
                        "requested_provider": provider,
                        "fallback_used": False,
                        "attempts": [],
                    }
                    yield _sse("start", ev)
                elif ev["type"] == "tool":
                    tool_steps.append(ev["step"])
                    yield _sse("tool", ev["step"])
                elif ev["type"] == "error":
                    stopped = False
                    yield _sse("error", {"detail": ev["detail"]})
                    return
                elif ev["type"] == "done":
                    meta = ev
                    stopped = False
            if meta is None:
                return
            ai_doc = await _persist_assistant(
                database, conversation_id, "".join(chunks), tool_steps, meta, False
            )
            await _autotitle(database, conv, conversation_id, text, attachments)
            yield _sse("done", ai_doc)
        finally:
            # Client parti en cours de route : on garde le texte deja produit.
            # Les `await` sont interdits ici (le generateur est en cours de
            # fermeture) : on delegue l'ecriture a une tache detachee.
            if stopped and chunks:
                _spawn(
                    _persist_stopped(
                        database, conversation_id, "".join(chunks), tool_steps,
                        meta or {
                            "provider": provider,
                            "model": model or provider,
                            "requested_provider": provider,
                            "fallback_used": False,
                            "attempts": [],
                        },
                    )
                )

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

    msgs = (
        await database.messages.find({"conversation_id": payload.conversation_id})
        .sort("created_at", 1)
        .to_list(2000)
    )
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
        raise HTTPException(status_code=500, detail=f"AI error: {e}")

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
    for pid in ("opencode", "ollama_cloud", *FREE_PROVIDER_IDS):
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


async def _github_token(user_id: str) -> tuple[str, str]:
    """Jeton a utiliser + origine ('ui' | 'env' | 'none')."""
    doc = await get_db().settings.find_one(
        {"key": "github_token", "user_id": user_id}
    )
    token = (doc or {}).get("token") or ""
    if token:
        return token, "ui"
    if settings.github_pat:
        return settings.github_pat, "env"
    return "", "none"


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


async def _run_preview_map_refresh() -> None:
    """Regenere la map Nginx des previews. Ne leve jamais : log uniquement."""
    global _preview_map_pending
    cmd = settings.preview_map_refresh_cmd
    if not cmd:
        return
    if _preview_map_lock.locked():
        # Une execution est deja en cours : on demande juste un re-run apres.
        _preview_map_pending = True
        return
    async with _preview_map_lock:
        while True:
            _preview_map_pending = False
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
                    logger.warning(
                        "Preview map: timeout apres %ss sur '%s'",
                        settings.preview_map_refresh_timeout,
                        cmd,
                    )
                    out = b""
                else:
                    if proc.returncode == 0:
                        logger.info("Preview map Nginx regeneree avec succes.")
                    else:
                        logger.warning(
                            "Preview map: '%s' a echoue (code %s) : %s",
                            cmd,
                            proc.returncode,
                            (out or b"").decode("utf-8", "replace")[-500:],
                        )
            except FileNotFoundError:
                logger.warning("Preview map: commande introuvable ('%s').", cmd)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Preview map: erreur inattendue : %s", exc)
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
    """Projets du workspace + URL de preview et nb de conversations."""
    database = get_db()
    meta = await _projects_meta(current_user["id"])
    projects = list_workspace_projects()
    for p in projects:
        m = meta.get(p["name"]) or {}
        if not m.get("preview_url") or not m.get("preview_port"):
            m = await _assign_project_meta(current_user["id"], p["name"])
        p["preview_url"] = m.get("preview_url") or ""
        p["preview_port"] = m.get("preview_port")
        p["conversations"] = await database.conversations.count_documents(
            {"user_id": current_user["id"], "project": p["name"]}
        )
    return {"root": settings.workspace_root, "projects": projects}


@api_router.post("/workspace/projects")
async def create_project(
    payload: CreateProjectRequest, current_user: dict = Depends(get_current_user)
):
    """Cree le sous-dossier du projet dans WORKSPACE_ROOT."""
    name = _valid_project_name(payload.name)
    d = _ensure_project_dir(name)
    meta = await _assign_project_meta(
        current_user["id"], name, (payload.preview_url or "").strip() or None
    )
    return {
        "name": name,
        "path": str(d),
        "preview_url": meta.get("preview_url", ""),
        "preview_port": meta.get("preview_port"),
        "is_git_repo": (d / ".git").exists(),
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


@api_router.post("/workspace/preview-map/refresh")
async def refresh_preview_map(current_user: dict = Depends(get_current_user)):
    """Force la regeneration de la map Nginx des sous-domaines de preview."""
    if not settings.preview_map_refresh_cmd:
        raise HTTPException(
            status_code=400,
            detail="PREVIEW_MAP_REFRESH_CMD n'est pas configure.",
        )
    asyncio.create_task(_run_preview_map_refresh())
    return {"scheduled": True, "command": settings.preview_map_refresh_cmd}


@api_router.post("/conversations/{conv_id}/project")
async def link_conversation_project(
    conv_id: str,
    payload: CreateProjectRequest,
    current_user: dict = Depends(get_current_user),
):
    """Rattache une conversation a un projet (cree le dossier si besoin)."""
    name = _valid_project_name(payload.name)
    _ensure_project_dir(name)
    meta = await _assign_project_meta(current_user["id"], name)
    res = await get_db().conversations.update_one(
        {"id": conv_id, "user_id": current_user["id"]},
        {"$set": {"project": name, "updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
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
    await get_db().settings.update_one(
        {"key": "github_token", "user_id": current_user["id"]},
        {"$set": {"token": token, "updated_at": now_iso()}},
        upsert=True,
    )
    return {"ok": True, "login": login, "source": "ui"}


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
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
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

        push = _git(["push", remote, f"HEAD:refs/heads/{branch}"], work_dir, timeout=600)
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


async def _synth_edge(text: str, voice: str, rate: str, pitch: str) -> bytes:
    import edge_tts

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
if settings.frontend_urls:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_urls,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
else:
    logger.error(
        "CORS non configure (FRONTEND_URL vide) : les appels navigateur "
        "echoueront. Renseignez FRONTEND_URL dans .env."
    )
