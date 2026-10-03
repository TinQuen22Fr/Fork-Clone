#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Unchained Forge — outil de recherche web autonome (DuckDuckGo).

Extrait autonome du module historique `code-edith-old/bin/tool/web_search.py`
adapte a l'architecture actuelle de The Forge (backend Python, httpx, sortie
texte compacte pour ne pas saturer la fenetre de contexte).

Strategie, sans aucune cle API :
  1. DuckDuckGo (via la lib `ddgs`, deja dans requirements.txt) ;
  2. Wikipedia (API publique, User-Agent identifiant l'application) en repli
     quand un moteur generaliste bloque les IP de datacenter (429/403).

Point de branchement : `_run_tool_raw("web_search", ...)` dans server.py.
Le resultat est formate en texte compact (titre / URL / extrait) afin de
minimiser le cout en tokens tout en restant exploitable par l'agent.

Ne depend de rien d'autre que la stdlib + `ddgs` + `httpx` (deja presents).
"""

from html import unescape
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("forge.tools.web_search")


_TAG_RE = re.compile(r"<[^>]+>")

# User-Agent obligatoire pour l'API Wikipedia (401 sinon).
_WIKI_UA = (
    "ClaudeUnchainedForge/1.0 "
    "(https://github.com/; auto-heberge)"
)

_WIKI_API = "https://{lang}.wikipedia.org/w/api.php"

# Borne de securite : 1..10 resultats (un volume superieur est du bruit).
MAX_RESULTS = 10


def _format_search_results(results: list[dict], query: str, source: str) -> str:
    """Formate une liste de resultats {title, href/url, body} en texte compact.

    Un seul saut de ligne entre chaque resultat, extrait limite a 300 car. :
    c'est volontairement dense pour economiser les tokens."
    """
    if not results:
        return f"Aucun resultat pour « {query} »."
    n = len(results)
    lignes = "resultat" if n == 1 else "resultats"
    lines = [f"{n} {lignes} pour « {query} » (via {source}) :"]
    for i, r in enumerate(results, 1):
        snippet = " ".join((r.get("body") or "").split())[:300]
        title = r.get("title") or "(sans titre)"
        href = r.get("href") or r.get("url") or ""
        lines.append(f"\n{i}. {title}\n   {href}\n   {snippet}")
    return "\n".join(lines)


def search_duckduckgo(query: str, max_results: int = 5) -> str:
    """Recherche DuckDuckGo via la lib officielle `ddgs`.

    Retourne la sortie formatee, ou une chaine vide si aucun resultat.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        logger.warning("ddgs non installe — repli Wikipedia.")
        return ""

    try:
        with DDGS(timeout=20) as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:  # noqa: BLE001
        logger.warning("DuckDuckGo indisponible (%s) — repli Wikipedia.", str(e)[:150])
        return ""
    if not results:
        return ""
    return _format_search_results(results, query, "DuckDuckGo")


def search_wikipedia(query: str, max_results: int = 5) -> str:
    """Recherche Wikipedia (repli sans cle quand un moteur generaliste bloque).

    Essaie le francais puis l'anglais, renvoie la premiere langue qui a des
    resultats. Utilise un User-Agent identifiant l'application (exige par
    Wikipedia pour eviter le 403).
    """
    for lang in ("fr", "en"):
        try:
            resp = httpx.get(
                _WIKI_API.format(lang=lang),
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": max_results,
                    "format": "json",
                },
                headers={"User-Agent": _WIKI_UA},
                timeout=20.0,
            )
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search") or []
        except Exception:  # noqa: BLE001
            continue
        if not hits:
            continue
        return _format_search_results(
            [
                {
                    "title": h.get("title", ""),
                    "href": (
                        f"https://{lang}.wikipedia.org/wiki/"
                        + (h.get("title") or "").replace(" ", "_")
                    ),
                    "body": _TAG_RE.sub("", unescape(h.get("snippet") or "")),
                }
                for h in hits
            ],
            query,
            f"Wikipedia ({lang})",
        )
    return ""


def web_search(query: str, max_results: int = 5) -> str:
    """Point d'entree unique : DuckDuckGo puis repli Wikipedia, sans cle.

    Retourne toujours une chaine exploitable (resultats formates ou message
    d'erreur explicite), jamais de `None` ni d'exception.
    """
    query = (query or "").strip()
    if not query:
        return "Erreur: requete vide."

    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(n, MAX_RESULTS))

    out = search_duckduckgo(query, n)
    if out:
        return out

    out = search_wikipedia(query, n)
    if out:
        return out

    return (
        f"Recherche web indisponible pour « {query} ». "
        "DuckDuckGo et Wikipedia ont refuse la requete depuis cette machine. "
        "Configure GOOGLE_CSE_KEY et GOOGLE_CSE_CX dans le fichier de config "
        "du backend pour une source fiable."
    )
