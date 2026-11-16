"""Tests dédiés à l'intégration du provider 9Router (pid = "9router").

Couvre les 5 invariants structurels posés par les étapes 1→4 du chantier
`integration-provider-9router`, sans réseau ni MongoDB : on importe `server`
via sys.path pour inspecter les registres et la configuration construite.

Invariants vérifiés :
  1. Le pid "9router" est déclaré dans `Settings.free_providers` avec le
     préfixe d'environnement `ROUTER9_*` (contournement : un nom de variable
     shell/.env ne peut pas commencer par un chiffre).
  2. Le pid "9router" figure dans `FREE_PROVIDER_IDS` et `PROVIDER_IDS`.
  3. L'étiquette lisible "9Router" est enregistrée dans `PROVIDER_LABELS`.
  4. `_provider_available("9router")` est False sans clé, True dès que la
     clé ET la base URL sont présentes (règle par défaut de
     `_provider_available` : conjonction clé + URL).
  5. `env.example` documente le bloc `ROUTER9_*` complet (4 variables).
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "test-secret-9router")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_forge")

_ENV_KEYS = (
    "ROUTER9_API_KEY",
    "ROUTER9_BASE_URL",
    "ROUTER9_MODEL",
    "ROUTER9_TIMEOUT",
)


def _fresh_server():
    """Recharge le module server pour appliquer les env vars du test."""
    if "server" in sys.modules:
        del sys.modules["server"]
    return importlib.import_module("server")


def _clear_router9_env():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


def test_9router_declared_in_free_providers():
    _clear_router9_env()
    srv = _fresh_server()
    settings = srv.Settings()
    assert "9router" in settings.free_providers, \
        "pid '9router' absent de Settings.free_providers"
    conf = settings.free_providers["9router"]
    assert conf["label"] == "9Router"
    # base_url par défaut doit être normalisée avec /v1 (assume_v1=True).
    assert conf["base_url"].endswith("/v1"), conf["base_url"]
    assert conf["base_url"].startswith("http://localhost:20128"), conf["base_url"]


def test_9router_in_global_registries():
    srv = _fresh_server()
    assert "9router" in srv.FREE_PROVIDER_IDS, \
        "pid '9router' absent de FREE_PROVIDER_IDS"
    assert "9router" in srv.PROVIDER_IDS, \
        "pid '9router' absent de PROVIDER_IDS (dérivé de FREE_PROVIDER_IDS)"


def test_9router_label_registered():
    srv = _fresh_server()
    assert srv.PROVIDER_LABELS.get("9router") == "9Router", \
        f"label inattendu : {srv.PROVIDER_LABELS.get('9router')!r}"


def test_9router_availability_requires_key_and_url():
    # Sans clé : provider indisponible.
    _clear_router9_env()
    srv = _fresh_server()
    assert srv._provider_available("9router") is False, \
        "9router devrait être indisponible sans clé"

    # Avec clé + URL par défaut : provider disponible.
    os.environ["ROUTER9_API_KEY"] = "test-key-abc"
    srv = _fresh_server()
    assert srv._provider_available("9router") is True, \
        "9router devrait être disponible avec clé + base_url par défaut"
    _clear_router9_env()


def test_env_example_documents_router9_block():
    env_path = os.path.join(os.path.dirname(__file__), "..", "env.example")
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()
    for var in _ENV_KEYS:
        assert var in content, f"{var} absent de backend/env.example"
