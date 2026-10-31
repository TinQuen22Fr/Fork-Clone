"""Brique D - tests autonomes de la chaine d'actions declarative.

Sans reseau, sans MongoDB : on teste la classification d'etape en action typee
(`_classify_action`) et l'enrichissement de `_new_step`, ainsi que la garantie
que `MAX_TOOL_ITERS` reste un simple garde-fou (present, inchange dans son role).
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import server  # noqa: E402


def _step(intention="", tools=None):
    s = server._new_step(0, intention)
    if tools is not None:
        s["tools"] = tools
    return s


def test_new_step_porte_les_champs_action():
    s = server._new_step(3, "  bonjour  ")
    assert s["index"] == 3
    assert s["intention"] == "bonjour"
    assert s["status"] == "running"
    assert s["action"] == server.ACTION_CODING
    assert s["action_reason"] == ""
    assert s["tools"] == []


def test_coding_agent_intention_seule():
    action, reason = server._classify_action(_step(intention="Je corrige le bug"))
    assert action == server.ACTION_CODING
    assert reason


def test_coding_agent_intention_plus_outils():
    s = _step(intention="Je lance les tests", tools=[{"tool": "bash", "output": "ok"}])
    action, reason = server._classify_action(s)
    assert action == server.ACTION_CODING


def test_script_request_outils_sans_intention():
    s = _step(intention="", tools=[{"tool": "bash", "output": "setup"}])
    action, reason = server._classify_action(s)
    assert action == server.ACTION_SCRIPT
    assert reason


def test_followup_reponse_non_finalisee():
    s = _step(intention="\n\n⚠️ Reponse non finalisee (modele muet ou limite)")
    action, _ = server._classify_action(s)
    assert action == server.ACTION_FOLLOWUP


def test_followup_trop_d_etapes():
    s = _step(intention="⚠️ Trop d'etapes d'outils, reponse non finalisee.")
    action, _ = server._classify_action(s)
    assert action == server.ACTION_FOLLOWUP


def test_followup_actions_terminees():
    s = _step(intention="*Actions terminées.*")
    action, _ = server._classify_action(s)
    assert action == server.ACTION_FOLLOWUP


def test_followup_prime_sur_outils():
    # Meme avec des outils, un marqueur de relance classe en FollowUp.
    s = _step(intention="modele muet apres outil", tools=[{"tool": "bash", "output": "x"}])
    action, _ = server._classify_action(s)
    assert action == server.ACTION_FOLLOWUP


def test_etape_vide_reste_coding():
    action, _ = server._classify_action(_step())
    assert action == server.ACTION_CODING


def test_classify_ne_leve_jamais():
    # Entree degeneree : pas d'exception, repli CodingAgent.
    action, reason = server._classify_action({"intention": None, "tools": None})
    assert action == server.ACTION_CODING
    assert reason


def test_classify_entree_totalement_vide():
    action, _ = server._classify_action({})
    assert action == server.ACTION_CODING


def test_max_tool_iters_reste_garde_fou():
    # Brique D : le compteur existe toujours comme garde-fou de securite.
    assert isinstance(server.MAX_TOOL_ITERS, int)
    assert server.MAX_TOOL_ITERS > 0


def test_truncate_preserve_action():
    # La troncature de persistance ne doit pas perdre les champs d'action.
    s = _step(intention="x", tools=[{"tool": "bash", "output": "y"}])
    s["action"] = server.ACTION_SCRIPT
    s["action_reason"] = "plomberie"
    out = server._truncate_step_logs(s)
    assert out["action"] == server.ACTION_SCRIPT
    assert out["action_reason"] == "plomberie"
