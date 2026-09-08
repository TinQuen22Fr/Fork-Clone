from fastapi.staticfiles import StaticFiles
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
from google import genai
from google.genai import types as genai_types
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

        # --- Git (automatisation commit/push) ---
        self.github_pat: str = _env("GITHUB_PAT")

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

        # --- Gemini (alternative gratuite via clé API Google AI Studio) ---
        self.gemini_api_key: str = _env("GEMINI_API_KEY")
        self.gemini_model: str = _env("GEMINI_MODEL", "gemini-3.6-flash")

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

# Dossier screenshots
os.makedirs('/var/www/forge/screenshots', exist_ok=True)
app.mount("/screenshots", StaticFiles(directory="/var/www/forge/screenshots"), name="screenshots")
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

# Racine autorisée en écriture pour l'outil write_file.
WORKSPACE_ROOT = Path("/var/www/forge/workspace").resolve()
# Repo git ciblé par git_commit_push (nom du dépôt cloné dans le workspace).
GIT_REPO_PATH = WORKSPACE_ROOT / "Fork-Clone"
GIT_ALLOWED_BRANCH = "claude-ai"


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
        "name": "write_file",
        "description": (
            "Crée ou modifie un fichier texte. Restreint au dossier "
            "/var/www/forge/workspace (et ses sous-dossiers) pour des raisons "
            "de sécurité."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Chemin du fichier (absolu ou relatif à workspace/).",
                },
                "content": {
                    "type": "string",
                    "description": "Contenu complet à écrire dans le fichier.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "overwrite (défaut) ou append.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "git_commit_push",
        "description": (
            "Ajoute (git add -A), commit et pousse (git push) les changements "
            "du dépôt cloné dans /var/www/forge/workspace/Fork-Clone, "
            "uniquement sur la branche 'claude-ai'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message de commit.",
                }
            },
            "required": ["message"],
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


def _tool_write_file(path: str, content: str, mode: str = "overwrite") -> str:
    if not path:
        return "Erreur: chemin vide."
    if content is None:
        return "Erreur: contenu manquant."
    if len(content) > 1_000_000:
        return "Erreur: contenu trop volumineux (max 1 Mo)."
    try:
        raw = Path(path).expanduser()
        target = raw if raw.is_absolute() else (WORKSPACE_ROOT / raw)
        target = target.resolve()
        # Garde-fou: interdit toute écriture hors de WORKSPACE_ROOT.
        if WORKSPACE_ROOT != target and WORKSPACE_ROOT not in target.parents:
            return (
                f"Erreur: écriture refusée hors de {WORKSPACE_ROOT} "
                f"(chemin résolu: {target})."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.is_file()
        if mode == "append":
            with target.open("a", encoding="utf-8") as f:
                f.write(content)
        else:
            target.write_text(content, encoding="utf-8")
        action = "modifié" if existed else "créé"
        return f"OK: fichier {action} ({len(content)} caractères écrits) -> {target}"
    except Exception as e:  # noqa: BLE001
        return f"Erreur d'écriture: {e}"


def _tool_git_commit_push(message: str) -> str:
    if not message or not message.strip():
        return "Erreur: message de commit vide."
    if not GIT_REPO_PATH.is_dir():
        return f"Erreur: dépôt introuvable: {GIT_REPO_PATH}"

    def _run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            cwd=str(GIT_REPO_PATH),
            capture_output=True,
            text=True,
            timeout=30,
        )

    try:
        branch_res = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        current_branch = (branch_res.stdout or "").strip()
        if current_branch != GIT_ALLOWED_BRANCH:
            return (
                f"Erreur: branche courante '{current_branch}' != "
                f"'{GIT_ALLOWED_BRANCH}'. Push refusé par garde-fou."
            )

        add_res = _run(["git", "add", "-A"])
        if add_res.returncode != 0:
            return f"Erreur 'git add': {add_res.stderr}"

        diff_res = _run(["git", "diff", "--cached", "--quiet"])
        if diff_res.returncode == 0:
            return "Rien à committer (aucun changement détecté)."

        commit_res = _run([
            "git", "-c", "user.email=bot@forge.local",
            "-c", "user.name=Claude Forge Bot",
            "commit", "-m", message,
        ])
        if commit_res.returncode != 0:
            return f"Erreur 'git commit': {commit_res.stderr}"

        github_pat = settings.github_pat
        push_args = ["git", "push", "origin", GIT_ALLOWED_BRANCH]
        env = None
        if github_pat:
            remote_res = _run(["git", "remote", "get-url", "origin"])
            remote_url = (remote_res.stdout or "").strip()
            if remote_url.startswith("https://") and "@" not in remote_url.split("//", 1)[-1].split("/", 1)[0]:
                auth_url = remote_url.replace(
                    "https://", f"https://x-access-token:{github_pat}@", 1
                )
                push_res = subprocess.run(
                    ["git", "push", auth_url, GIT_ALLOWED_BRANCH],
                    cwd=str(GIT_REPO_PATH),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
                push_res = _run(push_args)
        else:
            push_res = _run(push_args)

        if push_res.returncode != 0:
            safe_stderr = (push_res.stderr or "").replace(github_pat, "***") if github_pat else push_res.stderr
            return f"Commit OK mais 'git push' a échoué: {safe_stderr}"

        log_res = _run(["git", "log", "-1", "--format=%H %s"])
        return f"OK: commit + push réussis sur '{GIT_ALLOWED_BRANCH}'.\n{log_res.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return "Erreur: timeout de 30 secondes dépassé."
    except Exception as e:  # noqa: BLE001
        return f"Erreur git: {e}"


def _run_tool(name: str, tool_input: dict) -> str:
    if name == "bash":
        return _tool_bash(tool_input.get("command", ""))
    if name == "read_file":
        return _tool_read_file(tool_input.get("path", ""))
    if name == "write_file":
        return _tool_write_file(
            tool_input.get("path", ""),
            tool_input.get("content", ""),
            tool_input.get("mode", "overwrite"),
        )
    if name == "git_commit_push":
        return _tool_git_commit_push(tool_input.get("message", ""))
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


def _tools_to_gemini(tools: list[dict]) -> list[genai_types.FunctionDeclaration]:
    """Convertit le schéma TOOLS (format Anthropic input_schema) vers le format
    FunctionDeclaration attendu par le SDK google-genai (parameters)."""
    declarations = []
    for t in tools:
        declarations.append(
            genai_types.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("input_schema", {"type": "object", "properties": {}}),
            )
        )
    return declarations



async def tool_capture_screen(url: str = "https://forge.quentin-astro.fr") -> str:
    """Prend une capture d'écran de l'interface et retourne le lien Markdown pour l'afficher."""
    from capture_ui import capture
    import os
    import time
    filename = f"ui_{int(time.time())}.png"
    await capture(target_url=url, filename=filename)
    return f"![Capture UI](/screenshots/{filename})"


async def generate_ai_response(
    history: list[dict],
    text: str,
    image_b64: Optional[str],
    image_mime: Optional[str],
    provider: str = "claude",
) -> tuple[str, list[dict]]:
    """Dispatcher multi-provider : route vers Claude (défaut, abonnement OAuth)
    ou Gemini (clé API Google AI Studio, gratuite) selon `provider`.

    Le contrat de sortie est identique quel que soit le provider :
    (texte_final, tool_steps) où tool_steps liste les outils exécutés.
    """
    if provider == "gemini":
        return await _generate_gemini(history, text, image_b64, image_mime)
    return await _generate_claude(history, text, image_b64, image_mime)


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

    return (
        "(Boucle d'outils interrompue : trop d'itérations. Réponse partielle.)",
        tool_steps,
    )


async def _generate_gemini(
    history: list[dict],
    text: str,
    image_b64: Optional[str],
    image_mime: Optional[str],
) -> tuple[str, list[dict]]:
    """Génère une réponse via Google Gemini (clé API gratuite Google AI Studio),
    avec support du tool calling et boucle agentique équivalente à Claude."""
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "GEMINI_API_KEY absent. Récupère une clé gratuite sur "
                "Google AI Studio (aistudio.google.com) puis colle-la dans "
                "backend/.env."
            ),
        )

    client = genai.Client(api_key=settings.gemini_api_key)

    # Construction des contents au format Gemini : rôle "model" (pas "assistant")
    contents: list[genai_types.Content] = []
    for h in history:
        role = "user" if h.get("role") == "user" else "model"
        content = (h.get("content") or "").strip()
        if not content:
            continue
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=content)]))

    current_parts: list[genai_types.Part] = []
    if image_b64:
        current_parts.append(
            genai_types.Part.from_bytes(
                data=base64.b64decode(image_b64),
                mime_type=image_mime or "image/png",
            )
        )
    current_parts.append(genai_types.Part(text=text or "(image)"))
    contents.append(genai_types.Content(role="user", parts=current_parts))

    tool_steps: list[dict] = []
    gemini_tools = None
    if settings.enable_tools:
        gemini_tools = [genai_types.Tool(function_declarations=_tools_to_gemini(TOOLS))]

    config = genai_types.GenerateContentConfig(
        system_instruction=settings.claude_system_prompt,
        tools=gemini_tools,
        max_output_tokens=settings.claude_max_tokens,
    )

    for _ in range(MAX_TOOL_ITERS):
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # google.genai.errors.APIError et autres
            raise HTTPException(status_code=502, detail=f"Erreur Gemini: {exc}") from exc

        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if candidate and candidate.content else []

        function_calls = [p for p in parts if getattr(p, "function_call", None)]

        if function_calls and settings.enable_tools:
            # Réinjecter le tour "model" complet (avec les function_call)
            contents.append(genai_types.Content(role="model", parts=parts))

            response_parts: list[genai_types.Part] = []
            for p in function_calls:
                fc = p.function_call
                name = fc.name or ""
                tinput = dict(fc.args) if fc.args else {}
                output = _run_tool(name, tinput)
                tool_steps.append({
                    "tool": name,
                    "input": tinput,
                    "output": output[:4000],
                })
                response_parts.append(
                    genai_types.Part.from_function_response(
                        name=name,
                        response={"result": output or "(vide)"},
                    )
                )
            contents.append(genai_types.Content(role="user", parts=response_parts))
            continue

        # Réponse finale (texte)
        text_parts = [p.text for p in parts if getattr(p, "text", None)]
        answer = "\n".join(t for t in text_parts if t).strip()
        return (answer or "(reponse vide)", tool_steps)

    return (
        "(Boucle d'outils interrompue : trop d'itérations. Réponse partielle.)",
        tool_steps,
    )


async def _call_anthropic(messages: list[dict]) -> dict:
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
    if settings.enable_tools:
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
    file: Optional[UploadFile] = File(None),

    current_user: dict = Depends(get_current_user),
):
    database = get_db()

    conv = await database.conversations.find_one(
        {"id": conversation_id, "user_id": current_user["id"]}
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    text = (text or "").strip()
    if not text and image is None:
        raise HTTPException(status_code=400, detail="Empty message")

    if provider not in ("claude", "gemini"):
        raise HTTPException(
            status_code=400, detail="provider invalide (attendu: 'claude' ou 'gemini')"
        )

    # --- Fichier eventuel ---
    full_prompt = text
    file_name = None
    if file is not None:
        file_name = file.filename
        file_content = (await file.read()).decode("utf-8", errors="replace")
        file_block = f"\n\n--- Fichier: {file_name} ---\n{file_content}"
        full_prompt = f"{text}{file_block}" if text else file_block.strip()

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
        "content": (f"{text} [📎 {file_name}]" if file_name and text else (f"[📎 {file_name}]" if file_name else text)) or "(image)",
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
        ai_response, tool_steps = await generate_ai_response(
            history=history,
            text=full_prompt,
            image_b64=image_b64,
            image_mime=image_mime,
            provider=provider,
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
        ai_response, tool_steps = await generate_ai_response(
            history=history,
            text=full_prompt,
            image_b64=image_b64,
            image_mime=image_mime,
            provider=getattr(payload, "provider", "claude"),
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
