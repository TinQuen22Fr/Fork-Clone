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
import time
import asyncio
import base64
import subprocess
import uuid
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

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
        self.history_turns: int = int(_env("HISTORY_TURNS", "20"))

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
            "les images envoyees par l'utilisateur.",
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
                "PROVIDER_PRIORITY", "claude,gemini,ollama_cloud,opencode,ollama"
            ).split(",")
            if p.strip()
        ]
        self.enable_fallback: bool = _env_bool("ENABLE_FALLBACK", True)

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


def _run_tool(name: str, tool_input: dict) -> str:
    if name == "bash":
        return _tool_bash(tool_input.get("command", ""))
    if name == "read_file":
        return _tool_read_file(tool_input.get("path", ""))
    return f"Erreur: outil inconnu '{name}'."


def _build_messages(history: list[dict], text: str,
                    image_b64: Optional[str], image_mime: Optional[str]) -> list[dict]:
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
    if image_b64:
        current.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_mime or "image/png",
                "data": image_b64,
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
    image_b64: Optional[str],
    image_mime: Optional[str],
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

    messages = _build_messages(history, text, image_b64, image_mime)
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
PROVIDER_IDS = ("claude", "gemini", "ollama_cloud", "opencode", "ollama")

PROVIDER_LABELS = {
    "claude": "Claude",
    "gemini": "Gemini",
    "ollama_cloud": "Ollama Cloud",
    "opencode": "OpenCode Zen",
    "ollama": "Ollama (Local)",
}


def _provider_model(pid: str) -> str:
    return {
        "claude": settings.claude_model,
        "gemini": settings.gemini_model,
        "ollama_cloud": settings.ollama_cloud_model,
        "opencode": settings.opencode_model,
        "ollama": settings.ollama_model,
    }.get(pid, pid)


def _provider_available(pid: str) -> bool:
    """Detection dynamique : un provider sans cle configuree est ignore."""
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
    if any(k in low for k in ("not found", "introuvable", "model", "404",
                              "unsupported")):
        return "model"
    if any(k in low for k in ("503", "unavailable", "overloaded", "high demand",
                              "500", "502", "internal")):
        return "unavailable"
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
    image_b64: Optional[str],
    image_mime: Optional[str],
    session_id: Optional[str] = None,
    model_override: Optional[str] = None,
) -> tuple[str, list[dict], str]:
    """Retourne (texte, tool_steps, modele_reellement_utilise)."""
    if pid == "claude":
        answer, steps = await _generate_claude(history, text, image_b64, image_mime)
        return answer, steps, settings.claude_model
    if pid == "gemini":
        return await _generate_gemini(history, text, image_b64, image_mime)
    if pid == "ollama_cloud":
        return await _generate_ollama_cloud(
            history, text, image_b64, image_mime, model_override
        )
    if pid == "opencode":
        return await _generate_opencode(
            history, text, image_b64, image_mime, session_id, model_override
        )
    if pid == "ollama":
        answer, steps = await _generate_ollama(history, text)
        return answer, steps, settings.ollama_model
    raise HTTPException(status_code=400, detail=f"provider inconnu: {pid}")


async def generate_ai_response(
    history: list[dict],
    text: str,
    image_b64: Optional[str] = None,
    image_mime: Optional[str] = None,
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
                pid, history, text, image_b64, image_mime, session_id,
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


def _gemini_contents(history, text, image_b64, image_mime):
    from google.genai import types
    contents = []
    for h in history:
        role = "user" if h.get("role") == "user" else "model"
        c = (h.get("content") or "").strip()
        if c:
            contents.append(types.Content(role=role, parts=[types.Part(text=c)]))
    parts = []
    if image_b64:
        parts.append(types.Part(
            inline_data=types.Blob(
                mime_type=image_mime or "image/png",
                data=base64.b64decode(image_b64),
            )
        ))
    parts.append(types.Part(text=text or "(image)"))
    contents.append(types.Content(role="user", parts=parts))
    return contents


async def _generate_gemini(
    history: list[dict],
    text: str,
    image_b64: Optional[str],
    image_mime: Optional[str],
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
    contents = _gemini_contents(history, text, image_b64, image_mime)
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
    image_b64: Optional[str] = None,
    image_mime: Optional[str] = None,
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
    if image_b64:
        messages[-1]["images"] = [image_b64]
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
    image_b64: Optional[str] = None,
    image_mime: Optional[str] = None,
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
    if image_b64:
        messages[-1]["content"] = [
            {"type": "text", "text": text or "(image)"},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_mime or 'image/png'};base64,{image_b64}"
                },
            },
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
        await database.messages.find({"conversation_id": conv_id}, {"_id": 0})
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


# =========================================================================
# Chat
# =========================================================================
@api_router.post("/chat/send")
async def chat_send(
    conversation_id: str = Form(...),
    text: str = Form(""),
    image: Optional[UploadFile] = File(None),
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
    if not text and image is None:
        raise HTTPException(status_code=400, detail="Empty message")

    # --- Image eventuelle ---
    image_b64 = None
    image_mime = None
    if image is not None:
        image_bytes = await image.read()
        if len(image_bytes) > settings.max_image_mb * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"Image too large (max {settings.max_image_mb}MB)",
            )
        image_mime = image.content_type or "image/png"
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # --- Message utilisateur ---
    user_msg_id = str(uuid.uuid4())
    user_msg_doc = {
        "id": user_msg_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": text or "(image)",
        "has_image": image_b64 is not None,
        "image_b64": image_b64,
        "image_mime": image_mime,
        "created_at": now_iso(),
    }
    await database.messages.insert_one(user_msg_doc)

    # --- Historique (hors message courant) ---
    history = (
        await database.messages.find(
            {"conversation_id": conversation_id, "id": {"$ne": user_msg_id}},
            {"_id": 0, "image_b64": 0},
        )
        .sort("created_at", 1)
        .to_list(2000)
    )
    history = history[-settings.history_turns :]

    # --- Generation ---
    try:
        ai_response, tool_steps, meta = await generate_ai_response(
            history=history,
            text=text,
            image_b64=image_b64,
            image_mime=image_mime,
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
        "content": ai_response,
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
        update_fields["title"] = (text or "Image chat")[:50]
    await database.conversations.update_one(
        {"id": conversation_id}, {"$set": update_fields}
    )

    user_msg_doc.pop("image_b64", None)
    user_msg_doc.pop("_id", None)
    ai_msg_doc.pop("_id", None)
    return {"user_message": user_msg_doc, "ai_message": ai_msg_doc}


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
    history = prior[:-1][-settings.history_turns :]
    raw_text = prompt_msg.get("content", "") or ""
    text = "" if raw_text == "(image)" else raw_text
    image_b64 = prompt_msg.get("image_b64")
    image_mime = prompt_msg.get("image_mime") or "image/png"

    try:
        ai_response, tool_steps, meta = await generate_ai_response(
            history=history,
            text=text,
            image_b64=image_b64,
            image_mime=image_mime,
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
        "content": ai_response,
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
_CATALOG_CACHE: dict[str, tuple[float, list[str]]] = {}
_CATALOG_TTL = 600.0


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
    for pid in ("opencode", "ollama_cloud"):
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
