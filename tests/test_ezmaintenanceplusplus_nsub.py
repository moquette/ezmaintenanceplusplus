"""Coverage for script.ezmaintenanceplusplus's nsub.py (Apple TV BACKUP completeness).

nsub is the mirror image of nsud: on tvOS, userdata *.xml live only in NSUserDefaults
(gzip-compressed in an on-disk binary plist), invisible to EZM's POSIX `os.walk` backup.
nsub reads that plist DIRECTLY (plistlib + gunzip) and adds every `/userdata/*` key the walk
missed to the open zip. Reading the plist - not xbmcvfs - is load-bearing: on hardware
`xbmcvfs` returns 0 bytes for a key a running add-on wrote (instance-settings, sporthdme,
pvr.artwork), while the plist decodes them fine.

These tests build a REAL binary plist (plistlib) with gzip-compressed values in a temp
sandbox laid out like tvOS (`.../Library/Caches/Kodi` for special://home, `.../Library/
Preferences/<bundle>.plist` for the store), so the real module runs end to end. The three
invariants under test: ADDITIVE + IDEMPOTENT (never re-add / never dup a POSIX-captured
file), the SECRET (dropbox_refresh_token) is never embedded, incl. per-profile copies, and
FULL BACKUP: the pvr.iptvsimple subtree IS captured (owner decision 2026-07-16, reversing
the 2026.07.08.5 exclusion; restore-side sweep in wiz.py handles duplicate instances).
"""

from __future__ import annotations

import gzip
import importlib
import plistlib
import sys
import types
import zipfile
from pathlib import Path

import pytest

ADDON_MODULES = (
    Path(__file__).parent.parent
    / "script.ezmaintenanceplusplus"
    / "resources"
    / "lib"
    / "modules"
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A tvOS-shaped sandbox + the real nsub module with a faked xbmcvfs.translatePath.

    Layout:
      <root>/Library/Caches/Kodi          -> special://home (and the full-home backup root)
      <root>/Library/Caches/Kodi/userdata -> the userdata dir
      <root>/Library/Preferences/<b>.plist-> Kodi's NSUserDefaults store
    """
    root = tmp_path
    home = root / "Library" / "Caches" / "Kodi"
    (home / "userdata").mkdir(parents=True)
    prefs = root / "Library" / "Preferences"
    prefs.mkdir(parents=True)

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda s: str(home) if s == "special://home" else s

    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)
    monkeypatch.syspath_prepend(str(ADDON_MODULES))
    monkeypatch.delitem(sys.modules, "nsub", raising=False)
    mod = importlib.import_module("nsub")

    return types.SimpleNamespace(
        mod=mod, root=root, home=home, prefs=prefs, xbmcvfs=xbmcvfs
    )


def _write_plist(sandbox, entries, name="ca.koditvbox.kodi.tvos.21.plist", extra=None):
    """entries: {userdata-rel: raw file bytes}. Stored gzip-compressed under a /userdata/<rel>
    key (mirrors Kodi). `extra` merges arbitrary top-level keys (e.g. UserdataMigrated)."""
    store = {}
    for rel, body in entries.items():
        store["/userdata/" + rel] = gzip.compress(body)
    if extra:
        store.update(extra)
    with open(sandbox.prefs / name, "wb") as fh:
        plistlib.dump(store, fh)


def _zip(sandbox) -> zipfile.ZipFile:
    return zipfile.ZipFile(str(sandbox.root / "out.zip"), "w", zipfile.ZIP_DEFLATED)


def _names(sandbox) -> list:
    with zipfile.ZipFile(str(sandbox.root / "out.zip")) as z:
        return z.namelist()


def _read(sandbox, arc) -> bytes:
    with zipfile.ZipFile(str(sandbox.root / "out.zip")) as z:
        return z.read(arc)


# --------------------------------------------------------------------------- #
# Headline: capture NSUD-only files the POSIX walk missed (incl. the ones xbmcvfs
# could not read on hardware: instance-settings, sporthdme).
# --------------------------------------------------------------------------- #
def test_captures_nsud_only_files_from_plist(sandbox):
    gui = b'<settings version="2"><setting id="x">1</setting></settings>'
    prof = b'<profiles><setting id="y">1</setting></profiles>'
    _write_plist(
        sandbox,
        {
            "guisettings.xml": gui,
            "profiles.xml": prof,
            "addon_data/plugin.video.sporthdme/settings.xml": b"<s/>",
        },
    )
    zf = _zip(sandbox)
    added, _skipped, _failed = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs=set()
    )
    zf.close()

    assert added == 3
    names = _names(sandbox)
    assert "userdata/guisettings.xml" in names
    assert "userdata/profiles.xml" in names
    assert "userdata/addon_data/plugin.video.sporthdme/settings.xml" in names
    assert _read(sandbox, "userdata/guisettings.xml") == gui
    assert _read(sandbox, "userdata/profiles.xml") == prof


def test_raw_uncompressed_value_also_decodes(sandbox):
    # A value stored WITHOUT gzip (small) must still be captured verbatim.
    body = b"<x/>"
    with open(sandbox.prefs / "ca.koditvbox.kodi.tvos.21.plist", "wb") as fh:
        plistlib.dump({"/userdata/guisettings.xml": body}, fh)  # raw, not gzipped
    zf = _zip(sandbox)
    sandbox.mod.capture_nsud_userdata(zf, str(sandbox.home), already_arcs=set())
    zf.close()
    assert _read(sandbox, "userdata/guisettings.xml") == body


# --------------------------------------------------------------------------- #
# Arc convention: userdata-only backup has no 'userdata/' prefix.
# --------------------------------------------------------------------------- #
def test_userdata_mode_arcname_has_no_prefix(sandbox):
    _write_plist(sandbox, {"guisettings.xml": b"<x/>"})
    zf = _zip(sandbox)
    # source_root IS the userdata dir -> arc_prefix "".
    sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home / "userdata"), already_arcs=set()
    )
    zf.close()
    names = _names(sandbox)
    assert "guisettings.xml" in names
    assert "userdata/guisettings.xml" not in names


# --------------------------------------------------------------------------- #
# Idempotent: a path POSIX already captured is skipped (no dup entry).
# --------------------------------------------------------------------------- #
def test_skips_files_posix_already_captured(sandbox):
    _write_plist(sandbox, {"guisettings.xml": b"POISON-from-plist"})
    zf = _zip(sandbox)
    zf.writestr("userdata/guisettings.xml", b"the real POSIX bytes")
    added, skipped, _failed = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs={"userdata/guisettings.xml"}
    )
    zf.close()
    assert added == 0 and skipped >= 1
    assert _names(sandbox).count("userdata/guisettings.xml") == 1  # no duplicate entry
    assert _read(sandbox, "userdata/guisettings.xml") == b"the real POSIX bytes"


def test_no_plist_is_a_noop(sandbox):
    # Fire TV / desktop: no NSUserDefaults plist -> nothing captured, nothing raised.
    zf = _zip(sandbox)
    added, skipped, failed = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs=set()
    )
    zf.close()
    assert (added, skipped, failed) == (0, 0, 0)
    assert _names(sandbox) == []


# --------------------------------------------------------------------------- #
# The secret must never be captured - master profile AND per-profile copies.
# --------------------------------------------------------------------------- #
def test_never_captures_own_settings_secret(sandbox):
    _write_plist(
        sandbox,
        {
            "addon_data/script.ezmaintenanceplusplus/settings.xml": b'<settings><setting id="dropbox_refresh_token">SECRET</setting></settings>',
            "profiles/Kids/addon_data/script.ezmaintenanceplusplus/settings.xml": b'<settings><setting id="dropbox_refresh_token">SECRET2</setting></settings>',
            "guisettings.xml": b"<ok/>",
        },
    )
    zf = _zip(sandbox)
    sandbox.mod.capture_nsud_userdata(zf, str(sandbox.home), already_arcs=set())
    zf.close()

    names = _names(sandbox)
    assert not any("script.ezmaintenanceplusplus" in n for n in names)
    for n in names:
        assert b"SECRET" not in _read(sandbox, n)
    assert "userdata/guisettings.xml" in names  # the rest still captured


def test_secret_excluded_in_userdata_mode_too(sandbox):
    _write_plist(
        sandbox,
        {
            "addon_data/script.ezmaintenanceplusplus/settings.xml": b'<setting id="dropbox_refresh_token">SECRET</setting>',
            "guisettings.xml": b"<ok/>",
        },
    )
    zf = _zip(sandbox)
    sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home / "userdata"), already_arcs=set()
    )
    zf.close()
    names = _names(sandbox)
    assert "addon_data/script.ezmaintenanceplusplus/settings.xml" not in names
    assert "guisettings.xml" in names


# --------------------------------------------------------------------------- #
# FULL BACKUP includes IPTV: the pvr.iptvsimple addon_data subtree IS captured (top-level
# AND per-profile). Owner decision 2026-07-16 reversed the 2026.07.08.5 backup-side
# exclusion; duplicate-instance safety is the restore-side sweep in wiz.py, not a backup gap.
# --------------------------------------------------------------------------- #
def test_captures_pvr_iptvsimple_subtree(sandbox):
    inst = b'<settings version="2"><setting id="m3u">x</setting></settings>'
    _write_plist(
        sandbox,
        {
            "addon_data/pvr.iptvsimple/instance-settings-1.xml": inst,
            "addon_data/pvr.iptvsimple/customTVGroups-Foo.xml": b"<groups/>",
            "profiles/Kids/addon_data/pvr.iptvsimple/instance-settings-1.xml": b"<kids/>",
            "guisettings.xml": b"<ok/>",
        },
    )
    zf = _zip(sandbox)
    added, _skipped, _failed = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs=set()
    )
    zf.close()

    assert added == 4
    names = _names(sandbox)
    assert "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml" in names
    assert "userdata/addon_data/pvr.iptvsimple/customTVGroups-Foo.xml" in names
    assert (
        "userdata/profiles/Kids/addon_data/pvr.iptvsimple/instance-settings-1.xml"
        in names
    )
    assert "userdata/guisettings.xml" in names
    assert (
        _read(sandbox, "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml")
        == inst
    )


def test_captures_iptv_but_still_excludes_secret(sandbox):
    # Full backup includes IPTV while the add-on's own settings.xml (Dropbox token)
    # stays out - the ONLY exclusion left in this capture.
    _write_plist(
        sandbox,
        {
            "addon_data/pvr.iptvsimple/instance-settings-1.xml": b"<s/>",
            "addon_data/script.ezmaintenanceplusplus/settings.xml": b'<setting id="dropbox_refresh_token">SECRET</setting>',
            "guisettings.xml": b"<ok/>",
        },
    )
    zf = _zip(sandbox)
    sandbox.mod.capture_nsud_userdata(zf, str(sandbox.home), already_arcs=set())
    zf.close()

    names = _names(sandbox)
    assert "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml" in names
    assert not any("script.ezmaintenanceplusplus" in n for n in names)
    for n in names:
        assert b"SECRET" not in _read(sandbox, n)


def test_captures_iptv_in_userdata_mode(sandbox):
    # userdata-only backup: same inclusion, arcnames without the 'userdata/' prefix.
    _write_plist(
        sandbox,
        {"addon_data/pvr.iptvsimple/instance-settings-1.xml": b"<s/>"},
    )
    zf = _zip(sandbox)
    added, _s, _f = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home / "userdata"), already_arcs=set()
    )
    zf.close()
    assert added == 1
    assert "addon_data/pvr.iptvsimple/instance-settings-1.xml" in _names(sandbox)


# --------------------------------------------------------------------------- #
# Non-/userdata bookkeeping keys are ignored; empty/undecodable values skipped.
# --------------------------------------------------------------------------- #
def test_ignores_non_userdata_and_empty_values(sandbox):
    with open(sandbox.prefs / "ca.koditvbox.kodi.tvos.21.plist", "wb") as fh:
        plistlib.dump(
            {
                "UserdataMigrated": True,  # bookkeeping, not a userdata file
                "/userdata/guisettings.xml": gzip.compress(b"<ok/>"),
                "/userdata/empty.xml": b"",  # empty value -> skipped
            },
            fh,
        )
    zf = _zip(sandbox)
    added, _skipped, failed = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs=set()
    )
    zf.close()
    names = _names(sandbox)
    assert names == ["userdata/guisettings.xml"]
    assert added == 1 and failed >= 1


# --------------------------------------------------------------------------- #
# Bundle-id independence: the plist is found by CONTENT (/userdata key), not name.
# --------------------------------------------------------------------------- #
def test_finds_plist_by_content_not_name(sandbox):
    # a differently-named Kodi plist + an unrelated foreign plist that must NOT match.
    with open(sandbox.prefs / "com.apple.something.plist", "wb") as fh:
        plistlib.dump({"unrelated": 1}, fh)
    _write_plist(sandbox, {"guisettings.xml": b"<ok/>"}, name="org.xbmc.kodi.plist")
    zf = _zip(sandbox)
    added, _s, _f = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs=set()
    )
    zf.close()
    assert added == 1
    assert "userdata/guisettings.xml" in _names(sandbox)


# --------------------------------------------------------------------------- #
# Robustness: a corrupt plist / broken translatePath must never raise.
# --------------------------------------------------------------------------- #
def test_corrupt_plist_never_raises(sandbox):
    (sandbox.prefs / "ca.koditvbox.kodi.tvos.21.plist").write_bytes(b"not a plist")
    zf = _zip(sandbox)
    added, _s, _f = sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs=set()
    )
    zf.close()
    assert added == 0  # no crash, nothing captured


def test_broken_translatepath_never_raises(sandbox):
    def _boom(_s):
        raise RuntimeError("vfs exploded")

    sandbox.xbmcvfs.translatePath = _boom
    _write_plist(sandbox, {"guisettings.xml": b"<x/>"})
    zf = _zip(sandbox)
    # must not raise
    sandbox.mod.capture_nsud_userdata(zf, str(sandbox.home), already_arcs=set())
    zf.close()


def test_log_callback_that_raises_is_contained(sandbox):
    _write_plist(sandbox, {"guisettings.xml": b"<x/>"})
    zf = _zip(sandbox)
    # a log() that blows up must not propagate out of the capture (never breaks a backup).
    sandbox.mod.capture_nsud_userdata(
        zf, str(sandbox.home), already_arcs=set(), log=lambda m: 1 / 0
    )
    zf.close()


def test_non_home_root_is_noop(sandbox):
    _write_plist(sandbox, {"guisettings.xml": b"<x/>"})
    weird = sandbox.root / "somewhere_else"
    weird.mkdir()
    zf = _zip(sandbox)
    added, skipped, failed = sandbox.mod.capture_nsud_userdata(
        zf, str(weird), already_arcs=set()
    )
    zf.close()
    assert (added, skipped, failed) == (0, 0, 0)


# --------------------------------------------------------------------------- #
# Wiring: CreateZip augments the zip AFTER the POSIX walk, BEFORE zip_file.close(),
# and only when not canceled.
# --------------------------------------------------------------------------- #
def test_createzip_calls_nsub_after_walk_before_close():
    # The capture now goes through CreateZip's _capture_nsud helper (which calls
    # nsub.capture_nsud_userdata and raises BackupCaptureError on a tvOS failure);
    # the wiring contract is unchanged: after the POSIX walk, guarded on
    # not-canceled, with the manifest written before the zip is closed.
    wiz_src = (ADDON_MODULES / "wiz.py").read_text(encoding="utf-8")
    i_walk = wiz_src.index("written_arcs.add(arc)")
    i_capture = wiz_src.index("cap_added, cap_failed = _capture_nsud(")
    i_manifest = wiz_src.index("_write_manifest(zip_file, entries_total, failed)")
    i_close = wiz_src.index("zip_file.close()")
    assert i_walk < i_capture < i_manifest < i_close
    # The capture is guarded on not-canceled: the guard sits between walk and capture.
    guard = wiz_src.rindex("if not canceled:", i_walk, i_capture)
    assert i_walk < guard < i_capture
    # And the helper really does call nsub's capture.
    helper = wiz_src.index("def _capture_nsud(")
    assert wiz_src.index("nsub.capture_nsud_userdata(", helper) > helper
