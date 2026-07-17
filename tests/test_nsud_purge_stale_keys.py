"""Coverage for nsud.purge_stale_keys (the vector-everything-era stale-key cleanup).

EZM++ 2026.07.08.2 through 2026.07.13.x vectored EVERY userdata *.xml into
NSUserDefaults on Apple TV. `_should_vector` was later scoped down, but the stale keys
for the now-out-of-scope files still sit in NSUserDefaults and SHADOW the POSIX files
(CTVOSFile reads the key FIRST), so a restored or hand-copied file silently never
applies. purge_stale_keys drops exactly those keys, and never anything else.

The fakes model the real tvOS mechanics these tests exist to pin:
* the NSUserDefaults backing store is a REAL plist file on disk (built with plistlib,
  gzip values, plus the 'UserdataMigrated' bookkeeping key), resolved via
  special://home/../../Preferences exactly as on hardware;
* xbmcvfs.delete on a /userdata/*.xml special path drops ONLY the plist key and leaves
  the POSIX file alone, and returns True whether or not a key existed (the storage-map
  bug-4 lying boolean) - a 'stuck' key models a delete that silently did nothing.

nsud imports only os/gzip (real) + xbmc/xbmcvfs (faked here), so the real module is
exercised in isolation.
"""

from __future__ import annotations

import gzip
import importlib
import plistlib
import sys
import types
from pathlib import Path

import pytest

ADDON_MODULES = (
    Path(__file__).parent.parent
    / "script.ezmaintenanceplusplus"
    / "resources"
    / "lib"
    / "modules"
)

KEY_PREFIX = "/userdata/"
SPECIAL_PREFIX = "special://home/userdata/"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A tvOS-shaped sandbox: special://home under Library/Caches/Kodi, a REAL plist in
    Library/Preferences, and a fake xbmcvfs whose delete() behaves like CTVOSFile::Delete
    (drops only the key; the lying True return). tvOS is OFF by default - each test that
    needs Apple TV calls env.enable_tvos()."""
    home = tmp_path / "Library" / "Caches" / "Kodi"
    userdata = home / "userdata"
    userdata.mkdir(parents=True)
    prefs = tmp_path / "Library" / "Preferences"
    prefs.mkdir(parents=True)
    plist_path = prefs / "org.xbmc.kodi-tvos.plist"

    deleted: list[str] = []  # every special path handed to xbmcvfs.delete
    state: dict = {"stuck_keys": set()}  # keys delete() silently fails to remove

    xbmc = types.ModuleType("xbmc")
    xbmc.log = lambda *a, **k: None

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: str(home) if p == "special://home" else p

    def _delete(special):
        # Model CTVOSFile::Delete for a WantsFile-eligible path: DeleteKeyFromPath
        # removes only the NSUserDefaults key, never the POSIX file, and the call
        # returns True whether or not a key existed (TVOSNSUserDefaults.mm:188-202).
        deleted.append(special)
        assert special.startswith(SPECIAL_PREFIX), (
            "purge must only ever delete special://home/userdata/ paths, got %r"
            % special
        )
        key = KEY_PREFIX + special[len(SPECIAL_PREFIX) :]
        if plist_path.exists():
            data = plistlib.loads(plist_path.read_bytes())
            if key in data and key not in state["stuck_keys"]:
                del data[key]
                plist_path.write_bytes(plistlib.dumps(data))
        return True

    xbmcvfs.delete = _delete

    monkeypatch.setitem(sys.modules, "xbmc", xbmc)
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)
    monkeypatch.syspath_prepend(str(ADDON_MODULES))
    monkeypatch.delitem(sys.modules, "nsud", raising=False)
    mod = importlib.import_module("nsud")

    def seed_plist(keys: dict[str, bytes], gzip_values: bool = True):
        data: dict = {"UserdataMigrated": True}
        for k, v in keys.items():
            data[k] = gzip.compress(v) if gzip_values else v
        plist_path.write_bytes(plistlib.dumps(data))

    def plist_keys():
        return set(plistlib.loads(plist_path.read_bytes()))

    def enable_tvos():
        monkeypatch.setattr(
            xbmc, "getCondVisibility", lambda cond: "TVOS" in cond, raising=False
        )

    return types.SimpleNamespace(
        mod=mod,
        userdata=userdata,
        plist_path=plist_path,
        seed_plist=seed_plist,
        plist_keys=plist_keys,
        enable_tvos=enable_tvos,
        deleted=deleted,
        state=state,
    )


def _write(base: Path, rel: str, content: bytes = b"<x/>") -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


# --------------------------------------------------------------------------- #
# Out-of-scope key WITH a disk twin: the key (the shadow) goes, the disk file stays.
# --------------------------------------------------------------------------- #
def test_out_of_scope_key_with_disk_twin_is_purged_disk_kept(env):
    env.enable_tvos()
    rel = "addon_data/script.skinshortcuts/mainmenu.DATA.xml"
    env.seed_plist({KEY_PREFIX + rel: b"<stale>old menu</stale>"})
    _write(env.userdata, rel, b"<fresh>restored menu</fresh>")

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 1, 0, 0)
    assert KEY_PREFIX + rel not in env.plist_keys(), "the shadowing key must be gone"
    assert (env.userdata / rel).read_bytes() == b"<fresh>restored menu</fresh>", (
        "the POSIX file must be untouched - only the key is purged"
    )
    assert env.deleted == [SPECIAL_PREFIX + rel]


# --------------------------------------------------------------------------- #
# Key-only file: the key may be the ONLY copy - materialize to disk, THEN purge.
# --------------------------------------------------------------------------- #
def test_key_only_file_is_materialized_then_purged(env):
    env.enable_tvos()
    rel = "addon_data/script.skinshortcuts/menu.DATA.xml"
    content = b"<menu>the owner's customized main menu</menu>"
    env.seed_plist({KEY_PREFIX + rel: content})  # gzipped, as Kodi stores it

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (1, 1, 0, 0)
    assert (env.userdata / rel).read_bytes() == content, (
        "the key's decoded content must land on disk BEFORE the key is dropped - "
        "the purge never destroys the only copy"
    )
    assert KEY_PREFIX + rel not in env.plist_keys()


def test_key_only_raw_uncompressed_value_also_materializes(env):
    env.enable_tvos()
    rel = "addon_data/plugin.foo/private.xml"
    env.seed_plist({KEY_PREFIX + rel: b"<small/>"}, gzip_values=False)

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (1, 1, 0, 0)
    assert (env.userdata / rel).read_bytes() == b"<small/>"


def test_key_only_undecodable_value_keeps_the_key_and_counts_failed(env):
    env.enable_tvos()
    rel = "addon_data/plugin.foo/private.xml"
    env.seed_plist({KEY_PREFIX + rel: b""}, gzip_values=False)  # empty = undecodable

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 0, 0, 1)
    assert KEY_PREFIX + rel in env.plist_keys(), (
        "a key that cannot be materialized is the only copy - it must be KEPT"
    )
    assert env.deleted == []


# --------------------------------------------------------------------------- #
# In-scope keys: on tvOS the key IS the live store - NEVER purged.
# --------------------------------------------------------------------------- #
def test_in_scope_keys_are_kept(env):
    env.enable_tvos()
    in_scope = (
        "guisettings.xml",  # top-level userdata xml
        "keymaps/custom.xml",  # non-addon_data nested xml: Kodi reads it via VFS
        "addon_data/pvr.iptvsimple/settings.xml",
        "addon_data/pvr.iptvsimple/instance-settings-1.xml",
    )
    env.seed_plist({KEY_PREFIX + rel: b"<live/>" for rel in in_scope})

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 0, len(in_scope), 0)
    for rel in in_scope:
        assert KEY_PREFIX + rel in env.plist_keys(), "%s must survive" % rel
        assert not (env.userdata / rel).exists(), (
            "an in-scope key is the live store - nothing to materialize"
        )
    assert env.deleted == [], "no xbmcvfs.delete call may be made for in-scope keys"


def test_mixed_store_purges_only_the_out_of_scope_keys(env):
    env.enable_tvos()
    stale = "addon_data/script.skinshortcuts/menu.DATA.xml"
    live = "guisettings.xml"
    env.seed_plist({KEY_PREFIX + stale: b"<stale/>", KEY_PREFIX + live: b"<live/>"})
    _write(env.userdata, stale, b"<fresh/>")

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 1, 1, 0)
    assert KEY_PREFIX + live in env.plist_keys()
    assert KEY_PREFIX + stale not in env.plist_keys()


# --------------------------------------------------------------------------- #
# Android / Fire TV / desktop: a strict no-op (the hard platform gate).
# --------------------------------------------------------------------------- #
def test_android_is_a_strict_noop(env):
    # Default fake xbmc has no getCondVisibility -> _is_tvos() False (the safe answer).
    rel = "addon_data/script.skinshortcuts/menu.DATA.xml"
    env.seed_plist({KEY_PREFIX + rel: b"<stale/>"})

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 0, 0, 0)
    assert KEY_PREFIX + rel in env.plist_keys(), "the plist must be untouched"
    assert env.deleted == []
    assert not (env.userdata / rel).exists(), "nothing may be materialized off-tvOS"


def test_non_tvos_getcondvis_false_is_a_strict_noop(env, monkeypatch):
    # Real Fire TV / desktop: the condition EXISTS and answers False.
    monkeypatch.setattr(
        sys.modules["xbmc"], "getCondVisibility", lambda cond: False, raising=False
    )
    env.seed_plist({KEY_PREFIX + "addon_data/foo/private.xml": b"<stale/>"})

    assert env.mod.purge_stale_keys(str(env.userdata)) == (0, 0, 0, 0)
    assert env.deleted == []


# --------------------------------------------------------------------------- #
# The hard delete guards: paths WantsFile excludes must never reach xbmcvfs.delete,
# because there the delete dispatches to CPosixFile and removes the REAL disk file.
# --------------------------------------------------------------------------- #
def test_siriremote_key_is_never_deleted_and_posix_file_untouched(env):
    env.enable_tvos()
    rel = "keymaps/customcontroller.SiriRemote.xml"
    env.seed_plist({KEY_PREFIX + rel: b"<key-anomaly/>"})
    _write(env.userdata, rel, b"<the only real copy/>")

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 0, 1, 0)
    assert env.deleted == [], (
        "xbmcvfs.delete on a SiriRemote path would POSIX-delete the only copy"
    )
    assert (env.userdata / rel).read_bytes() == b"<the only real copy/>"
    assert KEY_PREFIX + rel in env.plist_keys()


def test_non_xml_key_is_never_deleted(env):
    env.enable_tvos()
    rel = "Thumbnails/oddball.jpg"  # cannot exist as a real key; hard guard anyway
    env.seed_plist({KEY_PREFIX + rel: b"jpegbytes"})
    _write(env.userdata, rel, b"jpegbytes")

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 0, 1, 0)
    assert env.deleted == []
    assert (env.userdata / rel).exists()


# --------------------------------------------------------------------------- #
# The lying boolean: a delete that silently did nothing must count as failed,
# never as purged (confirmation comes from re-reading the plist, not the return).
# --------------------------------------------------------------------------- #
def test_stuck_key_counts_failed_and_disk_twin_is_kept(env):
    env.enable_tvos()
    rel = "addon_data/script.skinshortcuts/menu.DATA.xml"
    key = KEY_PREFIX + rel
    env.seed_plist({key: b"<stale/>"})
    _write(env.userdata, rel, b"<fresh/>")
    env.state["stuck_keys"] = {key}  # delete() returns True but removes nothing

    result = env.mod.purge_stale_keys(str(env.userdata))

    assert result == (0, 0, 0, 1)
    assert key in env.plist_keys()
    assert (env.userdata / rel).read_bytes() == b"<fresh/>"


def test_tvos_with_no_plist_is_a_noop(env):
    env.enable_tvos()  # tvOS claimed, but no NSUserDefaults store resolves
    assert env.mod.purge_stale_keys(str(env.userdata)) == (0, 0, 0, 0)
    assert env.deleted == []


def test_empty_userdata_root_is_a_noop(env):
    env.enable_tvos()
    env.seed_plist({KEY_PREFIX + "addon_data/foo/private.xml": b"<stale/>"})
    assert env.mod.purge_stale_keys("") == (0, 0, 0, 0)
    assert env.deleted == []
