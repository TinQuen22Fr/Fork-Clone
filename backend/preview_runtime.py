"""
Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
Logiciel libre sous GNU GPL v3 — voir LICENSE.

preview_runtime.py — demarre reellement l'application d'un projet du workspace
sur son port dedie (8090+). Sans ce composant, Nginx proxifie vers un port ou
personne n'ecoute : 502 Bad Gateway systematique.

Types de projet detectes automatiquement :
  * Node  : package.json (vite / next / react-scripts / script dev|start)
  * Static: index.html, dist/, build/, public/  -> http.server Python
  * Python: app.py / main.py / server.py / requirements.txt -> venv + lancement
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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


class PreviewProcess:
    """Etat d'une preview (process + phase + journal)."""

    def __init__(self, project: str, port: int, log_path: Path):
        self.project = project
        self.port = port
        self.log_path = log_path
        self.proc: Optional[subprocess.Popen] = None
        self.kind: str = ""
        self.cmd: str = ""
        self.phase: str = PHASE_STOPPED
        self.message: str = ""
        self.started_at: float = 0.0
        self.task: Optional[asyncio.Task] = None


class PreviewManager:
    """Gestionnaire des previews : un process par projet, un port par projet."""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self.state_dir = self.workspace_root / ".forge-preview"
        self._procs: dict[str, PreviewProcess] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # -- utilitaires ------------------------------------------------------
    def _lock(self, project: str) -> asyncio.Lock:
        return self._locks.setdefault(project, asyncio.Lock())

    def _dirs(self) -> None:
        for d in (self.state_dir, self.state_dir / "home", self.state_dir / "npm-cache"):
            d.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project: str) -> Path:
        return self.workspace_root / project

    def _log_path(self, project: str) -> Path:
        return self.state_dir / f"{project}.log"

    def _env(self, port: int) -> dict:
        """Environnement des process enfants : HOME et caches dans le workspace
        (systemd ProtectHome=yes + PrivateTmp=yes rendent /root et /tmp inutilisables)."""
        env = dict(os.environ)
        home = self.state_dir / "home"
        env.update(
            {
                "HOME": str(home),
                "XDG_CACHE_HOME": str(self.state_dir / "cache"),
                "npm_config_cache": str(self.state_dir / "npm-cache"),
                "npm_config_update_notifier": "false",
                "npm_config_fund": "false",
                "npm_config_audit": "false",
                "PORT": str(port),
                "HOST": "127.0.0.1",
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
        return env

    # -- detection --------------------------------------------------------
    def detect(self, project: str, port: int) -> dict:
        """Retourne {kind, cmd(list), install(list|None), cwd, hint}."""
        d = self.project_dir(project)
        if not d.is_dir():
            return {"kind": "", "hint": f"dossier introuvable : {d}"}

        pkg_file = d / "package.json"
        if pkg_file.is_file():
            return self._detect_node(d, pkg_file, port)

        py = self._detect_python(d, port)
        if py:
            return py

        static = self._detect_static(d, port)
        if static:
            return static

        return {
            "kind": "",
            "hint": (
                "aucune application detectee. Attendu : package.json, "
                "index.html / dist / build, ou app.py / main.py."
            ),
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
            return {
                "kind": "",
                "hint": "package.json sans script `dev` ni `start`.",
            }

        if is_vite:
            cmd = [npm, "run", script, "--", "--host", "127.0.0.1",
                   "--port", str(port), "--strictPort"]
            kind = "node-vite"
        elif is_next:
            cmd = [npm, "run", script, "--", "-H", "127.0.0.1", "-p", str(port)]
            kind = "node-next"
        elif is_cra:
            cmd = [npm, "run", script]
            kind = "node-cra"
        else:
            cmd = [npm, "run", script]
            kind = "node"

        return {"kind": kind, "cmd": cmd, "install": install, "cwd": str(d), "hint": ""}

    def _detect_static(self, d: Path, port: int) -> Optional[dict]:
        for sub in ("dist", "build", "public", "."):
            target = (d / sub).resolve() if sub != "." else d
            if (target / "index.html").is_file():
                py = _bin("python3") or "python3"
                return {
                    "kind": "static",
                    "cmd": [py, "-m", "http.server", str(port),
                            "--bind", "127.0.0.1", "--directory", str(target)],
                    "install": None,
                    "cwd": str(d),
                    "hint": "",
                }
        return None

    def _detect_python(self, d: Path, port: int) -> Optional[dict]:
        entry = next((f for f in ("app.py", "main.py", "server.py", "wsgi.py")
                      if (d / f).is_file()), None)
        reqs = d / "requirements.txt"
        if not entry and not reqs.is_file():
            return None

        venv = d / ".venv"
        vpy = venv / "bin" / "python"
        install: Optional[list[str]] = None
        if not vpy.is_file():
            install = ["__venv__"]
        elif reqs.is_file() and not (venv / ".forge-installed").is_file():
            install = [str(vpy), "-m", "pip", "install", "-r", str(reqs)]

        python = str(vpy) if (vpy.is_file() or install) else (_bin("python3") or "python3")
        if not entry:
            return {
                "kind": "",
                "hint": "requirements.txt trouve mais aucun point d'entree (app.py / main.py).",
            }

        src = ""
        try:
            src = (d / entry).read_text(encoding="utf-8", errors="ignore")[:20000]
        except Exception:
            pass
        module = entry[:-3]
        if "FastAPI(" in src:
            cmd = [str(venv / "bin" / "uvicorn"), f"{module}:app",
                   "--host", "127.0.0.1", "--port", str(port)]
            kind = "python-fastapi"
        else:
            cmd = [python, entry]
            kind = "python"
        return {"kind": kind, "cmd": cmd, "install": install, "cwd": str(d), "hint": ""}

    # -- cycle de vie -----------------------------------------------------
    async def start(self, project: str, port: int, restart: bool = False) -> dict:
        async with self._lock(project):
            st = self._procs.get(project)
            if st and st.proc and st.proc.poll() is None:
                if not restart:
                    return self.status(project, port)
                await self._kill(st)
            elif _port_open(port) and not restart:
                # Quelque chose ecoute deja (process orphelin ou autre service).
                s = self._procs.setdefault(project, PreviewProcess(project, port, self._log_path(project)))
                s.phase = PHASE_RUNNING
                s.message = "port deja ouvert (process existant)"
                return self.status(project, port)

            plan = self.detect(project, port)
            if not plan.get("kind"):
                st = self._procs.setdefault(
                    project, PreviewProcess(project, port, self._log_path(project))
                )
                st.phase = PHASE_ERROR
                st.message = plan.get("hint") or "type de projet non reconnu"
                return self.status(project, port)

            self._dirs()
            st = PreviewProcess(project, port, self._log_path(project))
            st.kind = plan["kind"]
            st.cmd = " ".join(plan["cmd"])
            st.phase = PHASE_INSTALLING if plan.get("install") else PHASE_STARTING
            st.message = ""
            self._procs[project] = st
            st.task = asyncio.create_task(self._run(st, plan, port))
            return self.status(project, port)

    async def _run(self, st: PreviewProcess, plan: dict, port: int) -> None:
        cwd = plan["cwd"]
        env = self._env(port)
        log = st.log_path
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"demarrage {st.project} ({st.kind}) port {port} =====\n"
                )

            install = plan.get("install")
            if install:
                st.phase = PHASE_INSTALLING
                if install == ["__venv__"]:
                    steps = [
                        [_bin("python3") or "python3", "-m", "venv", ".venv"],
                    ]
                    reqs = Path(cwd) / "requirements.txt"
                    if reqs.is_file():
                        steps.append(
                            [str(Path(cwd) / ".venv/bin/python"), "-m", "pip",
                             "install", "-r", "requirements.txt"]
                        )
                else:
                    steps = [install]
                for step in steps:
                    code = await self._spawn_wait(step, cwd, env, log, timeout=900)
                    if code != 0:
                        st.phase = PHASE_ERROR
                        st.message = f"installation echouee (code {code}) — voir les logs"
                        return
                marker = Path(cwd) / ".venv" / ".forge-installed"
                if marker.parent.is_dir():
                    marker.write_text("ok\n", encoding="utf-8")
                # Le plan a peut-etre ete calcule avant la creation du venv.
                plan = self.detect(st.project, port) or plan
                if not plan.get("kind"):
                    st.phase = PHASE_ERROR
                    st.message = plan.get("hint", "type de projet non reconnu")
                    return
                st.cmd = " ".join(plan["cmd"])

            st.phase = PHASE_STARTING
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

            # Attente de l'ouverture du port (max 180 s : npm/vite peut compiler).
            for _ in range(360):
                if st.proc.poll() is not None:
                    st.phase = PHASE_ERROR
                    st.message = (
                        f"le process s'est arrete (code {st.proc.returncode}) — voir les logs"
                    )
                    return
                if await asyncio.get_running_loop().run_in_executor(None, _port_open, port):
                    st.phase = PHASE_RUNNING
                    st.message = ""
                    logger.info("Preview %s en ligne sur 127.0.0.1:%s", st.project, port)
                    return
                await asyncio.sleep(0.5)

            st.phase = PHASE_ERROR
            st.message = "le serveur n'a pas ouvert son port en 180 s — voir les logs"
        except FileNotFoundError as exc:
            st.phase = PHASE_ERROR
            st.message = f"commande introuvable : {exc}"
        except Exception as exc:  # noqa: BLE001
            st.phase = PHASE_ERROR
            st.message = f"erreur inattendue : {exc}"
            logger.exception("Preview %s : echec de demarrage", st.project)

    async def _spawn_wait(
        self, cmd: list[str], cwd: str, env: dict, log: Path, timeout: int
    ) -> int:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n$ {' '.join(cmd)}\n")
            fh.flush()
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT
            )
        try:
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124

    async def _kill(self, st: PreviewProcess) -> None:
        if st.task and not st.task.done():
            st.task.cancel()
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
        st.phase = PHASE_STOPPED
        st.message = ""

    async def stop(self, project: str, port: int) -> dict:
        async with self._lock(project):
            st = self._procs.get(project)
            if st:
                await self._kill(st)
            else:
                st = PreviewProcess(project, port, self._log_path(project))
                self._procs[project] = st
            return self.status(project, port)

    async def stop_all(self) -> None:
        for project in list(self._procs):
            st = self._procs[project]
            await self._kill(st)

    # -- lecture ----------------------------------------------------------
    def status(self, project: str, port: int) -> dict:
        st = self._procs.get(project)
        alive = bool(st and st.proc and st.proc.poll() is None)
        open_ = _port_open(port)
        phase = st.phase if st else PHASE_STOPPED
        if phase == PHASE_RUNNING and not (alive or open_):
            phase = PHASE_STOPPED
        elif not st and open_:
            phase = PHASE_RUNNING
        return {
            "project": project,
            "port": port,
            "phase": phase,
            "message": (st.message if st else ""),
            "kind": (st.kind if st else ""),
            "command": (st.cmd if st else ""),
            "pid": (st.proc.pid if alive else None),
            "uptime": int(time.time() - st.started_at) if (st and alive and st.started_at) else 0,
            "port_open": open_,
            "log": str(self._log_path(project)),
        }

    def logs(self, project: str, max_bytes: int = LOG_TAIL_BYTES) -> str:
        p = self._log_path(project)
        if not p.is_file():
            return ""
        size = p.stat().st_size
        with open(p, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
        return data.decode("utf-8", "replace")
