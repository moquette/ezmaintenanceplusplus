"""Coverage for onetap.py's TWO-LAYER wipe (the tvOS clean-clone fix).

On Apple TV Kodi vectors userdata *.xml into NSUserDefaults, reads the KEY FIRST (a key
SHADOWS the disk file) and never copies a key back to disk. onetap._wipe used to be a
pure POSIX os.walk + os.remove, so a "clean clone" wipe on tvOS left every key alive to
shadow whatever the subsequent restore wrote. The fix makes the wipe clear BOTH layers on
tvOS (and ONLY tvOS, hard-gated): the POSIX file via os.remove AND the NSUserDefaults key
via xbmcvfs.delete on the special:// path.

The harness models both layers for real, like tests/fake_kodi_sandbox_io.py does:
  * the POSIX layer is a real tree under tmp_path in the tvOS home shape
    (<sandbox>/Library/Caches/Kodi), so os.walk/os.remove run for real;
  * the key layer is a REAL binary plist at <sandbox>/Library/Preferences/, the exact
    store nsub._find_nsud_plist enumerates, so the wipe's key enumeration and its
    re-read verification run through the shipped nsub code path.
The fake xbmcvfs.delete models tvOS CTVOSFile::Delete exactly (kodi-storage-map bug 4):
it drops ONLY the plist key, NEVER touches the POSIX file, and returns True whether or
not a key existed - so a test passes only if the code never trusts that boolean.
"""

from __future__ import annotations

import importlib
import plistlib
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"

_KEY_PREFIX = "/userdata/"
_SPECIAL_PREFIX = "special://home/userdata/"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Import the REAL onetap.py (as its package module, so its lazy
    `from resources.lib.modules import nsub` resolves to the real nsub) under faked
    xbmc/xbmcaddon/xbmcgui/xbmcvfs, over a real two-layer store."""
    sandbox = tmp_path / "sandbox" / "Library"
    home = sandbox / "Caches" / "Kodi"
    prefs = sandbox / "Preferences"
    plist_path = prefs / "com.test.kodi.plist"
    home.mkdir(parents=True)

    logs: list[str] = []
    deleted: list[str] = []  # every xbmcvfs.delete() call, in order
    state = {"fail_keys": set()}  # rels whose key "delete" silently does nothing

    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 1
    xbmc.LOGERROR = 3
    xbmc.log = lambda msg, level=1: logs.append(msg)
    # Default platform answer: NOT tvOS (the real Fire TV / desktop shape: the condition
    # exists and returns False). Tests flip it per-case.
    xbmc.getCondVisibility = lambda cond: False

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _FakeAddon:
        def getAddonInfo(self, key):
            return {
                "id": "script.ezmaintenanceplusplus",
                "name": "EZ Maintenance++",
            }.get(key, "")

        def getSetting(self, key):
            return ""

        def setSetting(self, key, value):
            pass

    xbmcaddon.Addon = _FakeAddon

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.Dialog = lambda: types.SimpleNamespace()
    xbmcgui.DialogProgress = lambda: types.SimpleNamespace()

    def _plist_load():
        if not plist_path.exists():
            return {}
        with open(plist_path, "rb") as fh:
            return plistlib.load(fh)

    def _plist_dump(data):
        prefs.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "wb") as fh:
            plistlib.dump(data, fh, fmt=plistlib.FMT_BINARY)

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p.replace(
        "special://home/", str(home) + "/"
    ).replace("special://home", str(home))

    def _delete(p):
        """tvOS CTVOSFile::Delete semantics: drop ONLY the NSUserDefaults key (persisted
        to the plist, as [defaults synchronize] does), never the POSIX file, and return
        True whether or not a key existed (TVOSNSUserDefaults.mm:188-202)."""
        deleted.append(p)
        if p.startswith(_SPECIAL_PREFIX):
            rel = p[len(_SPECIAL_PREFIX) :]
            if rel not in state["fail_keys"]:
                data = _plist_load()
                data.pop(_KEY_PREFIX + rel, None)
                _plist_dump(data)
        return True  # ALWAYS True - the boolean must never be trusted

    xbmcvfs.delete = _delete

    for name, mod in (
        ("xbmc", xbmc),
        ("xbmcaddon", xbmcaddon),
        ("xbmcgui", xbmcgui),
        ("xbmcvfs", xbmcvfs),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    onetap = importlib.import_module("resources.lib.modules.onetap")

    def set_keys(rel_map, extra=None):
        """Write the fake NSUserDefaults plist: {rel: content} plus Kodi's
        UserdataMigrated bookkeeping key (never a /userdata/ key)."""
        data = {"UserdataMigrated": True}
        for rel, content in rel_map.items():
            data[_KEY_PREFIX + rel] = content
        if extra:
            data.update(extra)
        _plist_dump(data)

    def keys():
        return {
            k[len(_KEY_PREFIX) :]
            for k in _plist_load()
            if isinstance(k, str) and k.startswith(_KEY_PREFIX)
        }

    def raw_keys():
        return set(_plist_load())

    return types.SimpleNamespace(
        onetap=onetap,
        xbmc=xbmc,
        xbmcvfs=xbmcvfs,
        home=home,
        plist_path=plist_path,
        set_keys=set_keys,
        keys=keys,
        raw_keys=raw_keys,
        deleted=deleted,
        state=state,
        logs=logs,
    )


def _tvos(monkeypatch, env):
    """Make the faked xbmc report Apple TV, as onetap._is_tvos() checks."""
    monkeypatch.setattr(env.xbmc, "getCondVisibility", lambda cond: "TVOS" in cond)


def _w(base: Path, rel: str, content: bytes = b"<x/>") -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


# --------------------------------------------------------------------------- #
# The core fix: on tvOS the wipe clears BOTH layers, including key-ONLY files
# (after nsud's confirmed-vector-then-drop-POSIX, many userdata files exist only
# as keys and are invisible to the POSIX walk).
# --------------------------------------------------------------------------- #
def test_tvos_wipe_clears_both_layers(env, monkeypatch):
    _tvos(monkeypatch, env)
    _w(env.home, "userdata/keyboard.xml")  # exists in BOTH layers
    _w(env.home, "addons/plugin.video.x/addon.xml")  # POSIX only
    env.set_keys(
        {
            "guisettings.xml": b"<gui/>",  # key ONLY - no POSIX twin at all
            "keyboard.xml": b"<k/>",
        }
    )

    files_removed, keys_removed, failed = env.onetap._wipe(
        str(env.home), env.onetap._wipe_excludes()
    )

    assert files_removed == 2 and keys_removed == 2 and failed == 0
    assert not (env.home / "userdata" / "keyboard.xml").exists()
    assert env.keys() == set(), "every non-excluded /userdata key must be gone"
    # The key-only file was found via the plist (not the POSIX walk) and dropped
    # through xbmcvfs.delete on the special:// path.
    assert _SPECIAL_PREFIX + "guisettings.xml" in env.deleted
    # Kodi's bookkeeping key is not a /userdata/ key and is never touched.
    assert "UserdataMigrated" in env.raw_keys()


def test_tvos_key_delete_never_touches_posix_and_vice_versa(env, monkeypatch):
    # The two mechanisms are paired because each one alone is a half-wipe:
    # os.remove drops only the file, xbmcvfs.delete drops only the key.
    _tvos(monkeypatch, env)
    _w(env.home, "userdata/sources.xml", b"<s/>")
    env.set_keys({"sources.xml": b"<s/>"})

    env.onetap._wipe(str(env.home), env.onetap._wipe_excludes())

    assert not (env.home / "userdata" / "sources.xml").exists()
    assert "sources.xml" not in env.keys()


# --------------------------------------------------------------------------- #
# Exclusions: the key layer honors the SAME exclusions as the POSIX walk (temp,
# this add-on's own addon_data, the requests deps, Addons*.db keep_files).
# --------------------------------------------------------------------------- #
def test_tvos_key_wipe_respects_wipe_excludes(env, monkeypatch):
    _tvos(monkeypatch, env)
    own = "addon_data/script.ezmaintenanceplusplus/settings.xml"
    dep = "addon_data/script.module.requests/settings.xml"
    _w(env.home, "userdata/" + own)
    _w(env.home, "temp/staged_backup.zip")
    _w(env.home, "userdata/sources.xml")
    env.set_keys({own: b"<a/>", dep: b"<b/>", "guisettings.xml": b"<c/>"})

    files_removed, keys_removed, failed = env.onetap._wipe(
        str(env.home), env.onetap._wipe_excludes()
    )

    # Excluded keys survive; only the non-excluded one is dropped.
    assert env.keys() == {own, dep}
    assert keys_removed == 1 and failed == 0
    # And no delete call was even issued for an excluded key.
    assert env.deleted == [_SPECIAL_PREFIX + "guisettings.xml"]
    # The POSIX exclusions still hold too (same excludes, both layers).
    assert (env.home / "userdata" / own).exists()
    assert (env.home / "temp" / "staged_backup.zip").exists()
    assert not (env.home / "userdata" / "sources.xml").exists()
    assert files_removed == 1


def test_tvos_key_wipe_respects_keep_files(env, monkeypatch):
    # Addons*.db is preserved via keep_files (never actually vectored - non-xml is
    # never NSUserDefaults-eligible - but the key layer guards it anyway).
    _tvos(monkeypatch, env)
    db_rel = "userdata/Database/Addons33.db"
    _w(env.home, db_rel, b"sqlite")
    env.set_keys({"Database/Addons33.db": b"sqlite", "guisettings.xml": b"<g/>"})
    keep = {str(env.home / "userdata" / "Database" / "Addons33.db")}

    files_removed, keys_removed, failed = env.onetap._wipe(
        str(env.home), env.onetap._wipe_excludes(), keep_files=keep
    )

    assert (env.home / db_rel).exists()
    assert env.keys() == {"Database/Addons33.db"}
    assert files_removed == 0 and keys_removed == 1 and failed == 0


# --------------------------------------------------------------------------- #
# The hard gate: on Fire TV / desktop the key layer is a strict no-op - the
# store is never even consulted and xbmcvfs.delete is never called.
# --------------------------------------------------------------------------- #
def test_firetv_wipe_is_noop_on_key_layer(env, monkeypatch):
    # Default fake: getCondVisibility exists and returns False (the real production
    # answer on the dangerous platforms).
    consulted = []
    real_store = env.onetap._nsud_plist_store
    monkeypatch.setattr(
        env.onetap,
        "_nsud_plist_store",
        lambda: consulted.append(1) or real_store(),
    )
    _w(env.home, "userdata/guisettings.xml")
    env.set_keys({"guisettings.xml": b"<g/>"})  # a store, even though non-tvOS

    files_removed, keys_removed, failed = env.onetap._wipe(
        str(env.home), env.onetap._wipe_excludes()
    )

    assert files_removed == 1 and keys_removed == 0 and failed == 0
    assert env.deleted == [], "no key delete may ever be issued off-tvOS"
    assert consulted == [], "the NSUserDefaults store must not even be consulted"
    assert env.keys() == {"guisettings.xml"}, "the store must be untouched"


def test_is_tvos_defaults_false_on_error(env, monkeypatch):
    def _boom(cond):
        raise RuntimeError("no such infolabel")

    monkeypatch.setattr(env.xbmc, "getCondVisibility", _boom)
    assert env.onetap._is_tvos() is False


def test_is_tvos_queries_exact_condition_string(env, monkeypatch):
    # Pin the condition string so a typo (which real Kodi answers False = the fix
    # silently no-ops) is caught here instead of shipping.
    seen = []
    monkeypatch.setattr(
        env.xbmc, "getCondVisibility", lambda cond: seen.append(cond) or True
    )
    assert env.onetap._is_tvos() is True
    assert seen == ["System.Platform.TVOS"]


# --------------------------------------------------------------------------- #
# Failures are never silent: xbmcvfs.delete's boolean is untrustworthy (always
# True), so survival is verified by re-reading the plist; every surviving key is
# counted as a failure and logged by name, and the summary counts are logged and
# returned.
# --------------------------------------------------------------------------- #
def test_surviving_key_is_counted_as_failure_and_logged(env, monkeypatch):
    _tvos(monkeypatch, env)
    env.set_keys({"guisettings.xml": b"<g/>", "sources.xml": b"<s/>"})
    # delete() returns True for sources.xml but the key survives (the exact failure
    # shape the always-True boolean would hide).
    env.state["fail_keys"] = {"sources.xml"}

    files_removed, keys_removed, failed = env.onetap._wipe(
        str(env.home), env.onetap._wipe_excludes()
    )

    assert keys_removed == 1 and failed == 1
    assert env.keys() == {"sources.xml"}
    assert any("SURVIVED" in m and "sources.xml" in m for m in env.logs), (
        "a surviving (restore-shadowing) key must be named in the log"
    )


def test_posix_remove_failure_is_counted_and_logged(env, monkeypatch):
    import os

    _w(env.home, "userdata/locked.xml")
    _w(env.home, "userdata/free.xml")
    real_remove = os.remove

    def _remove(path, *a, **k):
        if str(path).endswith("locked.xml"):
            raise OSError("locked")
        return real_remove(path, *a, **k)

    monkeypatch.setattr("os.remove", _remove)

    files_removed, keys_removed, failed = env.onetap._wipe(
        str(env.home), env.onetap._wipe_excludes()
    )

    assert files_removed == 1 and failed == 1
    assert any("1 file failures" in m for m in env.logs)


def test_wipe_logs_summary_counts(env, monkeypatch):
    _tvos(monkeypatch, env)
    _w(env.home, "userdata/keyboard.xml")
    env.set_keys({"guisettings.xml": b"<g/>"})

    env.onetap._wipe(str(env.home), env.onetap._wipe_excludes())

    assert any(
        "1 files removed" in m and "1 NSUserDefaults keys removed" in m
        for m in env.logs
    )
