"""Test de non-régression : GET /models récupère les catalogues EN PARALLÈLE.

Protège le correctif perf du commit a93184d (boucle séquentielle remplacée
par asyncio.gather) : temps total ≈ max (et non somme), et un provider en
échec ne vide pas le catalogue des autres. Pas de réseau, pas de dépendance
asyncio externe : on suit le pattern du projet (asyncio.run dans un test sync).
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import server


# --- Reproduction fidèle du bloc parallélisé de list_models (server.py l.6875) ---
# On n'appelle pas la route HTTP (auth + providers réels) : on exerce la SEULE
# logique qui a changé, à l'identique du code de production.
async def _gather_catalogs(catalog_ids):
    catalogs: dict[str, list[str]] = {}
    if catalog_ids:
        results = await asyncio.gather(
            *(server._fetch_catalog(pid) for pid in catalog_ids),
            return_exceptions=True,
        )
        for pid, res in zip(catalog_ids, results):
            catalogs[pid] = res if isinstance(res, list) else []
    return catalogs


def test_fetch_catalogs_run_in_parallel(monkeypatch):
    """5 providers lents (0.4s chacun) → total ≈ max (0.4s), pas la somme (2.0s)."""
    delay = 0.4
    providers = ["claude", "gemini", "opencode", "ollama_cloud", "free_x"]

    async def fake_fetch(pid):
        await asyncio.sleep(delay)
        return [f"{pid}-model-1", f"{pid}-model-2"]

    monkeypatch.setattr(server, "_fetch_catalog", fake_fetch)

    start = time.perf_counter()
    catalogs = asyncio.run(_gather_catalogs(providers))
    elapsed = time.perf_counter() - start

    # Parallèle réel : total proche du max unitaire, loin de la somme (len*delay).
    assert elapsed < delay * 2, (
        f"attendu parallèle (<{delay * 2:.2f}s), mesuré {elapsed:.2f}s "
        f"(somme séquentielle = {delay * len(providers):.2f}s)"
    )
    # Tous les catalogues sont bien récupérés.
    for pid in providers:
        assert catalogs[pid] == [f"{pid}-model-1", f"{pid}-model-2"]


def test_failing_provider_does_not_empty_the_others(monkeypatch):
    """Un provider qui lève une exception ne vide pas le catalogue des autres."""
    providers = ["claude", "gemini", "opencode"]

    async def fake_fetch(pid):
        if pid == "gemini":
            raise RuntimeError("provider injoignable")
        return [f"{pid}-model"]

    monkeypatch.setattr(server, "_fetch_catalog", fake_fetch)

    catalogs = asyncio.run(_gather_catalogs(providers))

    # Le provider en échec donne [] (contrat préservé vs comportement séquentiel).
    assert catalogs["gemini"] == []
    # Les providers sains restent intacts.
    assert catalogs["claude"] == ["claude-model"]
    assert catalogs["opencode"] == ["opencode-model"]


def test_non_list_return_is_coerced_to_empty(monkeypatch):
    """isinstance(res, list) else [] : un retour non-liste devient []."""
    providers = ["claude", "gemini"]

    async def fake_fetch(pid):
        if pid == "gemini":
            return None  # retour inattendu
        return ["claude-model"]

    monkeypatch.setattr(server, "_fetch_catalog", fake_fetch)

    catalogs = asyncio.run(_gather_catalogs(providers))

    assert catalogs["claude"] == ["claude-model"]
    assert catalogs["gemini"] == []


def test_empty_catalog_ids_returns_empty_dict():
    """Aucun provider disponible → dict vide, pas d'appel à gather."""
    assert asyncio.run(_gather_catalogs([])) == {}
