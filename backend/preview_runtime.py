"""
Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
Logiciel libre sous GNU GPL v3 — voir LICENSE.

preview_runtime.py — demarre reellement l'application d'un projet du workspace
sur son port dedie (8090+). Sans ce composant, Nginx proxifie vers un port ou
personne n'ecoute : 502 Bad Gateway.

Deux formes de projet sont gerees :

  * Monorepo full-stack (cas des projets generes par la Forge) :
      <projet>/frontend|client|web|ui  -> sert sur le port de preview   (ex. 8091)
      <projet>/backend|api|server      -> sert sur le port + 100        (ex. 8191)
    Nginx proxifie /api/... du sous-domaine vers le port + 100, donc le
    frontend appelle /api/... en relatif (aucune URL absolue a coder).

  * Projet simple a la racine :
      package.json (vite / next / react-scripts / script dev|start)
      index.html, dist/, build/, public/  -> serveur statique Python
      app.py / main.py / requirements.txt -> venv + uvicorn ou python
        (mode docker : venv dans /forge-state, hors du workspace versionne)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("forge.preview")

PHASE_STOPPED = "stopped"
PHASE_INSTALLING = "installing"
PHASE_STARTING = "starting"
PHASE_RUNNING = "running"
PHASE_ERROR = "error"

LOG_TAIL_BYTES = 64_000
API_PORT_OFFSET = 100
WEB_DIRS = ("frontend", "client", "web", "ui")
API_DIRS = ("backend", "api", "server")

# --- D10 : observabilite et garde-fous anti-boucle -----------------------
RESTART_WINDOW = 300          # fenetre de comptage des echecs (5 min)
RESTART_MAX_FAILURES = 3      # au-dela : preview desactivee
RESTART_BACKOFF = 30          # delai minimum entre deux demarrages (s)

# --- D8 : secrets et masquage -------------------------------------------
# Liste blanche : seules les cles de .env a prefixe frontend public sont
# injectees dans le conteneur. Toute cle porteuse de secret est exclue.
PUBLIC_ENV_PREFIXES = (
    "VITE_", "NEXT_PUBLIC_", "REACT_APP_", "PUBLIC_", "GATSBY_",
    "NUXT_PUBLIC_", "SVELTE_", "EXPO_PUBLIC_", "ANGULAR_",
)
SECRET_ENV_HINT = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|DSN|CREDENTIAL|PRIVATE|"
    r"MONGO_URL|DATABASE_URL|JWT)",
    re.I,
)

# Motifs de masquage (alignes sur server.py pour coherence).
_SECRET_PATTERNS = [
    re.compile(r"\b(sk-or-v1-)[A-Za-z0-9_\-]{8,}", re.I),
    re.compile(r"\b(sk-ant-[a-z0-9\-]{0,12}-)[A-Za-z0-9_\-]{8,}", re.I),
    re.compile(r"\b(sk-)[A-Za-z0-9]{20,}"),
    re.compile(r"\b(gsk_)[A-Za-z0-9]{10,}"),
    re.compile(r"\b(csk-)[A-Za-z0-9]{10,}"),
    re.compile(r"\b(nvapi-)[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\b(gh[prous]_)[A-Za-z0-9_]+"),
    re.compile(r"\b(github_pat_)[A-Za-z0-9_]{10,}"),
    re.compile(r"\b(AIza)[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(xox[baprs]-)[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_SECRET_ASSIGN = re.compile(
    r"^(\s*(?:export\s+)?[A-Z0-9_]*"
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIAL|DSN|MONGO_URL|"
    r"DATABASE_URL|DB_URL|JWT)"
    r"[A-Z0-9_]*\s*[=:]\s*)(\S.*)$",
    re.M,
)


def redact_secrets(text: str) -> str:
    """Masque les secrets (jetons connus, affectations CLE=valeur) dans un texte."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(
            lambda m: (m.group(1) + "***REDACTED***") if m.groups() else "***REDACTED***",
            out,
        )
    out = _SECRET_ASSIGN.sub(lambda m: m.group(1) + "***REDACTED***", out)
    return out


def _is_public_env_key(key: str) -> bool:
    """Vrai si la cle peut etre injectee (prefixe public, sans connotation secret)."""
    if not any(key.startswith(p) for p in PUBLIC_ENV_PREFIXES):
        return False
    if SECRET_ENV_HINT.search(key):
        return False
    return True


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.35)
        return s.connect_ex((host, port)) == 0


def _bin(name: str) -> Optional[str]:
    """Binaire absolu : le PATH d'un service systemd est souvent limite au venv."""
    found = shutil.which(name)
    if found:
        return found
    for d in ("/usr/local/bin", "/usr/bin", "/bin", "/snap/bin"):
        p = Path(d) / name
        if p.is_file():
            return str(p)
    return None


def _du_bytes(path: Path) -> int:
    """Taille totale (octets) d'un dossier, sans suivre les liens symboliques."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


class PreviewProcess:
    """Etat d'un process de preview (une cible = web, api ou app)."""

    def __init__(self, project: str, target: str, port: int, log_path: Path):
        self.project = project
        self.target = target
        self.port = port
        self.log_path = log_path
        self.proc: Optional[subprocess.Popen] = None
        self.kind: str = ""
        self.cmd: str = ""
        self.phase: str = PHASE_STOPPED
        self.message: str = ""
        self.started_at: float = 0.0
        self.running: bool = False
        self.task: Optional[asyncio.Task] = None
        self.last_exit_code: int = 0   # D10 : dernier code de sortie observe
        self.failures: int = 0          # D10 : echecs consecutifs (fenetre glissante)
        self.first_failure_at: float = 0.0


class PreviewManager:
    """Un process par cible, un port par cible, un journal par cible."""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self.state_dir = self.workspace_root / ".forge-preview"
        self.state_root = Path(
            os.environ.get("FORGE_PREVIEW_STATE_ROOT", "/var/lib/forge-preview")
        )
        self._procs: dict[str, dict[str, PreviewProcess]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_seen: dict[str, float] = {}
        self._started_at: dict[str, float] = {}       # D10 : dernier demarrage (backoff)
        self._disabled: set[str] = set()              # D10 : previews desactivees
        self.driver: str = os.environ.get("PREVIEW_DRIVER", "native").strip().lower()
        self.wrapper: str = os.environ.get(
            "FORGE_PREVIEW_WRAPPER", "/usr/local/bin/forge-preview"
        )
        # Nettoyage et quotas disque (D7).
        self.prune_older_days: int = int(os.environ.get("PREVIEW_PRUNE_OLDER_DAYS", "30"))
        self.max_workspace_gb: float = float(os.environ.get("PREVIEW_MAX_WORKSPACE_GB", "10"))
        self.min_free_disk_gb: int = int(os.environ.get("PREVIEW_MIN_FREE_DISK_GB", "50"))

    # -- inactivite -------------------------------------------------------
    def touch(self, project: str) -> None:
        """Marque une activite (clic, status, logs) sur la preview d'un projet."""
        self._last_seen[project] = time.time()

    def idle_projects(self, max_idle: float) -> list[str]:
        """Projets dont un process tourne et sans activite depuis max_idle s."""
        now, out = time.time(), []
        for project, targets in self._procs.items():
            alive = any(t.running for t in targets.values())
            if alive and now - self._last_seen.get(project, 0.0) > max_idle:
                out.append(project)
        return out

    # -- utilitaires ------------------------------------------------------
    def _lock(self, project: str) -> asyncio.Lock:
        return self._locks.setdefault(project, asyncio.Lock())

    # -- D10 : garde-fous anti-boucle ------------------------------------
    def _mark_failure(self, st: PreviewProcess) -> bool:
        """Comptabilise un echec. Renvoie True si la preview doit etre desactivee."""
        now = time.time()
        if not st.first_failure_at or now - st.first_failure_at > RESTART_WINDOW:
            st.failures = 0
            st.first_failure_at = now
        st.failures += 1
        return st.failures >= RESTART_MAX_FAILURES

    def _clear_failures(self, st: PreviewProcess) -> None:
        st.failures = 0
        st.first_failure_at = 0.0

    def is_disabled(self, project: str) -> bool:
        return project in self._disabled

    def disable(self, project: str) -> None:
        self._disabled.add(project)
        for st in self._procs.get(project, {}).values():
            st.running = False

    def enable(self, project: str) -> None:
        self._disabled.discard(project)

    def backoff_remaining(self, project: str) -> float:
        """Secondes restantes avant de pouvoir redemarrer ce projet (D10)."""
        if self.driver != "docker":
            return 0.0
        last = self._started_at.get(project, 0.0)
        return max(0.0, RESTART_BACKOFF - (time.time() - last))

    def mark_started(self, project: str) -> None:
        self._started_at[project] = time.time()

    def _record_failure(self, st: PreviewProcess, message: str, code: int = 0) -> None:
        """Passe une cible en erreur et alimente le compteur anti-boucle D10."""
        st.phase = PHASE_ERROR
        st.message = message
        if code:
            st.last_exit_code = code
        if self._mark_failure(st):
            self.disable(st.project)
            st.message += (
                f" — preview desactivee apres {RESTART_MAX_FAILURES} echecs consecutifs (D10)"
            )

    def _record_success(self, st: PreviewProcess) -> None:
        self._clear_failures(st)
        st.phase = PHASE_RUNNING
        st.message = ""

    def _dirs(self) -> None:
        for d in (self.state_dir, self.state_dir / "home", self.state_dir / "npm-cache"):
            d.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project: str) -> Path:
        return self.workspace_root / project

    def log_path(self, project: str, target: str) -> Path:
        return self.state_dir / f"{project}.{target}.log"

    def _bind(self) -> str:
        """Adresse d'ecoute des applications.

        En mode natif, l'app tourne sur l'hote : 127.0.0.1 est le bon choix.
        En mode docker, l'app tourne DANS le conteneur : elle doit ecouter sur
        0.0.0.0 pour etre joignable a travers le port publie. La publication
        reste liee a 127.0.0.1 cote hote (contrat D4), seul le bind interne
        change."""
        return "0.0.0.0" if self.driver == "docker" else "127.0.0.1"

    def _venv_dir(self, d: Path) -> Path:
        """Venv propre au mode d'execution.

        En natif : .venv dans le dossier du projet (atelier de developpement).
        En docker : venv HORS du workspace versionne, dans l'etat du projet
        (/var/lib/forge-preview/<projet>, monte sur /forge-state dans le
        conteneur). Le workspace reste strictement l'atelier de dev : la
        preview ne le pollue jamais, donc Git ne voit aucun venv conteneur."""
        if self.driver != "docker":
            return d / ".venv"
        try:
            rel = d.resolve().relative_to(self.workspace_root.resolve())
            project = rel.parts[0] if rel.parts else d.name
        except (ValueError, IndexError, OSError):
            project = d.name
        return self.state_root / project / "venv"

    def _env(self, port: int, extra: Optional[dict] = None) -> dict:
        """Environnement des process enfants. HOME et caches dans le workspace
        (systemd ProtectHome=yes + PrivateTmp=yes rendent /root et /tmp
        inutilisables)."""
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(self.state_dir / "home"),
                "XDG_CACHE_HOME": str(self.state_dir / "cache"),
                "npm_config_cache": str(self.state_dir / "npm-cache"),
                "npm_config_update_notifier": "false",
                "npm_config_fund": "false",
                "npm_config_audit": "false",
                "PORT": str(port),
                "HOST": self._bind(),
                "BROWSER": "none",
                "CI": "1",
                "NODE_ENV": "development",
                "FORGE_PREVIEW_PORT": str(port),
            }
        )
        env.pop("VIRTUAL_ENV", None)
        # npm a besoin de trouver node : le PATH du service peut etre limite au venv.
        parts = [p for p in env.get("PATH", "").split(":") if p]
        for d in ("/usr/local/bin", "/usr/bin", "/bin"):
            if d not in parts:
                parts.append(d)
        env["PATH"] = ":".join(parts)
        if extra:
            env.update({k: str(v) for k, v in extra.items()})
        return env


    def _project_env(self, project: str) -> dict[str, str]:
        """Lit le .env du projet et n'en retient que les cles publiques
        (liste blanche D8). Aucune valeur sensible n'est injectee ni loguee."""
        env_file = self.project_dir(project) / ".env"
        out: dict[str, str] = {}
        if not env_file.is_file():
            return out
        try:
            for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not _is_public_env_key(key):
                    continue
                value = value.strip().strip("'\"").strip()
                out[key] = value
        except OSError:
            return out
        return out

    def _rel_script(self, target: str, name: str) -> str:
        """Chemin relatif au workspace monte d'un script borne, ecrit sous
        <projet>/.forge-preview/. Le wrapper n'autorise que ce prefixe."""
        return f".forge-preview/{target}.{name}.sh"

    def _write_script(self, project: str, target: str, name: str, body: str) -> str:
        d = self.project_dir(project) / ".forge-preview"
        d.mkdir(parents=True, exist_ok=True)
        rel = self._rel_script(target, name)
        p = d / f"{target}.{name}.sh"
        p.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n",
            encoding="utf-8",
        )
        os.chmod(p, 0o755)
        return rel

    def _rel_cwd(self, project: str, cwd: str) -> str:
        try:
            rel = Path(cwd).resolve().relative_to(self.project_dir(project).resolve())
            return rel.as_posix() if rel.as_posix() != "." else ""
        except ValueError:
            return ""

    def _docker_cmd(self, cmd: list[str], project: str) -> list[str]:
        """Adapte une commande de l'hote au conteneur.

        Transformations bornees :
        1. un chemin deja conteneur (/workspace/..., /forge-state/...) est
           laisse intact ;
        2. un chemin absolu de l'etat du projet (venv conteneur) devient
           /forge-state/<rel> ;
        3. un chemin absolu SOUS le workspace du projet devient
           /workspace/<rel> (le workspace est monte la) ;
        4. un binaire systeme de l'hote (npm, python3...) devient son nom,
           resolu par le PATH de l'image.
        Un chemin absolu hors de ces espaces est laisse tel quel : il n'existera
        pas dans le conteneur, et le faire disparaitre masquerait l'erreur."""
        known = {"npm", "npx", "node", "python3", "python", "pip", "pip3",
                 "uvicorn", "gunicorn"}
        root = self.project_dir(project).resolve() if project else None
        state_proj = (self.state_root / project).resolve() if project else None
        out: list[str] = []
        for arg in cmd:
            if not isinstance(arg, str) or not arg.startswith("/"):
                out.append(arg)
                continue
            if arg.startswith(("/workspace/", "/forge-state/")):
                out.append(arg)
                continue
            if state_proj is not None:
                try:
                    # PurePosixPath : comparaison textuelle SANS resolution
                    # de symlinks.  resolve() suivrait venv/bin/python ->
                    # /usr/bin/python3.xx et le relative_to echouerait,
                    # degradant le chemin en nom de base via « known » ->
                    # pip global -> PEP 668.
                    rel = Path(arg).relative_to(state_proj)
                    out.append("/forge-state/" + rel.as_posix())
                    continue
                except (ValueError, OSError):
                    pass
            if root is not None:
                try:
                    rel = Path(arg).resolve().relative_to(root)
                    out.append("/workspace/" + rel.as_posix())
                    continue
                except (ValueError, OSError):
                    pass
            base = arg.rsplit("/", 1)[-1]
            out.append(base if base in known else arg)
        return out

    def _script_cmd(self, st: PreviewProcess, cmd: list[str], cwd: str) -> str:
        rel = self._rel_cwd(st.project, cwd)
        head = [f"cd {shlex.quote(rel)}"] if rel else []
        head.append(shlex.join(self._docker_cmd(cmd, st.project)))
        return "\n".join(head)

    def _script_run(self, st: PreviewProcess, cmd: list[str], cwd: str) -> str:
        """Script de lancement. _bind() a deja place la bonne adresse dans la
        commande ; on exporte HOST pour les serveurs qui le lisent."""
        rel = self._rel_cwd(st.project, cwd)
        head = [f"export HOST={self._bind()}", "export PORT=" + str(st.port)]
        if rel:
            head.append(f"cd {shlex.quote(rel)}")
        head.append("exec " + shlex.join(self._docker_cmd(cmd, st.project)))
        return "\n".join(head)

    async def _container_running(self, project: str) -> bool:
        code, out = await self._docker("inspect", project, timeout=30)
        return code == 0 and '"Running": true' in out

    def _host_exec_allowed(self, argv: list[str]) -> bool:
        """D9 : en mode docker, la seule execution hote autorisee est le wrapper
        sudo (lui-meme le seul point privilegie vers Docker). Tout autre
        lancement de processus hote (Popen, create_subprocess_exec) est refuse."""
        if self.driver != "docker":
            return True
        return (
            len(argv) >= 3
            and argv[0] == "sudo"
            and argv[1] == "-n"
            and Path(argv[2]).name == "forge-preview"
        )

    async def _docker(self, *args: str, timeout: int = 120) -> tuple[int, str]:
        cmd = ["sudo", "-n", self.wrapper, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, f"timeout ({timeout}s) : forge-preview {' '.join(args)}"
        return proc.returncode, redact_secrets((out or b"").decode("utf-8", "replace"))

    # -- nettoyage et quotas disque (D7) ---------------------------------
    def disk_free_gb(self) -> float:
        """Espace libre sur / (en Go)."""
        return shutil.disk_usage("/").free / (1024 ** 3)

    def disk_alert(self) -> Optional[str]:
        """Message d'alerte si l'espace libre / passe sous le seuil D7."""
        free = self.disk_free_gb()
        if free < self.min_free_disk_gb:
            return (
                f"espace disque faible : {free:.1f} Go libres sur / "
                f"(seuil {self.min_free_disk_gb} Go)"
            )
        return None

    async def prune(self, older_days: Optional[int] = None) -> dict:
        """Nettoyage periodique D7 : rm des conteneurs arretes > N jours + image prune."""
        days = older_days or self.prune_older_days
        code, out = await self._docker("prune", "--older-days", str(days))
        return {"rc": code, "output": out.strip()}

    # -- detection --------------------------------------------------------
    def plan(self, project: str, port: int) -> dict:
        """Retourne {targets: [plan...], hint: str}. Un plan contient :
        target, port, kind, cmd, install, cwd, env."""
        d = self.project_dir(project)
        if not d.is_dir():
            return {"targets": [], "hint": f"dossier introuvable : {d}"}

        targets: list[dict] = []

        web_dir = next(
            (d / n for n in WEB_DIRS if (d / n / "package.json").is_file()), None
        )
        api_dir = next(
            (
                d / n
                for n in API_DIRS
                if (d / n).is_dir()
                and (
                    (d / n / "requirements.txt").is_file()
                    or (d / n / "package.json").is_file()
                    or any((d / n / f).is_file() for f in ("app.py", "main.py", "server.py"))
                )
            ),
            None,
        )

        if web_dir is not None:
            plan = self._detect_node(web_dir, web_dir / "package.json", port)
            plan.update({"target": "web", "port": port, "env": self._web_env(project)})
            targets.append(plan)

        if api_dir is not None:
            api_port = port + API_PORT_OFFSET
            plan = self._detect_api(api_dir, api_port)
            plan.update({"target": "api", "port": api_port, "env": self._api_env(project)})
            targets.append(plan)

        if targets:
            bad = [t for t in targets if not t.get("kind")]
            hint = " / ".join(f"{t['target']}: {t.get('hint', '')}" for t in bad)
            return {"targets": targets, "hint": hint}

        # Projet simple a la racine.
        if (d / "package.json").is_file():
            plan = self._detect_node(d, d / "package.json", port)
        else:
            plan = self._detect_python(d, port) or self._detect_static(d, port) or {
                "kind": "",
                "hint": (
                    "aucune application detectee. Attendu : frontend/ + backend/, "
                    "ou package.json, ou index.html / dist / build, "
                    "ou app.py / main.py."
                ),
            }
        plan.update({"target": "app", "port": port})
        return {"targets": [plan] if plan.get("kind") else [], "hint": plan.get("hint", "")}

    def _safe_id(self, project: str) -> str:
        """Identifiant sans danger pour un nom de cookie / cle localStorage."""
        return re.sub(r"[^a-zA-Z0-9_]", "_", project).strip("_") or "project"

    def _web_env(self, project: str) -> dict:
        """Le frontend doit appeler /api/... en relatif : Nginx proxifie /api vers
        le backend du projet. On neutralise les URL absolues des frameworks."""
        return {
            "VITE_API_URL": "/api",
            "VITE_API_BASE_URL": "/api",
            "VITE_BACKEND_URL": "/api",
            "VITE_APP_BACKEND_URL": "/api",
            "REACT_APP_BACKEND_URL": "",
            "REACT_APP_API_URL": "/api",
            "NEXT_PUBLIC_API_URL": "/api",
            "NEXT_PUBLIC_BACKEND_URL": "",
            # CRA/webpack-dev-server derriere un reverse proxy.
            "DANGEROUSLY_DISABLE_HOST_CHECK": "true",
            "WDS_SOCKET_PORT": "0",
            # Isole le token de session en localStorage de celui de l'instance
            # hote (dogfooding : ce projet peut heberger sa propre preview).
            "VITE_AUTH_TOKEN_KEY": f"forge_preview_{self._safe_id(project)}_token",
        }

    def _api_env(self, project: str) -> dict:
        """Isole le cookie de session du backend sandboxe de celui de
        l'instance hote, meme si elles partagent le meme domaine cookie
        (COOKIE_DOMAIN herite de l'environnement du process parent)."""
        return {
            "COOKIE_NAME": f"forge_preview_{self._safe_id(project)}_session",
            "COOKIE_DOMAIN": "",
        }

    def _detect_node(self, d: Path, pkg_file: Path, port: int) -> dict:
        npm = _bin("npm")
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except Exception:
            pkg = {}
        scripts = pkg.get("scripts") or {}
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}

        if not npm:
            static = self._detect_static(d, port)
            if static:
                static["hint"] = "npm absent : la version compilee est servie en statique."
                return static
            return {"kind": "", "hint": "npm introuvable sur le serveur (apt install nodejs npm)."}

        install = None
        if not (d / "node_modules").is_dir():
            install = [npm, "install", "--no-audit", "--no-fund"]

        is_vite = "vite" in deps or any(d.glob("vite.config.*"))
        is_next = "next" in deps
        is_cra = "react-scripts" in deps

        script = "dev" if "dev" in scripts else ("start" if "start" in scripts else None)
        if script is None:
            static = self._detect_static(d, port)
            if static:
                return static
            return {"kind": "", "hint": "package.json sans script `dev` ni `start`."}

        if is_vite:
            cmd = [npm, "run", script, "--", "--host", self._bind(),
                   "--port", str(port), "--strictPort"]
            kind = "node-vite"
        elif is_next:
            cmd = [npm, "run", script, "--", "-H", self._bind(), "-p", str(port)]
            kind = "node-next"
        elif is_cra:
            cmd = [npm, "run", script]
            kind = "node-cra"
        else:
            cmd = [npm, "run", script]
            kind = "node"

        return {"kind": kind, "cmd": cmd, "install": install, "cwd": str(d), "hint": ""}

    def _detect_api(self, d: Path, port: int) -> dict:
        """Backend d'un monorepo : Python d'abord, sinon Node."""
        py = self._detect_python(d, port)
        if py and py.get("kind"):
            return py
        if (d / "package.json").is_file():
            return self._detect_node(d, d / "package.json", port)
        return py or {"kind": "", "hint": "aucun point d'entree backend detecte."}

    def _detect_static(self, d: Path, port: int) -> Optional[dict]:
        for sub in ("dist", "build", "public", "."):
            target = (d / sub).resolve() if sub != "." else d
            if (target / "index.html").is_file():
                return {
                    "kind": "static",
                    "cmd": [_bin("python3") or "python3", "-m", "http.server", str(port),
                            "--bind", self._bind(), "--directory", str(target)],
                    "install": None,
                    "cwd": str(d),
                    "hint": "",
                }
        return None

    def _detect_python(self, d: Path, port: int) -> Optional[dict]:
        entry = next((f for f in ("server.py", "app.py", "main.py", "wsgi.py")
                      if (d / f).is_file()), None)
        reqs = d / "requirements.txt"
        if not entry and not reqs.is_file():
            return None

        venv = self._venv_dir(d)
        vpy = venv / "bin" / "python"
        install: Optional[list[str]] = None
        if not vpy.is_file():
            install = ["__venv__"]
        elif reqs.is_file() and not (venv / ".forge-installed").is_file():
            install = [str(vpy), "-m", "pip", "install", "-r", str(reqs)]

        if not entry:
            return {
                "kind": "",
                "hint": "requirements.txt trouve mais aucun point d'entree (app.py / main.py / server.py).",
            }

        # Lu en entier (read_text charge deja tout le fichier en memoire, la
        # troncature ne fait qu'economiser la comparaison) : un cap trop bas
        # ratait `app = FastAPI(...)` dans les gros fichiers (ex. server.py de
        # la Forge elle-meme, 180+ Ko), qui tombaient alors en kind="python"
        # -> `python server.py` s'importait puis sortait aussitot (code 0,
        # log vide) sans jamais lancer uvicorn.
        src = ""
        try:
            src = (d / entry).read_text(encoding="utf-8", errors="ignore")[:2_000_000]
        except Exception:
            pass
        module = entry[:-3]
        if "FastAPI(" in src or "Flask(" in src:
            asgi = "FastAPI(" in src
            uvicorn_bin = str(venv / "bin" / "uvicorn")
            if asgi:
                cmd = [uvicorn_bin, f"{module}:app", "--host", self._bind(), "--port", str(port)]
                kind = "python-fastapi"
            else:
                cmd = [str(vpy), entry]
                kind = "python-flask"
        else:
            cmd = [str(vpy) if (vpy.is_file() or install) else (_bin("python3") or "python3"),
                   entry]
            kind = "python"
        return {"kind": kind, "cmd": cmd, "install": install, "cwd": str(d), "hint": ""}

    # -- cycle de vie -----------------------------------------------------
    async def _start_docker(
        self, project: str, port: int, plan: dict, restart: bool
    ) -> dict:
        """Un conteneur par projet (contrat D4), tous les ports publies,
        puis un _run par cible (install + exec borne)."""
        tracked = self._procs.setdefault(project, {})
        targets = plan["targets"]
        ports = sorted({t["port"] for t in targets})

        running = await self._container_running(project)
        if running and not restart:
            for t in targets:
                tport = t["port"]
                st = tracked.get(t["target"]) or PreviewProcess(
                    project, t["target"], tport, self.log_path(project, t["target"])
                )
                st.kind = t.get("kind", "")
                st.cmd = " ".join(t.get("cmd", []))
                st.running = True
                self._record_success(st)
                tracked[t["target"]] = st
            return self.status(project, port)

        if running and restart:
            await self._docker("rm", project, timeout=60)
            running = False

        for st in list(tracked.values()):
            st.running = False

        args = [
            "start", project,
            "--workspace", str(self.project_dir(project)),
            "--port", str(ports[0]),
        ]
        if len(ports) > 1:
            args += ["--port2", str(ports[1])]
        for key, val in self._project_env(project).items():
            args += ["--env", f"{key}={val}"]
        code, out = await self._docker(*args)
        if code != 0:
            for t in targets:
                st = tracked.get(t["target"]) or PreviewProcess(
                    project, t["target"], t["port"], self.log_path(project, t["target"])
                )
                self._record_failure(st, f"echec forge-preview start (code {code}) : {out[-200:]}", code)
                tracked[t["target"]] = st
            return self.status(project, port)

        for t in targets:
            st = PreviewProcess(
                project, t["target"], t["port"], self.log_path(project, t["target"])
            )
            st.kind = t.get("kind", "")
            st.cmd = " ".join(t.get("cmd", []))
            st.running = True
            st.phase = PHASE_INSTALLING if t.get("install") else PHASE_STARTING
            tracked[t["target"]] = st
            st.task = asyncio.create_task(self._run_docker(st, t))
        return self.status(project, port)

    async def _run_docker(self, st: PreviewProcess, plan: dict) -> None:
        port = plan["port"]
        project = st.project
        target = st.target
        st.running = True
        st.started_at = time.time()

        install = plan.get("install")
        if install:
            st.phase = PHASE_INSTALLING
            if install == ["__venv__"]:
                steps = [["python3", "-m", "venv", "--clear", "/forge-state/venv"]]
                if (Path(plan["cwd"]) / "requirements.txt").is_file():
                    steps.append(["/forge-state/venv/bin/python", "-m", "pip",
                                  "install", "-r", "requirements.txt"])
            else:
                steps = [self._docker_cmd(install, project)]
            for step in steps:
                code = await self._spawn_wait(st, step, plan["cwd"], {}, st.log_path,
                                              timeout=1800)
                if code != 0:
                    self._record_failure(st, f"installation echouee (code {code}) — voir les logs", code)
                    return
            marker = self._venv_dir(Path(plan["cwd"])) / ".forge-installed"
            if marker.parent.is_dir():
                marker.write_text("ok\n", encoding="utf-8")
            fresh = self.plan(
                st.project,
                st.port - (API_PORT_OFFSET if st.target == "api" else 0),
            )
            for t in fresh["targets"]:
                if t["target"] == st.target and t.get("kind"):
                    plan = t
                    st.kind = t["kind"]
                    st.cmd = " ".join(t["cmd"])
                    break

        st.phase = PHASE_STARTING
        rel = self._write_script(
            project, target, "run", self._script_run(st, plan["cmd"], plan["cwd"])
        )
        code, out = await self._docker("exec", project, "--script", rel, "--detach")
        if code != 0:
            self._record_failure(st, f"echec lancement (code {code}) : {out[-200:]}", code)
            return
        for _ in range(360):
            if await asyncio.get_running_loop().run_in_executor(None, _port_open, port):
                self._record_success(st)
                logger.info(
                    "Preview %s/%s en ligne sur 127.0.0.1:%s (docker)",
                    project, target, port,
                )
                return
            await asyncio.sleep(0.5)
        self._record_failure(st, "port non ouvert apres 180s — voir les logs")

    async def start(self, project: str, port: int, restart: bool = False) -> dict:
        # D10 : backoff entre deux demarches d'un meme projet.
        backoff = self.backoff_remaining(project)
        if backoff > 0:
            tracked = self._procs.setdefault(project, {})
            st = tracked.setdefault(
                "app", PreviewProcess(project, "app", port, self.log_path(project, "app"))
            )
            st.phase = PHASE_ERROR
            st.message = f"demarrage trop rapproche : attendre {int(backoff) + 1} s (D10)"
            return self.status(project, port)
        # D10 : une preview desactivee (3 echecs consecutifs) n'est plus
        # relancee automatiquement. Seul un redemarrage manuel explicite
        # (restart=True) la reautorise.
        if self.is_disabled(project) and not restart:
            tracked = self._procs.setdefault(project, {})
            st = tracked.setdefault(
                "app", PreviewProcess(project, "app", port, self.log_path(project, "app"))
            )
            st.phase = PHASE_ERROR
            st.message = (
                f"preview desactivee apres {RESTART_MAX_FAILURES} echecs consecutifs (D10) — "
                "redemarrage manuel requis"
            )
            return self.status(project, port)
        if restart:
            self.enable(project)
        self.touch(project)
        alert = self.disk_alert()
        if alert:
            logger.warning("Preview %s : %s", project, alert)
        async with self._lock(project):
            plan = self.plan(project, port)
            if not plan["targets"]:
                tracked = self._procs.setdefault(project, {})
                st = tracked.setdefault(
                    "app", PreviewProcess(project, "app", port, self.log_path(project, "app"))
                )
                st.phase = PHASE_ERROR
                st.message = plan["hint"] or "type de projet non reconnu"
                return self.status(project, port)

            self._dirs()
            # D7 : controle du poids du projet avant lancement (refus au-dela du seuil).
            if self.max_workspace_gb > 0:
                size_gb = _du_bytes(self.project_dir(project)) / (1024 ** 3)
                if size_gb > self.max_workspace_gb:
                    tracked = self._procs.setdefault(project, {})
                    st = tracked.setdefault(
                        "app",
                        PreviewProcess(project, "app", port, self.log_path(project, "app")),
                    )
                    st.phase = PHASE_ERROR
                    st.message = (
                        f"projet trop volumineux : {size_gb:.2f} Go "
                        f"(seuil {self.max_workspace_gb:.0f} Go) — lancement refuse"
                    )
                    return self.status(project, port)
            tracked = self._procs.setdefault(project, {})
            if self.driver == "docker":
                self.mark_started(project)
                return await self._start_docker(project, port, plan, restart)
            for tplan in plan["targets"]:
                target, tport = tplan["target"], tplan["port"]
                st = tracked.get(target)
                if st and st.running:
                    if not restart:
                        continue
                    await self._kill(st)
                elif not restart and _port_open(tport):
                    st = st or PreviewProcess(project, target, tport, self.log_path(project, target))
                    st.phase = PHASE_RUNNING
                    st.message = "port deja ouvert (process existant)"
                    tracked[target] = st
                    continue

                if not tplan.get("kind"):
                    st = PreviewProcess(project, target, tport, self.log_path(project, target))
                    st.phase = PHASE_ERROR
                    st.message = tplan.get("hint", "cible non demarrable")
                    tracked[target] = st
                    continue

                st = PreviewProcess(project, target, tport, self.log_path(project, target))
                st.kind = tplan["kind"]
                st.cmd = " ".join(tplan["cmd"])
                st.phase = PHASE_INSTALLING if tplan.get("install") else PHASE_STARTING
                tracked[target] = st
                st.task = asyncio.create_task(self._run(st, tplan))
            return self.status(project, port)

    async def _run(self, st: PreviewProcess, plan: dict) -> None:
        if self.driver == "docker":
            await self._run_docker(st, plan)
            return
        cwd, port = plan["cwd"], plan["port"]
        env = self._env(port, plan.get("env"))
        env.update(self._project_env(st.project))
        log = st.log_path
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"demarrage {st.project}/{st.target} ({st.kind}) port {port} =====\n"
                )

            install = plan.get("install")
            if install:
                st.phase = PHASE_INSTALLING
                if install == ["__venv__"]:
                    steps = [[_bin("python3") or "python3", "-m", "venv", ".venv"]]
                    if (Path(cwd) / "requirements.txt").is_file():
                        steps.append([str(Path(cwd) / ".venv/bin/python"), "-m", "pip",
                                      "install", "-r", "requirements.txt"])
                else:
                    steps = [install]
                for step in steps:
                    code = await self._spawn_wait(st, step, cwd, env, log, timeout=1800)
                    if code != 0:
                        self._record_failure(st, f"installation echouee (code {code}) — voir les logs", code)
                        return
                marker = Path(cwd) / ".venv" / ".forge-installed"
                if marker.parent.is_dir():
                    marker.write_text("ok\n", encoding="utf-8")
                # Le plan a pu etre calcule avant la creation du venv / node_modules.
                fresh = self.plan(st.project, st.port - (API_PORT_OFFSET if st.target == "api" else 0))
                for t in fresh["targets"]:
                    if t["target"] == st.target and t.get("kind"):
                        plan = t
                        st.kind = t["kind"]
                        st.cmd = " ".join(t["cmd"])
                        break

            st.phase = PHASE_STARTING
            if not self._host_exec_allowed(plan["cmd"]):
                self._record_failure(st, "execution hote interdite en mode docker (D9)")
                return
            fh = open(log, "a", encoding="utf-8")
            st.proc = subprocess.Popen(
                plan["cmd"],
                cwd=cwd,
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            st.started_at = time.time()

            # Attente de l'ouverture du port (max 180 s : compilation possible).
            for _ in range(360):
                if st.proc.poll() is not None:
                    self._record_failure(
                        st,
                        f"le process s'est arrete (code {st.proc.returncode}) — voir les logs",
                        st.proc.returncode,
                    )
                    return
                if await asyncio.get_running_loop().run_in_executor(None, _port_open, port):
                    self._record_success(st)
                    logger.info(
                        "Preview %s/%s en ligne sur 127.0.0.1:%s", st.project, st.target, port
                    )
                    return
                await asyncio.sleep(0.5)

            self._record_failure(st, "le serveur n'a pas ouvert son port en 180 s — voir les logs")
        except FileNotFoundError as exc:
            self._record_failure(st, f"commande introuvable : {exc}")
        except Exception as exc:  # noqa: BLE001
            self._record_failure(st, f"erreur inattendue : {exc}")
            logger.exception("Preview %s/%s : echec de demarrage", st.project, st.target)

    async def _spawn_wait(
        self, st: PreviewProcess, cmd: list[str], cwd: str, env: dict, log: Path, timeout: int
    ) -> int:
        if self.driver == "docker":
            rel = self._write_script(
                st.project, st.target, "install", self._script_cmd(st, cmd, cwd)
            )
            code, out = await self._docker(
                "exec", st.project, "--script", rel, timeout=timeout
            )
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"\n$ {' '.join(cmd)}\n{redact_secrets(out)}\n")
            return code
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n$ {' '.join(cmd)}\n")
            fh.flush()
            if not self._host_exec_allowed(cmd):
                st.phase = PHASE_ERROR
                st.message = "execution hote interdite en mode docker (D9)"
                return 1
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=fh, stderr=subprocess.STDOUT,
            )
        try:
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124

    async def _kill(self, st: PreviewProcess) -> None:
        if st.task and not st.task.done():
            st.task.cancel()
        if self.driver == "docker":
            st.running = False
            st.phase = PHASE_STOPPED
            st.message = ""
            return
        proc = st.proc
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
            for _ in range(20):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.25)
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
        st.proc = None
        st.running = False
        st.phase = PHASE_STOPPED
        st.message = ""

    async def stop(self, project: str, port: int) -> dict:
        async with self._lock(project):
            self.enable(project)
            if self.driver == "docker":
                await self._docker("stop", project, timeout=60)
                for st in self._procs.get(project, {}).values():
                    st.running = False
                    st.phase = PHASE_STOPPED
                    st.message = ""
                return self.status(project, port)
            for st in list(self._procs.get(project, {}).values()):
                await self._kill(st)
            return self.status(project, port)

    async def stop_all(self) -> None:
        for project in list(self._procs):
            if self.driver == "docker":
                await self._docker("stop", project, timeout=60)
                for st in self._procs[project].values():
                    st.running = False
                    st.phase = PHASE_STOPPED
                    st.message = ""
            else:
                for st in list(self._procs[project].values()):
                    await self._kill(st)

    # -- lecture ----------------------------------------------------------
    def status(self, project: str, port: int) -> dict:
        plan = self.plan(project, port)
        tracked = self._procs.get(project, {})
        targets: list[dict] = []

        planned = {t["target"]: t for t in plan["targets"]}
        for name in list(planned) + [t for t in tracked if t not in planned]:
            tplan = planned.get(name, {})
            st = tracked.get(name)
            tport = tplan.get("port") or (st.port if st else port)
            alive = bool(st and st.running)
            open_ = _port_open(tport)
            phase = st.phase if st else PHASE_STOPPED
            if phase == PHASE_RUNNING and not (alive or open_):
                phase = PHASE_STOPPED
            elif st is None and open_:
                phase = PHASE_RUNNING
            elif st is None and not tplan.get("kind") and tplan:
                phase = PHASE_ERROR
            targets.append(
                {
                    "target": name,
                    "port": tport,
                    "phase": phase,
                    "message": redact_secrets(st.message if st else tplan.get("hint", "")),
                    "kind": (st.kind if st and st.kind else tplan.get("kind", "")),
                    "command": (st.cmd if st and st.cmd else " ".join(tplan.get("cmd", []))),
                    "pid": (st.proc.pid if (alive and st.proc) else None),
                    "uptime": int(time.time() - st.started_at) if (st and alive and st.started_at) else 0,
                    "port_open": open_,
                    "last_exit_code": st.last_exit_code if st else None,
                    "failures": st.failures if st else 0,
                }
            )

        phases = [t["phase"] for t in targets]
        if not targets:
            phase = PHASE_ERROR if plan["hint"] else PHASE_STOPPED
        elif PHASE_ERROR in phases:
            phase = PHASE_ERROR
        elif PHASE_INSTALLING in phases:
            phase = PHASE_INSTALLING
        elif PHASE_STARTING in phases:
            phase = PHASE_STARTING
        elif all(p == PHASE_RUNNING for p in phases):
            phase = PHASE_RUNNING
        elif PHASE_RUNNING in phases:
            phase = PHASE_STARTING
        else:
            phase = PHASE_STOPPED

        web = next((t for t in targets if t["target"] in ("web", "app")), None)
        msgs = [f"{t['target']}: {t['message']}" for t in targets if t["message"]]
        return {
            "project": project,
            "port": (web or {}).get("port", port),
            "phase": phase,
            "message": redact_secrets("; ".join(msgs) or plan["hint"]),
            "kind": " + ".join(f"{t['target']}:{t['kind']}" for t in targets if t["kind"]),
            "command": (web or {}).get("command", ""),
            "pid": (web or {}).get("pid"),
            "uptime": (web or {}).get("uptime", 0),
            "port_open": (web or {}).get("port_open", False),
            "disabled": project in self._disabled,
            "backoff_remaining": self.backoff_remaining(project),
            "last_exit_code": (web or {}).get("last_exit_code") if web else None,
            "stats": self.stats(project),
            "targets": targets,
            "log": str(self.log_path(project, (web or {}).get("target", "app"))),
        }

    def stats(self, project: str) -> dict:
        """Metriques instantanees du conteneur (D10) : CPU, RAM, PIDs.

        Renvoie des champs None en mode natif (pas de conteneur) et en cas
        d'absence/echec du wrapper, sans jamais lever.
        """
        if self.driver != "docker":
            return {
                "cpu_percent": None,
                "mem_usage": None,
                "mem_percent": None,
                "pids": None,
            }
        none = {"cpu_percent": None, "mem_usage": None, "mem_percent": None, "pids": None}
        try:
            # D10d : ne lire les metriques que si le conteneur tourne reellement.
            # Un conteneur stoppe (mais non supprime) repond encore a `docker stats`
            # avec des zeros, il faut donc verifier l'etat via `docker inspect`.
            chk = subprocess.run(
                ["sudo", "-n", self.wrapper, "inspect", project],
                capture_output=True, text=True, timeout=30,
            )
            if chk.returncode != 0 or '"Running": true' not in chk.stdout:
                return none
            proc = subprocess.run(
                ["sudo", "-n", self.wrapper, "stats", project],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                return none
            out = proc.stdout.strip()
        except Exception:
            return none
        # Format : "0.01%|1.234MiB / 1.944GiB|1"
        parts = out.split("|")
        cpu = parts[0].strip() if len(parts) > 0 else ""
        mem = parts[1].strip() if len(parts) > 1 else ""
        pids = parts[2].strip() if len(parts) > 2 else ""
        mem_usage, mem_percent = None, None
        m = re.match(r"^([^ ]+)\s*/\s*([0-9.]+[KMGT]iB)", mem)
        if m:
            mem_usage = m.group(1)
            mem_percent = m.group(2)
        return {
            "cpu_percent": cpu or None,
            "mem_usage": mem_usage,
            "mem_percent": mem_percent,
            "pids": int(pids) if pids.isdigit() else None,
        }

    def active_previews(self) -> dict:
        """Resume D10 destine a /chat/status : pour chaque preview active,
        etat, uptime, dernier code de sortie et metriques CPU/RAM du conteneur.
        """
        out: dict = {}
        for project, targets in self._procs.items():
            if not targets:
                continue
            alive = any(t.running for t in targets.values())
            web = targets.get("web") or targets.get("app")
            out[project] = {
                "running": alive,
                "disabled": project in self._disabled,
                "uptime": int(time.time() - web.started_at) if (web and web.started_at) else 0,
                "last_exit_code": web.last_exit_code if web else None,
                "phase": web.phase if web else PHASE_STOPPED,
                "stats": self.stats(project),
            }
        return out

    def logs(self, project: str, target: str = "", max_bytes: int = LOG_TAIL_BYTES) -> str:
        if self.driver == "docker":
            n = min(5000, max(50, max_bytes // 100))
            try:
                out = subprocess.run(
                    ["sudo", "-n", self.wrapper, "logs", project, "--tail", str(n)],
                    capture_output=True, text=True, timeout=30,
                )
                return redact_secrets(out.stdout or "")
            except Exception:
                return ""
        candidates = (
            [self.log_path(project, target)]
            if target
            else [self.log_path(project, t) for t in ("web", "app", "api")]
        )
        for p in candidates:
            if p.is_file():
                size = p.stat().st_size
                with open(p, "rb") as fh:
                    if size > max_bytes:
                        fh.seek(size - max_bytes)
                    return redact_secrets(fh.read().decode("utf-8", "replace"))
        return ""
