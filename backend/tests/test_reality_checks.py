"""Brique G — tests autonomes des reality checks.

Sans réseau, sans MongoDB : on crée des scripts `checks/NN_*_test.py` factices
dans un répertoire temporaire et on vérifie la découverte, le parsing du
verdict et l'exécution bornée.
"""

import asyncio
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import reality_checks as rc  # noqa: E402


def _write(d: Path, name: str, body: str) -> None:
    (d / "checks").mkdir(exist_ok=True)
    (d / "checks" / name).write_text(textwrap.dedent(body), encoding="utf-8")


def test_discover_sorted_and_filtered(tmp_path):
    _write(tmp_path, "02_b_test.py", "print('PASS')\n")
    _write(tmp_path, "01_a_test.py", "print('PASS')\n")
    # Non conformes : ignorés.
    _write(tmp_path, "zz_not_matching.py", "print('PASS')\n")
    _write(tmp_path, "1_too_short_test.py", "print('PASS')\n")
    found = rc.discover_checks(tmp_path)
    assert [p.name for p in found] == ["01_a_test.py", "02_b_test.py"]


def test_discover_no_checks_dir(tmp_path):
    assert rc.discover_checks(tmp_path) == []


def test_parse_verdict_marker_pass():
    passed, hint = rc._parse_verdict("ligne\nPASS: tout bon", "", 0)
    assert passed is True
    assert hint == "tout bon"


def test_parse_verdict_marker_fail_hint():
    passed, hint = rc._parse_verdict("blabla\nFAIL: build casse", "", 1)
    assert passed is False
    assert "build casse" in hint


def test_parse_verdict_exitcode_fallback():
    # Pas de marqueur : code de sortie fait foi.
    passed, _ = rc._parse_verdict("juste du texte", "", 0)
    assert passed is True
    passed, hint = rc._parse_verdict("erreur quelconque", "boom", 2)
    assert passed is False
    assert hint  # hint repli non vide


def test_parse_verdict_marker_pass_but_nonzero_exit_fails():
    # Désaccord marqueur/exit : l'échec prime.
    passed, _ = rc._parse_verdict("PASS", "", 3)
    assert passed is False


def test_run_no_checks_returns_none(tmp_path):
    res = asyncio.run(rc.run_reality_checks(tmp_path, sys.executable))
    assert res is None


def test_run_none_project_returns_none():
    res = asyncio.run(rc.run_reality_checks(None, sys.executable))
    assert res is None


def test_run_all_pass(tmp_path):
    _write(tmp_path, "01_ok_test.py", "print('PASS: nickel')\n")
    _write(tmp_path, "02_ok_test.py", "import sys; sys.exit(0)\n")
    res = asyncio.run(rc.run_reality_checks(tmp_path, sys.executable))
    assert res is not None
    assert res["ok"] is True
    assert res["total"] == 2
    assert res["passed"] == 2
    assert res["failed"] == 0


def test_run_one_fail_sets_hint(tmp_path):
    _write(tmp_path, "01_ok_test.py", "print('PASS')\n")
    _write(
        tmp_path,
        "02_ko_test.py",
        "import sys\nprint('FAIL: lint casse, lance ruff')\nsys.exit(1)\n",
    )
    res = asyncio.run(rc.run_reality_checks(tmp_path, sys.executable))
    assert res["ok"] is False
    assert res["passed"] == 1
    assert res["failed"] == 1
    assert "02_ko_test.py" in res["hint"]
    assert "lint casse" in res["hint"]


def test_run_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "CHECK_TIMEOUT_S", 1)
    _write(tmp_path, "01_slow_test.py", "import time; time.sleep(5)\n")
    res = asyncio.run(rc.run_reality_checks(tmp_path, sys.executable))
    assert res["ok"] is False
    assert res["results"][0]["passed"] is False
    assert "timeout" in res["results"][0]["hint"]


def test_run_crashing_script(tmp_path):
    _write(tmp_path, "01_boom_test.py", "raise RuntimeError('kaboom')\n")
    res = asyncio.run(rc.run_reality_checks(tmp_path, sys.executable))
    assert res["ok"] is False
    assert res["failed"] == 1


def test_output_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "CHECK_OUTPUT_TRUNC", 50)
    _write(tmp_path, "01_big_test.py", "print('x' * 5000)\nprint('PASS')\n")
    res = asyncio.run(rc.run_reality_checks(tmp_path, sys.executable))
    assert len(res["results"][0]["output"]) <= 80  # 50 + suffixe tronque
