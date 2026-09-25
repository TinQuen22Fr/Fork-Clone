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
API_PORT_OFFSET = 100
WEB_DIRS = ("frontend", "client", "web", "ui")
API_DIRS = ("backend", "api", "server")


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
        self.task: Optional[asyncio.Task] = None


class PreviewManager:
    """Un process par cible, un port par cible, un journal par cible."""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self.state_dir = self.workspace_root / ".forge-preview"
        self._procs: dict[str, dict[str, PreviewProcess]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # -- utilitaires ------------------------------------------------------
    def _lock(self, project: str) -> asyncio.Lock:
        return self._locks.setdefault(project, asyncio.Lock())

    def _dirs(self) -> None:
        for d in (self.state_dir, self.state_dir / "home", self.state_dir / "npm-cache"):
            d.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project: str) -> Path:
        return self.workspace_root / project

    def log_path(self, project: str, target: str) -> Path:
        return self.state_dir / f"{project}.{target}.log"

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
        if extra:
            env.update({k: str(v) for k, v in extra.items()})
        return env

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
            plan.update({"target": "web", "port": port, "env": self._web_env()})
            targets.append(plan)

        if api_dir is not None:
            api_port = port + API_PORT_OFFSET
            plan = self._detect_api(api_dir, api_port)
            plan.update({"target": "api", "port": api_port})
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

    def _web_env(self) -> dict:
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
                            "--bind", "127.0.0.1", "--directory", str(target)],
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

        venv = d / ".venv"
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

        src = ""
        try:
            src = (d / entry).read_text(encoding="utf-8", errors="ignore")[:20000]
        except Exception:
            pass
        module = entry[:-3]
        if "FastAPI(" in src or "Flask(" in src:
            asgi = "FastAPI(" in src
            uvicorn_bin = str(venv / "bin" / "uvicorn")
            if asgi:
                cmd = [uvicorn_bin, f"{module}:app", "--host", "127.0.0.1", "--port", str(port)]
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
    async def start(self, project: str, port: int, restart: bool = False) -> dict:
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
            tracked = self._procs.setdefault(project, {})
            for tplan in plan["targets"]:
                target, tport = tplan["target"], tplan["port"]
                st = tracked.get(target)
                if st and st.proc and st.proc.poll() is None:
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
        cwd, port = plan["cwd"], plan["port"]
        env = self._env(port, plan.get("env"))
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
                    code = await self._spawn_wait(step, cwd, env, log, timeout=1800)
                    if code != 0:
                        st.phase = PHASE_ERROR
                        st.message = f"installation echouee (code {code}) — voir les logs"
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
            fh = open(log, "a", encoding="utf-8")
            st.proc = subprocess.Popen(
                plan["cmd"],
                cwd=cwd,
                env=self._env(port, plan.get("env")),
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            st.started_at = time.time()

            # Attente de l'ouverture du port (max 180 s : compilation possible).
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
                    logger.info(
                        "Preview %s/%s en ligne sur 127.0.0.1:%s", st.project, st.target, port
                    )
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
            logger.exception("Preview %s/%s : echec de demarrage", st.project, st.target)

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
            for st in list(self._procs.get(project, {}).values()):
                await self._kill(st)
            return self.status(project, port)

    async def stop_all(self) -> None:
        for project in list(self._procs):
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
            alive = bool(st and st.proc and st.proc.poll() is None)
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
                    "message": (st.message if st else tplan.get("hint", "")),
                    "kind": (st.kind if st and st.kind else tplan.get("kind", "")),
                    "command": (st.cmd if st and st.cmd else " ".join(tplan.get("cmd", []))),
                    "pid": (st.proc.pid if alive else None),
                    "uptime": int(time.time() - st.started_at) if (st and alive and st.started_at) else 0,
                    "port_open": open_,
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
            "message": "; ".join(msgs) or plan["hint"],
            "kind": " + ".join(f"{t['target']}:{t['kind']}" for t in targets if t["kind"]),
            "command": (web or {}).get("command", ""),
            "pid": (web or {}).get("pid"),
            "uptime": (web or {}).get("uptime", 0),
            "port_open": (web or {}).get("port_open", False),
            "targets": targets,
            "log": str(self.log_path(project, (web or {}).get("target", "app"))),
        }

    def logs(self, project: str, target: str = "", max_bytes: int = LOG_TAIL_BYTES) -> str:
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
                    return fh.read().decode("utf-8", "replace")
        return ""
