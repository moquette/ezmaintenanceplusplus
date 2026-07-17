# -*- coding: utf-8 -*-
"""boxsetup.add_media_sources: the repo source is named .T7B, and a box set up
under the old .tony.7.bones name is RENAMED in place (never duplicated) on the
next Set-up-this-box run (owner request 2026-07-17).
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ADDON_ROOT = HERE.parent / "script.ezmaintenanceplusplus"


@pytest.fixture
def boxsetup(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    sources_path = tmp_path / "sources.xml"

    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = xbmc.LOGERROR = xbmc.LOGWARNING = xbmc.LOGDEBUG = 0
    xbmc.log = lambda *a, **k: None
    xbmc.executeJSONRPC = lambda *a, **k: '{"result":true}'
    xbmc.executebuiltin = lambda *a, **k: None
    xbmc.translatePath = lambda p: str(sources_path)

    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = lambda *a, **k: types.SimpleNamespace(
        getAddonInfo=lambda k: "EZ Maintenance++", getSetting=lambda k: ""
    )

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = xbmc.translatePath

    class _File:
        def __init__(self, *_a, **_k):
            self._b = sources_path.read_bytes() if sources_path.exists() else b""

        def readBytes(self):
            return self._b

        def close(self):
            pass

    xbmcvfs.File = _File
    xbmcvfs.exists = lambda p: Path(p).exists()

    # Let the REAL resources.* package tree import from ADDON_ROOT (syspath), and
    # inject ONLY the leaf modules boxsetup lazily imports (nsud, ui) as fakes into
    # sys.modules so `from resources.lib.modules import nsud/ui` picks up the fakes.
    nsud = types.ModuleType("resources.lib.modules.nsud")
    nsud.persist_one = lambda *a, **k: None
    ui = types.ModuleType("resources.lib.modules.ui")
    ui.done = lambda *a, **k: None
    ui.error = lambda *a, **k: None

    class _Prog:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    ui.Progress = lambda *a, **k: _Prog()

    for name, mod in (
        ("xbmc", xbmc),
        ("xbmcaddon", xbmcaddon),
        ("xbmcvfs", xbmcvfs),
        ("resources.lib.modules.nsud", nsud),
        ("resources.lib.modules.ui", ui),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    mod = importlib.import_module("resources.lib.modules.boxsetup")
    mod = importlib.reload(mod)
    mod._test_sources_path = sources_path
    return mod


def _names_paths(sources_path):
    import xml.etree.ElementTree as ET

    root = ET.fromstring(sources_path.read_text())
    out = []
    for s in root.find("files").findall("source"):
        out.append(
            ((s.findtext("name") or "").strip(), (s.findtext("path") or "").strip())
        )
    return out


def test_fresh_box_gets_t7b_repo_source(boxsetup):
    assert boxsetup.REPO_SOURCE_NAME == ".T7B"
    assert boxsetup.add_media_sources(interactive=False) is True
    got = dict(_names_paths(boxsetup._test_sources_path))
    assert got[".T7B"] == "https://tony7bones.github.io/"
    assert ".tony.7.bones" not in got


def test_old_name_is_renamed_in_place_not_duplicated(boxsetup):
    boxsetup._test_sources_path.write_text(
        "<sources><files><default/>"
        "<source><name>.tony.7.bones</name>"
        '<path pathversion="1">https://tony7bones.github.io/</path>'
        "<allowsharing>true</allowsharing></source>"
        "</files></sources>"
    )
    assert boxsetup.add_media_sources(interactive=False) is True
    pairs = _names_paths(boxsetup._test_sources_path)
    repo = [p for p in pairs if p[1] == "https://tony7bones.github.io/"]
    assert len(repo) == 1, "the repo source must not be duplicated"
    assert repo[0][0] == ".T7B", "the old name must be renamed to .T7B"
    assert not any(n == ".tony.7.bones" for n, _ in pairs)


def test_second_run_is_a_noop(boxsetup):
    boxsetup.add_media_sources(interactive=False)
    before = boxsetup._test_sources_path.read_text()
    boxsetup.add_media_sources(interactive=False)
    after = boxsetup._test_sources_path.read_text()
    assert before == after, "an already-set-up box must not change on a re-run"


def test_existing_user_sources_are_preserved(boxsetup):
    boxsetup._test_sources_path.write_text(
        "<sources><files><default/>"
        "<source><name>MyMovies</name>"
        '<path pathversion="1">nfs://192.168.1.9/movies/</path>'
        "<allowsharing>true</allowsharing></source>"
        "<source><name>.tony.7.bones</name>"
        '<path pathversion="1">https://tony7bones.github.io/</path>'
        "<allowsharing>true</allowsharing></source>"
        "</files></sources>"
    )
    boxsetup.add_media_sources(interactive=False)
    pairs = _names_paths(boxsetup._test_sources_path)
    assert ("MyMovies", "nfs://192.168.1.9/movies/") in pairs
    assert (".T7B", "https://tony7bones.github.io/") in pairs


def test_migration_dedups_two_repo_sources_by_url(boxsetup):
    """audit Finding G: a box that carries BOTH the old .tony.7.bones and a .T7B on the
    SAME repo url must end with exactly ONE .T7B - the migration dedups by URL, not
    name, so it can never leave two entries pointing at the same repo."""
    boxsetup._test_sources_path.write_text(
        "<sources><files><default/>"
        "<source><name>.tony.7.bones</name>"
        '<path pathversion="1">https://tony7bones.github.io/</path>'
        "<allowsharing>true</allowsharing></source>"
        "<source><name>.T7B</name>"
        '<path pathversion="1">https://tony7bones.github.io/</path>'
        "<allowsharing>true</allowsharing></source>"
        "</files></sources>"
    )
    boxsetup.add_media_sources(interactive=False)
    pairs = _names_paths(boxsetup._test_sources_path)
    repo = [p for p in pairs if p[1] == "https://tony7bones.github.io/"]
    assert repo == [(".T7B", "https://tony7bones.github.io/")], (
        "exactly one .T7B repo source, no duplicate: %r" % pairs
    )
