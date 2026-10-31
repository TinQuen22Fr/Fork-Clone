"""Brique G — Reality checks automatiques.

Après une étape qui modifie du code, la Forge exécute automatiquement les
scripts de vérification autonomes fournis par le projet actif et rattache leur
résultat au tour. Convention (empruntée à `build-your-own-x`) :

- Les scripts vivent dans `<projet>/checks/` et se nomment `NN_*_test.py`
  (`NN` = deux chiffres d'ordre, ex. `01_build_test.py`).
- Chaque script est AUTONOME : il s'exécute avec l'interpréteur Python courant,
  sans argument, depuis la racine du projet.
- Il imprime son verdict sur la DERNIÈRE ligne non vide de sa sortie, au
  format `PASS` ou `FAIL` (insensible à la casse), optionnellement suivi d'un
  indice : `FAIL: build cassé, lancer npm run build`.
- Son code de sortie fait foi en priorité (0 = succès) ; le marqueur texte sert
  de repli et à extraire le *hint*.

Garanties : aucune exception ne remonte, tout est borné dans le temps, et la
sortie est tronquée pour ne pas saturer le flux ni la base.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

# Bornes de sécurité.
CHECK_TIMEOUT_S = 60          # par script
CHECKS_TOTAL_TIMEOUT_S = 180  # budget global pour l'ensemble des scripts
CHECK_OUTPUT_TRUNC = 4000     # caractères conservés par script

_CHECK_NAME_RE = re.compile(r"^\d{2}_.*_test\.py$")
_VERDICT_RE = re.compile(r"^\s*(PASS|FAIL)\b[:\-\s]*(.*)$", re.IGNORECASE)


def discover_checks(project_dir: Path) -> list[Path]:
    """Liste triée des scripts de check d'un projet (`checks/NN_*_test.py`)."""
    try:
        checks_dir = Path(project_dir) / "checks"
        if not checks_dir.is_dir():
            return []
        found = [
            p for p in checks_dir.iterdir()
            if p.is_file() and _CHECK_NAME_RE.match(p.name)
        ]
        return sorted(found, key=lambda p: p.name)
    except Exception:  # noqa: BLE001
        return []


def _parse_verdict(stdout: str, stderr: str, returncode: int) -> tuple[bool, str]:
    """Déduit (passed, hint) d'une exécution.

    Priorité au code de sortie ; le marqueur texte sert de repli et fournit le
    hint. Un script sans marqueur mais code 0 est considéré comme PASS.
    """
    text = (stdout or "") + "\n" + (stderr or "")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    marker_passed: Optional[bool] = None
    hint = ""
    for ln in reversed(lines):
        m = _VERDICT_RE.match(ln)
        if m:
            marker_passed = m.group(1).upper() == "PASS"
            hint = m.group(2).strip()
            break
    if marker_passed is None:
        # Pas de marqueur : le code de sortie tranche.
        passed = returncode == 0
        if not passed and lines:
            hint = lines[-1].strip()
        return passed, hint[:300]
    # Marqueur présent : il doit être cohérent avec le code de sortie. En cas de
    # désaccord, l'échec prime (sécurité).
    passed = marker_passed and returncode == 0
    if not hint and not passed and lines:
        hint = lines[-1].strip()
    return passed, hint[:300]


async def _run_one(script: Path, project_dir: Path, python_bin: str) -> dict:
    """Exécute un script de check. Ne lève jamais."""
    name = script.name
    try:
        proc = await asyncio.create_subprocess_exec(
            python_bin, str(script),
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(), timeout=CHECK_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return {
                "name": name,
                "passed": False,
                "hint": f"timeout apres {CHECK_TIMEOUT_S}s",
                "returncode": None,
                "output": "",
            }
        out = (out_b or b"").decode("utf-8", "replace")
        err = (err_b or b"").decode("utf-8", "replace")
        passed, hint = _parse_verdict(out, err, proc.returncode)
        combined = out
        if err.strip():
            combined += ("\n--- stderr ---\n" + err)
        if len(combined) > CHECK_OUTPUT_TRUNC:
            combined = combined[:CHECK_OUTPUT_TRUNC] + "\n... [tronque]"
        return {
            "name": name,
            "passed": passed,
            "hint": hint,
            "returncode": proc.returncode,
            "output": combined,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "passed": False,
            "hint": f"execution impossible: {type(exc).__name__}",
            "returncode": None,
            "output": "",
        }


async def run_reality_checks(
    project_dir: Optional[Path],
    python_bin: str = "python3",
) -> Optional[dict]:
    """Exécute tous les checks du projet et agrège le résultat.

    Retourne `None` si aucun check n'existe (projet sans `checks/`). Sinon un
    dict : {ok, total, passed, failed, results:[...], hint}. Ne lève jamais.
    """
    try:
        if project_dir is None:
            return None
        project_dir = Path(project_dir)
        scripts = discover_checks(project_dir)
        if not scripts:
            return None

        async def _run_all() -> list[dict]:
            results = []
            for s in scripts:
                results.append(await _run_one(s, project_dir, python_bin))
            return results

        try:
            results = await asyncio.wait_for(
                _run_all(), timeout=CHECKS_TOTAL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "total": len(scripts),
                "passed": 0,
                "failed": len(scripts),
                "results": [],
                "hint": f"budget global depasse ({CHECKS_TOTAL_TIMEOUT_S}s)",
            }

        passed = sum(1 for r in results if r.get("passed"))
        failed = len(results) - passed
        ok = failed == 0
        first_fail = next((r for r in results if not r.get("passed")), None)
        hint = ""
        if first_fail:
            hint = f"{first_fail['name']}: {first_fail.get('hint') or 'echec'}"
        return {
            "ok": ok,
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "results": results,
            "hint": hint,
        }
    except Exception:  # noqa: BLE001
        # Un reality check ne doit jamais casser un tour.
        return {
            "ok": True,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "results": [],
            "hint": "reality checks indisponibles",
        }
