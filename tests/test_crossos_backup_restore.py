"""FIRST cross-OS backup/restore roundtrips: tvOS (two-layer) <-> Fire TV (plain POSIX).

Every prior test exercised ONE side of the wire (nsub's capture, nsud's rewrite, the
sandbox IO quirk) in isolation. These tests run the actual owner journey end to end
against tests/fake_kodi_storage.py, the two-layer NSUserDefaults+POSIX fake:

  1. A tvOS box in the post-restore steady state (its userdata *.xml live ONLY in
     NSUserDefaults; a POSIX walk is blind to them) is backed up. The zip must contain
     EVERYTHING, including every addon_data/pvr.iptvsimple file, except only EZM's own
     secret settings.xml. The 2026-07-16 owner contract REVERSES the old "zero IPTV"
     capture exclusion: full means full.
  2. That tvOS zip is restored onto a Fire TV box with a populated IPTV config: after
     restore + the instance-settings sweep, the box's addon_data/pvr.iptvsimple must
     exactly equal the archive, and guisettings.xml must carry the tvOS content.
  3. A Fire TV zip (plain POSIX tree) is restored onto a tvOS box pre-seeded with STALE
     NSUserDefaults keys. The stale keys would otherwise SHADOW the extracted files
     forever (a key is checked FIRST; Kodi never copies a key back to disk). After
     nsud.rewrite_userdata_xml the key layer must hold the ARCHIVE bytes, vectored
     files must be "key-only" (POSIX dropped), and addon-private data stays
     "disk-only".
  4. The wipe: a POSIX-only wipe leaves stale keys that shadow the next restore. The
     owner contract requires a TWO-LAYER wipe (POSIX files AND NSUserDefaults keys).
     Tested against onetap's wipe path, plus a root-cause regression that pins WHY the
     second layer is mandatory (the stale-key shadow) independent of onetap's code.

Behaviors the 2026-07-16 contract mandates but concurrent work has not landed yet are
marked xfail with the precise missing piece; the conditions are probed from the live
module sources at collection, so each xfail lifts itself the moment the fix lands.
No assertion is weakened to pass.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import types
import zipfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
ADDON_ROOT = TESTS_DIR.parent / "script.ezmaintenanceplusplus"
MODULES_DIR = ADDON_ROOT / "resources" / "lib" / "modules"
ADDON_ID = "script.ezmaintenanceplusplus"

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

fks = pytest.importorskip(
    "fake_kodi_storage",
    reason="tests/fake_kodi_storage.py (the two-layer NSUserDefaults/POSIX storage "
    "fake, built concurrently) is not importable yet",
)

NSUB_SRC = (MODULES_DIR / "nsub.py").read_text(encoding="utf-8")
NSUD_SRC = (MODULES_DIR / "nsud.py").read_text(encoding="utf-8")
WIZ_SRC = (MODULES_DIR / "wiz.py").read_text(encoding="utf-8")
ONETAP_SRC = (MODULES_DIR / "onetap.py").read_text(encoding="utf-8")

# --------------------------------------------------------------------------- #
# Collection-time probes for the concurrent fixes this suite locks in. Each is a
# positive marker of the landed behavior, read from the live sources, so the
# matching xfail disappears on the commit that lands the fix.
# --------------------------------------------------------------------------- #

# fix-nsub-capture: the deliberate pvr.iptvsimple capture exclusion is being REMOVED
# (owner contract: a full backup captures addon_data/pvr.iptvsimple on both OSes).
IPTV_CAPTURE_LANDED = "_IPTV_SUBTREE" not in NSUB_SRC

# restore-engine: restore must sweep the target's instance-settings-*.xml so
# pvr.iptvsimple state exactly equals the archive (the duplicate-instance brick guard).
_SWEEP_DEF = re.compile(r"def\s+(\w*sweep\w*)\s*\(")
SWEEP_LANDED = bool(_SWEEP_DEF.search(NSUD_SRC)) or bool(
    _SWEEP_DEF.search(WIZ_SRC) and "instance-settings" in WIZ_SRC
)

# fix-onetap-wipe: the wipe must clear BOTH layers. The current _wipe is a pure
# os.walk/os.remove; any key-layer handling has to reference the NSUserDefaults
# store (via nsud, the plist, or by name).
TWO_LAYER_WIPE_LANDED = any(
    marker in ONETAP_SRC for marker in ("nsud", "NSUserDefaults", "plist")
)


# --------------------------------------------------------------------------- #
# Wiring: build a box (fake + bound xbmc/xbmcvfs), install its Kodi modules, and
# import the real add-on modules fresh against it.
# --------------------------------------------------------------------------- #
def _box(tmp_path, name, platform):
    fake = fks.FakeKodiStorage(tmp_path / name, platform=platform)
    xbmc, xbmcvfs = fks.make_modules(fake)
    return types.SimpleNamespace(fake=fake, xbmc=xbmc, xbmcvfs=xbmcvfs)


def _install_kodi(monkeypatch, box):
    """Point sys.modules' xbmc/xbmcvfs at this box (plus minimal xbmcaddon/xbmcgui
    stubs for module import chains like onetap -> ui)."""
    for attr, fn in (
        ("sleep", lambda ms: None),
        ("executebuiltin", lambda *a, **k: None),
        ("executeJSONRPC", lambda s: '{"result":"OK"}'),
    ):
        if not hasattr(box.xbmc, attr):
            setattr(box.xbmc, attr, fn)

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _Addon:
        def __init__(self, *a, **k):
            pass

        def getAddonInfo(self, key):
            return {"id": ADDON_ID, "name": "EZ Maintenance++"}.get(key, "")

        def getSetting(self, key):
            return ""

        def setSetting(self, key, value):
            return None

    xbmcaddon.Addon = _Addon

    xbmcgui = types.ModuleType("xbmcgui")

    class _Dialog:
        def ok(self, *a, **k):
            return True

        def yesno(self, *a, **k):
            return False

        def notification(self, *a, **k):
            return None

        def select(self, *a, **k):
            return -1

    class _DialogProgress:
        def create(self, *a, **k):
            return None

        def update(self, *a, **k):
            return None

        def close(self):
            return None

        def iscanceled(self):
            return False

    xbmcgui.Dialog = _Dialog
    xbmcgui.DialogProgress = _DialogProgress

    monkeypatch.setitem(sys.modules, "xbmc", box.xbmc)
    monkeypatch.setitem(sys.modules, "xbmcvfs", box.xbmcvfs)
    monkeypatch.setitem(sys.modules, "xbmcaddon", xbmcaddon)
    monkeypatch.setitem(sys.modules, "xbmcgui", xbmcgui)


def _import_flat(monkeypatch, name):
    """Import nsub/nsud as their own tests do: top-level from the modules dir, fresh,
    so their module-level `import xbmcvfs` binds to the CURRENT box."""
    monkeypatch.syspath_prepend(str(MODULES_DIR))
    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name)


def _import_pkg(monkeypatch, name):
    """Import a module that needs the resources package (onetap -> ui), fresh."""
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for mod in list(sys.modules):
        if mod == "resources" or mod.startswith("resources."):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    return importlib.import_module("resources.lib.modules." + name)


# --------------------------------------------------------------------------- #
# The backup capture path, exactly as wiz.CreateZip runs it for a userdata backup:
# the POSIX os.walk (forward-slash arcnames, 'temp' dirs excluded) followed by
# nsub.capture_nsud_userdata over the same open zip. Replicated here because
# importing wiz drags in the full UI stack; the walk -> capture -> close wiring
# order is already pinned by test_ezmaintenanceplusplus_nsub.py.
# --------------------------------------------------------------------------- #
def _backup_userdata_zip(nsub_mod, userdata_root, zip_path):
    abs_src = os.path.abspath(str(userdata_root))
    assert nsub_mod._arc_context(abs_src) == "", (
        "userdata-mode backup: the source root must be the userdata dir"
    )
    written = set()
    zf = zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED, allowZip64=True)
    try:
        for dirpath, dirnames, filenames in os.walk(abs_src):
            dirnames[:] = [d for d in dirnames if d != "temp"]
            for fname in filenames:
                fpath = os.path.normpath(os.path.join(dirpath, fname))
                arc = fpath[len(abs_src) + 1 :].replace(os.sep, "/")
                zf.write(fpath, arc)
                written.add(arc)
        result = nsub_mod.capture_nsud_userdata(zf, abs_src, written)
    finally:
        zf.close()
    return result


def _zip_map(zip_path):
    with zipfile.ZipFile(str(zip_path)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _zip_names(zip_path):
    with zipfile.ZipFile(str(zip_path)) as z:
        return z.namelist()


def _extract(zip_path, dest_root):
    """The restore extractor: plain zipfile (a POSIX write that BYPASSES CTVOSFile),
    exactly what wiz.ExtractWithProgress does."""
    with zipfile.ZipFile(str(zip_path)) as z:
        z.extractall(str(dest_root))


def _read_vfs(xbmcvfs, rel):
    """Read a userdata file the way Kodi does on tvOS: through the VFS, key first."""
    f = xbmcvfs.File("special://home/userdata/" + rel)
    try:
        got = f.readBytes()
        return bytes(got) if got is not None else b""
    finally:
        f.close()


def _tree(root):
    """{userdata-relative path: bytes} for every POSIX file under root."""
    out = {}
    for dirpath, _dirs, files in os.walk(str(root)):
        for name in files:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, str(root)).replace(os.sep, "/")
            with open(p, "rb") as fh:
                out[rel] = fh.read()
    return out


# --------------------------------------------------------------------------- #
# Content fixtures. Origin-distinct bytes so a wrong-layer/wrong-origin read can
# never accidentally pass.
# --------------------------------------------------------------------------- #
GUI_TVOS = b'<settings version="2"><setting id="lookandfeel.skin">tvos-skin</setting></settings>'
GUI_ANDROID = b'<settings version="2"><setting id="lookandfeel.skin">android-skin</setting></settings>'
IPTV_INST_TVOS = (
    b'<settings version="2"><setting id="m3uUrl">tvos-playlist</setting></settings>'
)
IPTV_SET_TVOS = b'<settings version="2"><setting id="origin">tvos-iptv-settings</setting></settings>'
IPTV_INST_ANDROID = (
    b'<settings version="2"><setting id="m3uUrl">android-playlist</setting></settings>'
)
MENU_TVOS = b"<shortcuts><shortcut>tvos-menu</shortcut></shortcuts>"
MENU_ANDROID = b"<shortcuts><shortcut>android-menu</shortcut></shortcuts>"
SOURCES_XML = b"<sources><video><default/></video></sources>"
SECRET_XML = (
    b'<settings><setting id="dropbox_refresh_token">SECRET-DO-NOT-SHIP</setting>'
    b"</settings>"
)

SECRET_REL = "addon_data/%s/settings.xml" % ADDON_ID
IPTV_DIR = "addon_data/pvr.iptvsimple/"

# The post-restore steady state of a healthy tvOS box: every Kodi-read userdata xml
# is KEY-ONLY (nsud vectored it and dropped the POSIX twin); sources.xml sits in
# both layers (identical bytes: freshly persisted, twin not yet dropped).
TVOS_STEADY_STATE_KEYS = {
    "guisettings.xml": GUI_TVOS,
    "addon_data/pvr.iptvsimple/instance-settings-1.xml": IPTV_INST_TVOS,
    "addon_data/pvr.iptvsimple/settings.xml": IPTV_SET_TVOS,
    "addon_data/script.skinshortcuts/menu.DATA.xml": MENU_TVOS,
    SECRET_REL: SECRET_XML,
}

EXPECTED_TVOS_BACKUP = {
    "guisettings.xml": GUI_TVOS,
    "sources.xml": SOURCES_XML,
    "addon_data/pvr.iptvsimple/instance-settings-1.xml": IPTV_INST_TVOS,
    "addon_data/pvr.iptvsimple/settings.xml": IPTV_SET_TVOS,
    "addon_data/script.skinshortcuts/menu.DATA.xml": MENU_TVOS,
}


def _seed_tvos_steady_state(fake):
    for rel, data in TVOS_STEADY_STATE_KEYS.items():
        fake.seed_key(rel, data)
    fake.seed_disk("sources.xml", SOURCES_XML)
    fake.seed_key("sources.xml", SOURCES_XML)


def _build_tvos_backup(tmp_path, monkeypatch):
    """Seed a steady-state tvOS box and run the real backup capture path over it.
    Returns (zip_path, archive_map)."""
    src = _box(tmp_path, "tvos-src", "tvos")
    _install_kodi(monkeypatch, src)
    nsub = _import_flat(monkeypatch, "nsub")
    _seed_tvos_steady_state(src.fake)
    assert src.fake.state("guisettings.xml") == "key-only"
    assert src.fake.state("sources.xml") == "both"
    zip_path = tmp_path / "tvos_backup.zip"
    _backup_userdata_zip(nsub, src.fake.userdata, zip_path)
    return zip_path, _zip_map(zip_path)


# --------------------------------------------------------------------------- #
# 1. tvOS backup completeness: the POSIX walk is blind to key-only files; the
#    plist capture must supply them ALL, minus only the secret.
# --------------------------------------------------------------------------- #
def test_tvos_backup_captures_key_only_userdata_excluding_only_the_secret(
    tmp_path, monkeypatch
):
    zip_path, members = _build_tvos_backup(tmp_path, monkeypatch)

    # Key-only files the POSIX walk could never see are in the zip, byte-exact.
    assert members["guisettings.xml"] == GUI_TVOS
    assert members["addon_data/script.skinshortcuts/menu.DATA.xml"] == MENU_TVOS
    # The both-layers file came from the walk and is not duplicated by the capture.
    assert members["sources.xml"] == SOURCES_XML
    assert _zip_names(zip_path).count("sources.xml") == 1
    # The ONLY legitimate exclusion: EZM's own settings.xml (the Dropbox secret).
    assert SECRET_REL not in members
    for data in members.values():
        assert b"SECRET-DO-NOT-SHIP" not in data


@pytest.mark.xfail(
    condition=not IPTV_CAPTURE_LANDED,
    reason="fix-nsub-capture not landed: nsub.py still hard-excludes the "
    "pvr.iptvsimple subtree from the NSUserDefaults capture (_IPTV_SUBTREE); the "
    "2026-07-16 owner contract reverses that: a full backup MUST capture "
    "addon_data/pvr.iptvsimple",
    strict=False,
)
def test_tvos_backup_captures_every_pvr_iptvsimple_file(tmp_path, monkeypatch):
    _zip_path, members = _build_tvos_backup(tmp_path, monkeypatch)

    assert (
        members["addon_data/pvr.iptvsimple/instance-settings-1.xml"] == IPTV_INST_TVOS
    )
    assert members["addon_data/pvr.iptvsimple/settings.xml"] == IPTV_SET_TVOS
    # Completeness, exactly: everything seeded travels, nothing else does.
    assert members == EXPECTED_TVOS_BACKUP


# --------------------------------------------------------------------------- #
# 2. tvOS zip -> Fire TV restore.
# --------------------------------------------------------------------------- #
def _restore_onto_android(tmp_path, monkeypatch, zip_path):
    dst = _box(tmp_path, "android-dst", "android")
    _install_kodi(monkeypatch, dst)
    nsud = _import_flat(monkeypatch, "nsud")
    # A lived-in Fire TV box: its own look, its own (divergent) IPTV config,
    # including a second instance the archive does not carry.
    dst.fake.seed_disk("guisettings.xml", GUI_ANDROID)
    dst.fake.seed_disk(IPTV_DIR + "instance-settings-1.xml", b"<old-android-1/>")
    dst.fake.seed_disk(IPTV_DIR + "instance-settings-2.xml", b"<stale-android-2/>")
    dst.fake.seed_disk(IPTV_DIR + "settings.xml", b"<old-android-iptv-settings/>")

    _extract(zip_path, dst.fake.userdata)
    return dst, nsud


def _find_sweep(nsud_mod):
    for name in dir(nsud_mod):
        if "sweep" in name.lower() and callable(getattr(nsud_mod, name)):
            return getattr(nsud_mod, name)
    return None


def _call_sweep(fn, userdata_root, archive_map):
    archive_rels = set(archive_map)
    for args in (
        (str(userdata_root), archive_rels),
        (str(userdata_root), sorted(archive_rels)),
        (str(userdata_root),),
    ):
        try:
            fn(*args)
            return
        except TypeError:
            continue
    pytest.fail("discovered sweep %r accepts none of the expected signatures" % fn)


def test_tvos_zip_restored_onto_firetv_carries_tvos_guisettings(tmp_path, monkeypatch):
    zip_path, _members = _build_tvos_backup(tmp_path, monkeypatch)
    dst, nsud = _restore_onto_android(tmp_path, monkeypatch, zip_path)

    nsud.rewrite_userdata_xml(dst.fake.userdata)

    # The restored settings arrived and survived the rewrite; on Android the
    # special:// path IS the POSIX file, so the file must still be on disk.
    assert dst.fake.state("guisettings.xml") == "disk-only"
    gui = os.path.join(dst.fake.userdata, "guisettings.xml")
    with open(gui, "rb") as fh:
        assert fh.read() == GUI_TVOS
    # The secret never traveled.
    assert not os.path.exists(os.path.join(dst.fake.userdata, SECRET_REL))


_IPTV_STATE_MISSING = []
if not IPTV_CAPTURE_LANDED:
    _IPTV_STATE_MISSING.append(
        "fix-nsub-capture not landed (nsub.py still excludes pvr.iptvsimple, so a "
        "tvOS archive carries no IPTV config at all)"
    )
if not SWEEP_LANDED:
    _IPTV_STATE_MISSING.append(
        "restore-engine instance-settings sweep not landed (no sweep is defined in "
        "nsud.py or wiz.py, so a stale instance-settings-2.xml on the target "
        "survives the restore: the duplicate-instance brick)"
    )


@pytest.mark.xfail(
    condition=bool(_IPTV_STATE_MISSING),
    reason="; ".join(_IPTV_STATE_MISSING) or "landed",
    strict=False,
)
def test_tvos_zip_to_firetv_iptv_state_exactly_equals_archive(tmp_path, monkeypatch):
    zip_path, members = _build_tvos_backup(tmp_path, monkeypatch)
    dst, nsud = _restore_onto_android(tmp_path, monkeypatch, zip_path)

    sweep = _find_sweep(nsud)
    if sweep is not None:
        _call_sweep(sweep, dst.fake.userdata, members)
    nsud.rewrite_userdata_xml(dst.fake.userdata)

    expected = {
        rel[len(IPTV_DIR) :]: data
        for rel, data in members.items()
        if rel.startswith(IPTV_DIR)
    }
    assert expected, "the tvOS archive must itself carry the IPTV config"
    got = _tree(os.path.join(dst.fake.userdata, "addon_data", "pvr.iptvsimple"))
    assert got == expected, (
        "after restore + sweep, pvr.iptvsimple on the target must EXACTLY equal "
        "the archive (stale instances removed, archive instances byte-exact)"
    )


# --------------------------------------------------------------------------- #
# 3. Fire TV zip -> tvOS restore: stale keys must be overwritten, vectored files
#    end key-only, private add-on data stays plain POSIX.
# --------------------------------------------------------------------------- #
FIRETV_ARCHIVE = {
    "guisettings.xml": GUI_ANDROID,
    "addon_data/pvr.iptvsimple/instance-settings-1.xml": IPTV_INST_ANDROID,
    "addon_data/script.skinshortcuts/menu.DATA.xml": MENU_ANDROID,
    "addon_data/plugin.video.example/cache.dat": b"\x00binary-cache-not-xml\x01",
}


def test_firetv_zip_restored_onto_tvos_overwrites_stale_keys_and_drops_posix(
    tmp_path, monkeypatch
):
    zip_path = tmp_path / "firetv_backup.zip"
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in FIRETV_ARCHIVE.items():
            z.writestr(rel, data)

    dst = _box(tmp_path, "tvos-dst", "tvos")
    _install_kodi(monkeypatch, dst)
    nsud = _import_flat(monkeypatch, "nsud")

    # The box's previous life: stale keys that SHADOW anything a plain extract
    # writes (reads check the key first; Kodi never copies a key back to disk).
    dst.fake.seed_key("guisettings.xml", b"<stale-tvos-guisettings/>")
    dst.fake.seed_key(
        "addon_data/pvr.iptvsimple/instance-settings-1.xml", b"<stale-tvos-iptv/>"
    )

    _extract(zip_path, dst.fake.userdata)
    # The extract alone changed nothing Kodi can see: the stale key still wins.
    assert _read_vfs(dst.xbmcvfs, "guisettings.xml") == b"<stale-tvos-guisettings/>"

    written, skipped, failed = nsud.rewrite_userdata_xml(dst.fake.userdata)
    assert (written, skipped, failed) == (2, 1, 0)

    # Reads through the key layer now return the ARCHIVE content: stale keys gone.
    assert _read_vfs(dst.xbmcvfs, "guisettings.xml") == GUI_ANDROID
    assert (
        _read_vfs(dst.xbmcvfs, "addon_data/pvr.iptvsimple/instance-settings-1.xml")
        == IPTV_INST_ANDROID
    )
    # Vectored files are key-only (the POSIX twin was dropped after a confirmed
    # read-back), so File Manager lists them once and nothing shadows anything.
    assert dst.fake.state("guisettings.xml") == "key-only"
    assert (
        dst.fake.state("addon_data/pvr.iptvsimple/instance-settings-1.xml")
        == "key-only"
    )
    # Add-on private data is NEVER vectored: its owner reads it with plain open(),
    # and a key-only copy would be invisible to it (the skinshortcuts menu bug).
    assert dst.fake.state("addon_data/script.skinshortcuts/menu.DATA.xml") == (
        "disk-only"
    )
    assert dst.fake.state("addon_data/plugin.video.example/cache.dat") == "disk-only"
    with open(
        os.path.join(
            dst.fake.userdata, "addon_data", "script.skinshortcuts", "menu.DATA.xml"
        ),
        "rb",
    ) as fh:
        assert fh.read() == MENU_ANDROID


# --------------------------------------------------------------------------- #
# 4. The wipe must clear BOTH layers. A POSIX-only wipe leaves stale keys that
#    shadow the very restore the wipe was preparing for.
# --------------------------------------------------------------------------- #
WIPE_SEEDS_XML = (
    "guisettings.xml",
    "sources.xml",
    "addon_data/pvr.iptvsimple/instance-settings-1.xml",
)


def _seed_wipe_box(fake):
    fake.seed_disk("guisettings.xml", GUI_TVOS)
    fake.seed_key("guisettings.xml", GUI_TVOS)  # both layers
    fake.seed_key("sources.xml", SOURCES_XML)  # key-only
    fake.seed_key(
        "addon_data/pvr.iptvsimple/instance-settings-1.xml", IPTV_INST_TVOS
    )  # key-only
    fake.seed_disk("keyboard.xml", b"<keymap/>")  # disk-only
    fake.seed_disk(
        "addon_data/plugin.video.example/cache.dat", b"\x00cache\x01"
    )  # disk-only, non-xml
    return WIPE_SEEDS_XML + (
        "keyboard.xml",
        "addon_data/plugin.video.example/cache.dat",
    )


def _maybe_run_extra_key_wipe(onetap_mod, home):
    """If fix-onetap-wipe lands the key-layer pass as a separate entry point (rather
    than inside _wipe), find and run it too."""
    for name in (
        "wipe_two_layer",
        "_wipe_two_layer",
        "wipe_keys",
        "_wipe_keys",
        "wipe_nsud_keys",
        "_wipe_nsud_keys",
    ):
        fn = getattr(onetap_mod, name, None)
        if not callable(fn):
            continue
        for args in ((home, onetap_mod._wipe_excludes()), (home,), ()):
            try:
                fn(*args)
                return
            except TypeError:
                continue


@pytest.mark.xfail(
    condition=not TWO_LAYER_WIPE_LANDED,
    reason="fix-onetap-wipe not landed: onetap._wipe is a POSIX-only "
    "os.walk/os.remove, so key-only NSUserDefaults entries survive the wipe and "
    "shadow the next restore (owner contract 2026-07-16: a tvOS wipe clears BOTH "
    "layers)",
    strict=False,
)
def test_onetap_wipe_clears_both_layers_on_tvos(tmp_path, monkeypatch):
    box = _box(tmp_path, "tvos-wipe", "tvos")
    _install_kodi(monkeypatch, box)
    onetap = _import_pkg(monkeypatch, "onetap")
    rels = _seed_wipe_box(box.fake)

    onetap._wipe(box.fake.home, onetap._wipe_excludes())
    _maybe_run_extra_key_wipe(onetap, box.fake.home)

    for rel in rels:
        assert box.fake.state(rel) == "absent", (
            "%s survived the wipe in the %r layer" % (rel, box.fake.state(rel))
        )


def test_posix_only_wipe_leaves_stale_keys_that_shadow_the_next_restore(
    tmp_path, monkeypatch
):
    """The ROOT CAUSE the two-layer wipe exists for, pinned independently of
    onetap's implementation: a POSIX-only wipe (what the wipe was before the
    2026-07-16 contract) leaves every NSUserDefaults key alive, and a stale key
    SHADOWS whatever the next restore extracts - reads check the key FIRST and
    Kodi never copies a key back to disk. Also pins the corrected CLAUDE.md fact
    that closes the loop: xbmcvfs.delete on an eligible userdata xml drops ONLY
    the key (never the POSIX file), which is exactly the second layer a real wipe
    must clear explicitly."""
    box = _box(tmp_path, "tvos-wipe-sim", "tvos")
    _install_kodi(monkeypatch, box)
    _seed_wipe_box(box.fake)

    # The OLD, buggy wipe: remove every POSIX file under home. Nothing else.
    for dirpath, _dirs, files in os.walk(box.fake.home):
        for name in files:
            os.remove(os.path.join(dirpath, name))

    # Disk-only files are gone, but every key survived the "wipe".
    assert box.fake.state("keyboard.xml") == "absent"
    assert box.fake.state("guisettings.xml") == "key-only"
    assert box.fake.state("sources.xml") == "key-only"

    # The next restore extracts fresh content with plain POSIX I/O...
    box.fake.seed_disk("guisettings.xml", GUI_ANDROID)
    # ...and Kodi still serves the STALE key: the restore looks like it never ran.
    assert _read_vfs(box.xbmcvfs, "guisettings.xml") == GUI_TVOS

    # The only way out is clearing the key layer explicitly. xbmcvfs.delete does
    # exactly (and only) that: the key drops, the POSIX file is untouched.
    assert box.xbmcvfs.delete("special://home/userdata/guisettings.xml") is True
    assert box.fake.state("guisettings.xml") == "disk-only"
    assert _read_vfs(box.xbmcvfs, "guisettings.xml") == GUI_ANDROID
