# -*- coding: utf-8 -*-
"""restorecheck: the post-restore verification that proves state before anyone
reports it (born from the atv2 2026-07-17 round-trip, where a raw "9 items"
wipe warning read as breakage and manual triage proved every item harmless).

Covers the triage classes (overwritten / residue / shadow), the surviving-key
re-read, the duplicate-listing probe, and the composed verdict: attention is
ONLY ever proven danger; everything else is log detail.
"""

import importlib
import pathlib
import sys
import types

import pytest

ADDON_ROOT = (
    pathlib.Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"
)


@pytest.fixture()
def rc(monkeypatch):
    """Import restorecheck fresh with recording fakes for xbmc/xbmcvfs."""
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    logs = []
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 1
    xbmc.log = lambda msg, level=1: logs.append(msg)
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs._listings = {}
    xbmcvfs.listdir = lambda d: xbmcvfs._listings[d]
    monkeypatch.setitem(sys.modules, "xbmc", xbmc)
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)
    sys.modules.pop("resources.lib.modules.restorecheck", None)
    mod = importlib.import_module("resources.lib.modules.restorecheck")
    mod = importlib.reload(mod)
    mod._test_logs = logs
    mod._test_xbmcvfs = xbmcvfs
    yield mod
    sys.modules.pop("resources.lib.modules.restorecheck", None)


NAMES_HOME = [
    "userdata/guisettings.xml",
    "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml",
    "addons/plugin.x/addon.xml",
]


def test_archive_rels_home_and_userdata_anchors(rc):
    assert "userdata/guisettings.xml" in rc.archive_rels(NAMES_HOME, "home")
    # A userdata-anchored zip's bare members map under userdata/ in home terms.
    assert rc.archive_rels(["guisettings.xml"], "userdata") == {
        "userdata/guisettings.xml"
    }


def test_triage_classes(rc):
    leftovers = [
        ("file", "userdata/guisettings.xml"),  # in archive -> overwritten
        ("file", "userdata/Thumbnails/old.jpg"),  # not in archive -> residue
        ("key", "userdata/guisettings.xml"),  # in archive -> overwritten (rewrite)
        ("key", "userdata/keymaps/old.xml"),  # key not in archive -> shadow
    ]
    tri = rc.triage_leftovers(leftovers, NAMES_HOME, "home")
    assert tri["overwritten"] == [
        "userdata/guisettings.xml",
        "userdata/guisettings.xml",
    ]
    assert tri["residue"] == ["userdata/Thumbnails/old.jpg"]
    assert tri["shadow"] == ["userdata/keymaps/old.xml"]


def test_surviving_shadow_keys_reads_the_store(rc, monkeypatch):
    onetap = types.SimpleNamespace(
        _nsud_userdata_rels=lambda: ["keymaps/old.xml"]  # still in the store
    )
    monkeypatch.setitem(sys.modules, "resources.lib.modules.onetap", onetap)
    survivors = rc.surviving_shadow_keys(
        ["userdata/keymaps/old.xml", "userdata/gone.xml"]
    )
    assert survivors == ["userdata/keymaps/old.xml"]


def test_duplicate_listing_hits(rc):
    rc._test_xbmcvfs._listings = {d: (([], [])) for d in rc.DUPLICATE_PROBE_DIRS}
    rc._test_xbmcvfs._listings["special://profile/addon_data/pvr.iptvsimple/"] = (
        [],
        ["instance-settings-1.xml", "instance-settings-1.xml"],
    )
    hits = rc.duplicate_listing_hits()
    assert hits == [
        "special://profile/addon_data/pvr.iptvsimple/instance-settings-1.xml"
    ]


def test_verify_clean_state_returns_no_attention(rc, monkeypatch):
    rc._test_xbmcvfs._listings = {d: ([], []) for d in rc.DUPLICATE_PROBE_DIRS}
    monkeypatch.setitem(
        sys.modules,
        "resources.lib.modules.onetap",
        types.SimpleNamespace(_nsud_userdata_rels=lambda: []),
    )
    leftovers = [
        ("file", "userdata/guisettings.xml"),
        ("file", "userdata/Thumbnails/old.jpg"),
        ("key", "userdata/keymaps/old.xml"),  # purge cleared it (store is empty)
    ]
    attention, detail = rc.verify_restored_state(leftovers, NAMES_HOME, "home")
    assert attention == [], "harmless/auto-fixed leftovers must not alarm"
    assert any("auto-fixed" in d for d in detail)
    assert any("verified clean" in m for m in rc._test_logs)


def test_surviving_key_not_in_archive_is_cruft_not_attention(rc, monkeypatch):
    """A surviving stale key at a path the ARCHIVE does not carry shadows nothing
    restored - it is cruft, LOGGED not alarmed. Flagging it as attention cried
    wolf on a benign wipe leftover (atv2 2026-07-17 false 'needs attention')."""
    rc._test_xbmcvfs._listings = {d: ([], []) for d in rc.DUPLICATE_PROBE_DIRS}
    monkeypatch.setitem(
        sys.modules,
        "resources.lib.modules.onetap",
        types.SimpleNamespace(_nsud_userdata_rels=lambda: ["keymaps/old.xml"]),
    )
    attention, detail = rc.verify_restored_state(
        [("key", "userdata/keymaps/old.xml")], NAMES_HOME, "home"
    )
    assert attention == [], "a benign surviving key must NOT raise attention"
    assert any("keymaps/old.xml" in d and "cruft" in d for d in detail)


def test_duplicate_listing_is_the_real_attention_signal(rc, monkeypatch):
    """A restored file shadowed by a stale key (same path in both layers) shows
    as a duplicate two-layer listing - THAT is the genuine attention case."""
    rc._test_xbmcvfs._listings = {d: ([], []) for d in rc.DUPLICATE_PROBE_DIRS}
    rc._test_xbmcvfs._listings["special://profile/addon_data/pvr.iptvsimple/"] = (
        [],
        ["instance-settings-1.xml", "instance-settings-1.xml"],
    )
    monkeypatch.setitem(
        sys.modules,
        "resources.lib.modules.onetap",
        types.SimpleNamespace(_nsud_userdata_rels=lambda: []),
    )
    attention, _detail = rc.verify_restored_state([], NAMES_HOME, "home")
    assert len(attention) == 1 and "shadowed by a stale key" in attention[0]
    assert "instance-settings-1.xml" in attention[0]


def test_verify_never_raises_and_a_probe_hiccup_is_log_only(rc, monkeypatch):
    """A probe that raises must NOT break the restore AND must NOT alarm the user:
    a diagnostic that could not run is not a restore failure (the extract already
    decided that). It goes to detail (log), never attention."""

    def boom(*a, **k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(rc, "duplicate_listing_hits", boom)
    attention, detail = rc.verify_restored_state([], [], "home")
    assert attention == [], "a probe hiccup must not raise attention"
    assert any("could not complete" in d for d in detail)
