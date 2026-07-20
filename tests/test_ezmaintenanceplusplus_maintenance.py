"""Coverage for script.ezmaintenanceplusplus's maintenance.py clean helpers and
the service.py boot-path restructure (the 2026-07-09 P1).

What is pinned here and why:

* `_clean_tree` REPLACED four copy-pasted os.walk loops whose rmtree pass was
  nested inside an `if file_count > 0:` gate - a directory level holding
  subdirectories but zero loose files was never cleaned at all. The dir-only
  tests are the regression for that bug.
* Protected names (kodi.log etc.) must survive a cache clean; protected DIRS
  (temp/, archive_cache/) are kept as directories but their contents are still
  cleaned - matching what the old walk did by recursing into them.
* `getNextMaintenance` is read from the PLUGIN process too (default.py's
  Maintenance submenu), where nothing guarantees the service has set the
  window property yet - int("") used to blow up the listing.
* service.py used to run two full-tree walks and two modal yesno prompts AT
  IMPORT, during Kodi boot. Importing it must now be side-effect-free; the
  walks/prompts live in _startup_checks(), and the packages file count must be
  the TOTAL across subfolders (the old loop reset `count = 0` per folder).

Same fixture approach as test_ezmaintenanceplusplus_wiz.py: fake just enough
of xbmc*/xbmcaddon/xbmcgui/xbmcvfs for the real modules to import, then
exercise the real functions.
"""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
ADDON_ROOT = REPO_ROOT / "script.ezmaintenanceplusplus"


class _Recorder:
    def __init__(self):
        self.yesno_calls = []
        self.yesno_answer = 0
        self.dialogs_created = 0
        self.builtins = []
        self.window_props = {}
        self.settings = {}


def _install_fakes(monkeypatch, tmp_path, rec):
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    xbmc = types.ModuleType("xbmc")
    xbmc.translatePath = lambda p: p.replace("special://", str(tmp_path) + "/")
    xbmc.getLocalizedString = lambda i: str(i)
    xbmc.getInfoLabel = lambda s: ""
    xbmc.getCondVisibility = lambda s: True  # GUI is "up" for _wait_kodi_ready
    xbmc.getSkinDir = lambda: "skin.estuary"
    xbmc.log = lambda *a, **k: None
    xbmc.executebuiltin = lambda cmd: rec.builtins.append(cmd)
    xbmc.executeJSONRPC = lambda cmd: "{}"
    xbmc.LOGERROR = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGINFO = 3
    xbmc.LOGDEBUG = 4
    xbmc.LOGNOTICE = 3
    xbmc.PLAYLIST_VIDEO = 1
    xbmc.sleep = lambda ms: None
    xbmc.Player = lambda *a, **k: types.SimpleNamespace(
        isPlayingVideo=lambda: False, play=lambda *a, **k: None
    )
    xbmc.Monitor = type(
        "Monitor",
        (),
        {"abortRequested": lambda self: False, "waitForAbort": lambda self, t: False},
    )

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _FakeAddon:
        def __init__(self, *a, **k):
            pass

        def getLocalizedString(self, i):
            return str(i)

        def getSetting(self, key):
            return rec.settings.get(key, "")

        def setSetting(self, key, value):
            rec.settings[key] = value

        def getAddonInfo(self, key):
            return {
                "id": "script.ezmaintenanceplusplus",
                "name": "EZ Maintenance++",
                "path": str(ADDON_ROOT),
                "profile": "special://profile/",
                "version": "0.0.0",
            }.get(key, "")

    xbmcaddon.Addon = _FakeAddon

    xbmcgui = types.ModuleType("xbmcgui")

    class _FakeDialogProgress:
        def create(self, *a, **k):
            pass

        def update(self, *a, **k):
            pass

        def close(self):
            pass

        def iscanceled(self):
            return False

    class _FakeDialog:
        def __init__(self):
            rec.dialogs_created += 1

        def ok(self, *a, **k):
            return False

        def yesno(self, *a, **k):
            rec.yesno_calls.append(a)
            return rec.yesno_answer

        def notification(self, *a, **k):
            pass

        def select(self, *a, **k):
            return -1

    xbmcgui.DialogProgress = _FakeDialogProgress
    xbmcgui.DialogProgressBG = _FakeDialogProgress
    xbmcgui.Dialog = _FakeDialog
    xbmcgui.NOTIFICATION_INFO = "info"
    xbmcgui.NOTIFICATION_WARNING = "warning"
    xbmcgui.NOTIFICATION_ERROR = "error"
    xbmcgui.ListItem = lambda *a, **k: types.SimpleNamespace(
        setArt=lambda *a, **k: None,
        setInfo=lambda *a, **k: None,
        setProperty=lambda *a, **k: None,
    )

    class _FakeWindow:
        def __init__(self, *a, **k):
            pass

        def getProperty(self, k):
            return rec.window_props.get(k, "")

        def setProperty(self, k, v):
            rec.window_props[k] = v

        def clearProperty(self, k):
            rec.window_props.pop(k, None)

    xbmcgui.Window = _FakeWindow
    xbmcgui.WindowDialog = _FakeWindow

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = xbmc.translatePath
    xbmcvfs.exists = lambda p: Path(p).exists()
    xbmcvfs.mkdirs = lambda p: Path(p).mkdir(parents=True, exist_ok=True)
    xbmcvfs.mkdir = lambda p: Path(p).mkdir(parents=True, exist_ok=True)
    xbmcvfs.rmdir = lambda p: None
    xbmcvfs.delete = lambda p: None
    xbmcvfs.listdir = lambda p: ([], [])
    xbmcvfs.File = lambda *a, **k: types.SimpleNamespace(
        read=lambda *a: b"", write=lambda *a: True, close=lambda: None, size=lambda: 0
    )

    rec.dir_items = []
    rec.end_dirs = []
    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.addDirectoryItem = lambda *a, **k: (
        rec.dir_items.append(k.get("url") or (a[1] if len(a) > 1 else "")) or True
    )
    xbmcplugin.endOfDirectory = lambda *a, **k: rec.end_dirs.append(True)

    for name, mod in (
        ("xbmc", xbmc),
        ("xbmcaddon", xbmcaddon),
        ("xbmcgui", xbmcgui),
        ("xbmcvfs", xbmcvfs),
        ("xbmcplugin", xbmcplugin),
    ):
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def maint(monkeypatch, tmp_path):
    rec = _Recorder()
    _install_fakes(monkeypatch, tmp_path, rec)
    mod = importlib.import_module("resources.lib.modules.maintenance")
    mod._rec = rec
    mod._tmp = tmp_path
    return mod


@pytest.fixture
def service(monkeypatch, tmp_path):
    rec = _Recorder()
    _install_fakes(monkeypatch, tmp_path, rec)
    monkeypatch.delitem(sys.modules, "ezm_service_under_test", raising=False)
    spec = importlib.util.spec_from_file_location(
        "ezm_service_under_test", ADDON_ROOT / "service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._rec = rec
    mod._tmp = tmp_path
    return mod


def _import_plugin(monkeypatch, tmp_path, rec, argv):
    """Import default.py the way Kodi invokes it: module-level code parses
    sys.argv and routes immediately. THE regression net for import-time breaks
    - the urllib.parse AttributeError shipped past 1268 green tests because
    nothing imported EZM's default.py."""
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delitem(sys.modules, "ezm_default_under_test", raising=False)
    spec = importlib.util.spec_from_file_location(
        "ezm_default_under_test", ADDON_ROOT / "default.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mktree(base, spec):
    """spec: {relpath: bytes-content} for files, relpath ending in '/' for dirs."""
    for rel, content in spec.items():
        p = base / rel.rstrip("/")
        if rel.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)


# ---------------------------------------------------------------- _clean_tree


def test_clean_tree_removes_files_and_dirs(maint, tmp_path):
    root = tmp_path / "junk"
    _mktree(root, {"a.txt": b"x", "sub/b.txt": b"y", "sub/deep/c.txt": b"z"})
    maint._clean_tree(str(root))
    assert root.exists()
    assert list(root.iterdir()) == []


def test_clean_tree_cleans_dir_only_level(maint, tmp_path):
    # REGRESSION: the old walk skipped the rmtree pass on any level with zero
    # loose files, so a dir-only tree was never cleaned at all.
    root = tmp_path / "junk"
    _mktree(root, {"sub1/x.txt": b"x", "sub2/deep/": None or b""})
    (root / "sub2" / "deep").mkdir(parents=True, exist_ok=True)
    assert not any(p.is_file() for p in root.iterdir())  # dir-only top level
    maint._clean_tree(str(root))
    assert list(root.iterdir()) == []


def test_clean_tree_keeps_protected_files(maint, tmp_path):
    root = tmp_path / "junk"
    _mktree(root, {"kodi.log": b"log", "junk.txt": b"x"})
    maint._clean_tree(str(root), keep_files=maint.KEEP_FILES)
    assert (root / "kodi.log").exists()
    assert not (root / "junk.txt").exists()


def test_clean_tree_keeps_protected_dirs_but_cleans_contents(maint, tmp_path):
    root = tmp_path / "junk"
    _mktree(
        root,
        {
            "temp/inside.txt": b"x",
            "temp/kodi.log": b"log",
            "temp/subdir/deep.txt": b"y",
            "gone/inside.txt": b"z",
        },
    )
    maint._clean_tree(str(root), keep_files=maint.KEEP_FILES, keep_dirs=maint.KEEP_DIRS)
    assert (root / "temp").is_dir()  # kept
    assert not (root / "temp" / "inside.txt").exists()  # but cleaned
    assert (root / "temp" / "kodi.log").exists()  # file protection applies inside too
    assert not (root / "temp" / "subdir").exists()
    assert not (root / "gone").exists()


def test_clean_tree_remove_dirs_false_keeps_skeleton(maint, tmp_path):
    root = tmp_path / "thumbs"
    _mktree(root, {"0/a.jpg": b"x", "f/b.jpg": b"y"})
    maint._clean_tree(str(root), remove_dirs=False)
    assert (root / "0").is_dir() and (root / "f").is_dir()
    assert not (root / "0" / "a.jpg").exists()
    assert not (root / "f" / "b.jpg").exists()


def test_clean_tree_missing_path_is_noop(maint, tmp_path):
    maint._clean_tree(str(tmp_path / "does-not-exist"))  # must not raise


# ------------------------------------------------------------ public cleaners


def test_clearCache_protects_logs_and_keeps_temp_dir(maint, tmp_path):
    cache = Path(maint.cachePath)
    temp = Path(maint.tempPath)
    _mktree(cache, {"stale.bin": b"x", "sub/deep.bin": b"y", "temp/held.bin": b"z"})
    _mktree(temp, {"kodi.log": b"log", "commoncache.db": b"db", "junk.tmp": b"x"})
    maint.clearCache(mode="silent")
    assert not (cache / "stale.bin").exists()
    assert not (cache / "sub").exists()
    assert (cache / "temp").is_dir()
    assert not (cache / "temp" / "held.bin").exists()
    assert (temp / "kodi.log").exists()
    assert (temp / "commoncache.db").exists()
    assert not (temp / "junk.tmp").exists()


def test_purgePackages_cleans_dir_only_packages(maint, tmp_path):
    # REGRESSION for the dir-only skip: packages/ holding only a subfolder.
    packages = tmp_path / "home" / "addons" / "packages"
    _mktree(packages, {"nested/leftover.zip": b"z"})
    maint.purgePackages(mode="silent")
    assert list(packages.iterdir()) == []


def test_deleteThumbnails_distinct_legacy_dir_fully_removed(maint, tmp_path):
    # The fixture maps special://thumbnails and userdata/Thumbnails to DIFFERENT
    # dirs - the legacy/split layout. Skeleton kept in the live cache, the
    # separate legacy dir removed whole, Textures13.db unlinked.
    thumbs_special = Path(maint.thumbnailPath)
    thumbs_userdata = Path(maint.THUMBS)
    db = Path(maint.databasePath)
    _mktree(thumbs_special, {"0/a.jpg": b"x"})
    _mktree(thumbs_userdata, {"f/b.jpg": b"y"})
    _mktree(db, {"Textures13.db": b"db"})
    maint.deleteThumbnails(mode="silent")
    assert (thumbs_special / "0").is_dir()  # skeleton kept
    assert not (thumbs_special / "0" / "a.jpg").exists()
    assert list(thumbs_userdata.iterdir()) == []  # separate legacy dir emptied
    assert not (db / "Textures13.db").exists()


def test_deleteThumbnails_aliased_paths_preserve_bucket_skeleton(maint, tmp_path):
    # REGRESSION (caught in adversarial QA 2026-07-09): on a REAL box
    # special://thumbnails resolves INTO userdata/Thumbnails - thumbnailPath and
    # THUMBS are the SAME directory. The second cleaning pass must not rmtree
    # the 0-f/ bucket skeleton the first pass just preserved. The old walk got
    # this right only by accident (its rmtree was disabled by the file_count
    # bug); this pins it deliberately.
    shared = tmp_path / "home" / "userdata" / "Thumbnails"
    _mktree(shared, {"0/a.jpg": b"x", "f/b.jpg": b"y", "Video/c.jpg": b"z"})
    maint.thumbnailPath = str(shared)
    maint.THUMBS = str(shared)
    maint.deleteThumbnails(mode="silent")
    assert sorted(p.name for p in shared.iterdir()) == ["0", "Video", "f"]
    assert not (shared / "0" / "a.jpg").exists()
    assert not (shared / "f" / "b.jpg").exists()
    assert not (shared / "Video" / "c.jpg").exists()


# --------------------------------------------------------- getNextMaintenance


def test_getNextMaintenance_unset_property_returns_zero(maint):
    # REGRESSION: default.py's Maintenance submenu reads this from the PLUGIN
    # process; before the service sets the property, int("") used to raise.
    assert maint.getNextMaintenance() == 0


def test_getNextMaintenance_garbage_returns_zero(maint):
    maint._rec.window_props["ezmaintenance.nextMaintenanceTime"] = "not-a-number"
    assert maint.getNextMaintenance() == 0


def test_getNextMaintenance_roundtrips_from_determine(maint):
    maint._rec.settings["autoCleanDays"] = "0"
    maint.determineNextMaintenance()
    assert maint.getNextMaintenance() == 0
    maint._rec.window_props["ezmaintenance.nextMaintenanceTime"] = "12345"
    assert maint.getNextMaintenance() == 12345


# -------------------------------------------------------------- service boot


def test_service_import_is_side_effect_free(service):
    # The old service walked packages/ + Thumbnails and could pop TWO modal
    # yesno prompts AT IMPORT, i.e. during Kodi boot. Importing must now do
    # neither; the checks live in _startup_checks().
    assert service._rec.yesno_calls == []
    assert service._rec.dialogs_created == 0


def test_folder_size_and_count_totals_across_subfolders(service, tmp_path):
    # REGRESSION: the old loop reset `count = 0` inside the outer os.walk, so
    # the "N zip files" dialog reported only the LAST subfolder's count.
    root = tmp_path / "pkgs"
    _mktree(
        root,
        {
            "a.zip": b"12345",
            "sub1/b.zip": b"123",
            "sub1/c.zip": b"1",
            "sub2/d.zip": b"12",
        },
    )
    total, count = service._folder_size_and_count(str(root))
    assert count == 4
    assert total == 5 + 3 + 1 + 2


def test_startup_checks_alerts_over_threshold_without_a_modal(service, tmp_path):
    """The packages alert NOTIFIES; it must never open a modal.

    A modal opened on the service thread cannot be closed during shutdown: Kodi's
    application thread is blocked inside CPythonInvoker::stop() waiting for this
    very script, so it can never process the Dialog.Close message a watchdog would
    post. Kodi then kills the script after 5 seconds. Both halves were reproduced
    on the macOS bench 2026-07-20."""
    # Three zips split across TWO subfolders: the old per-folder `count = 0`
    # reset would have reported 1; the total is 3.
    packages = tmp_path / "home" / "addons" / "packages"
    _mktree(
        packages,
        {
            "sub1/a.zip": b"x" * 1024000,
            "sub1/b.zip": b"x" * 1024000,
            "sub2/c.zip": b"x" * 1024000,
        },
    )
    service._rec.settings.update(
        {
            "notify_mode": "false",
            "startup.cache": "false",
            "filesize_alert": "1",
            "filesizethumb_alert": "999999",
        }
    )
    purged = []
    maint_mod = sys.modules["resources.lib.modules.maintenance"]
    orig = maint_mod.purgePackages
    maint_mod.purgePackages = lambda *a, **k: purged.append(True)
    try:
        service._startup_checks()
    finally:
        maint_mod.purgePackages = orig
    assert service._rec.yesno_calls == []
    # Boot never cleans by itself; the user cleans from the menu.
    assert purged == []
    alerts = [b for b in service._rec.builtins if "Notification" in b]
    assert len(alerts) == 1
    # The alert must report the TOTAL zip count across subfolders.
    assert "3 zip files" in alerts[0]


def test_startup_checks_quiet_under_thresholds(service, tmp_path):
    service._rec.settings.update(
        {
            "notify_mode": "false",
            "startup.cache": "false",
            "filesize_alert": "200",
            "filesizethumb_alert": "500",
        }
    )
    service._startup_checks()
    assert service._rec.yesno_calls == []
    assert service._rec.builtins == []


def test_startup_checks_notification_and_autoclean(service, tmp_path):
    service._rec.settings.update(
        {
            "notify_mode": "true",
            "startup.cache": "true",
            "filesize_alert": "200",
            "filesizethumb_alert": "500",
        }
    )
    cleaned = []
    maint_mod = sys.modules["resources.lib.modules.maintenance"]
    orig = maint_mod.clearCache
    maint_mod.clearCache = lambda *a, **k: cleaned.append(True)
    try:
        service._startup_checks()
    finally:
        maint_mod.clearCache = orig
    assert any("Notification" in b for b in service._rec.builtins)
    assert cleaned == [True]


def test_int_setting_falls_back_on_unset(service):
    assert service._int_setting(lambda k: "", "filesize_alert", 200) == 200
    assert service._int_setting(lambda k: "42", "filesize_alert", 200) == 42


def test_startup_checks_thumbnails_alert_branch(service, tmp_path):
    thumbs = tmp_path / "home" / "userdata" / "Thumbnails"
    _mktree(thumbs, {"0/a.jpg": b"x" * (2 * 1024000)})
    service._rec.settings.update(
        {
            "notify_mode": "false",
            "startup.cache": "false",
            "filesize_alert": "999999",
            "filesizethumb_alert": "1",
        }
    )
    wiped = []
    maint_mod = sys.modules["resources.lib.modules.maintenance"]
    orig = maint_mod.deleteThumbnails
    maint_mod.deleteThumbnails = lambda *a, **k: wiped.append(True)
    try:
        service._startup_checks()
    finally:
        maint_mod.deleteThumbnails = orig
    assert service._rec.yesno_calls == []
    assert wiped == []
    alerts = [b for b in service._rec.builtins if "Notification" in b]
    assert len(alerts) == 1
    assert "Images folder" in alerts[0]


def test_wait_kodi_ready_returns_true_when_home_visible(service):
    monitor = types.SimpleNamespace(
        abortRequested=lambda: False, waitForAbort=lambda t: False
    )
    assert service._wait_kodi_ready(monitor) is True


def test_wait_kodi_ready_returns_false_on_abort(service):
    monitor = types.SimpleNamespace(
        abortRequested=lambda: True, waitForAbort=lambda t: True
    )
    assert service._wait_kodi_ready(monitor) is False


def test_wait_kodi_ready_gives_up_after_timeout_without_gui(service, monkeypatch):
    # GUI never comes up: the wait must still return True at the bound (well
    # past any black-screen phase) rather than block the service forever.
    monkeypatch.setattr(sys.modules["xbmc"], "getCondVisibility", lambda s: False)
    monitor = types.SimpleNamespace(
        abortRequested=lambda: False, waitForAbort=lambda t: False
    )
    assert service._wait_kodi_ready(monitor, timeout=4) is True


def test_folder_size_and_count_survives_vanishing_file(service, tmp_path, monkeypatch):
    # A file deleted mid-scan (live Kodi does this) must be skipped, not raised.
    root = tmp_path / "pkgs"
    _mktree(root, {"a.zip": b"12345", "b.zip": b"123"})
    real_getsize = service.os.path.getsize

    def flaky(p):
        if p.endswith("a.zip"):
            raise OSError("gone")
        return real_getsize(p)

    monkeypatch.setattr(service.os.path, "getsize", flaky)
    total, count = service._folder_size_and_count(str(root))
    assert (total, count) == (3, 1)


# ------------------------------------------------- verbose + error branches


def test_public_cleaners_verbose_notify(maint, tmp_path):
    # verbose mode must fire the ui notification for all three cleaners.
    notes = []
    maint.ui.notify = lambda msg, **k: notes.append(msg)
    maint.clearCache()
    maint.purgePackages()
    maint.deleteThumbnails()
    assert notes == [
        "Clean Completed",
        "Clean Packages Completed",
        "Clean Thumbs Completed",
    ]


def _make_pvr_db(path, last_watched_values):
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE channels (idChannel INTEGER PRIMARY KEY, "
        "iLastWatched INTEGER, iLastWatchedGroupId INTEGER)"
    )
    for i, lw in enumerate(last_watched_values, 1):
        con.execute(
            "INSERT INTO channels (idChannel, iLastWatched, iLastWatchedGroupId) "
            "VALUES (?, ?, ?)",
            (i, lw, lw),
        )
    con.commit()
    con.close()


# ------------------------------------------------- _pvr_databases selection
#
# Kodi migrates the PVR DB across versions as TV<N>.db / Radio<N>.db and reads only the
# HIGHEST-numbered one; the older files are stale leftovers. Picking a stale DB would make
# every clear silently no-op against a database Kodi does not read. Existing coverage only
# ever laid down a single TV46.db, so neither the selection nor the Radio half was pinned.


def test_pvr_databases_picks_highest_schema(maint, tmp_path, monkeypatch):
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    for name in ("TV45.db", "TV46.db"):
        (dbdir / name).write_bytes(b"x")
    monkeypatch.setattr(maint, "databasePath", str(dbdir))

    assert [Path(p).name for p in maint._pvr_databases()] == ["TV46.db"]


def test_pvr_databases_sorts_numerically_not_lexically(maint, tmp_path, monkeypatch):
    # The trap the `key=_num` exists for: a plain string sort puts "TV5.db" AFTER "TV46.db",
    # so a lexical sort would hand back the OLD schema.
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    for name in ("TV5.db", "TV46.db"):
        (dbdir / name).write_bytes(b"x")
    monkeypatch.setattr(maint, "databasePath", str(dbdir))

    assert [Path(p).name for p in maint._pvr_databases()] == ["TV46.db"]


def test_pvr_databases_includes_radio_and_tv(maint, tmp_path, monkeypatch):
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    for name in ("TV45.db", "TV46.db", "Radio45.db", "Radio46.db"):
        (dbdir / name).write_bytes(b"x")
    monkeypatch.setattr(maint, "databasePath", str(dbdir))

    assert sorted(Path(p).name for p in maint._pvr_databases()) == [
        "Radio46.db",
        "TV46.db",
    ]


def test_pvr_databases_radio_only(maint, tmp_path, monkeypatch):
    # TV absent must not suppress Radio (the two prefixes are independent).
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    (dbdir / "Radio46.db").write_bytes(b"x")
    monkeypatch.setattr(maint, "databasePath", str(dbdir))

    assert [Path(p).name for p in maint._pvr_databases()] == ["Radio46.db"]


def test_pvr_databases_no_candidates(maint, tmp_path, monkeypatch):
    # No PVR ever configured, and unrelated databases must not be mistaken for PVR ones.
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    (dbdir / "MyVideos131.db").write_bytes(b"x")
    (dbdir / "Textures13.db").write_bytes(b"x")
    monkeypatch.setattr(maint, "databasePath", str(dbdir))

    assert maint._pvr_databases() == []


def test_pvr_databases_missing_directory(maint, tmp_path, monkeypatch):
    # A box with no Database dir at all: glob returns nothing, must not raise.
    monkeypatch.setattr(maint, "databasePath", str(tmp_path / "nope"))

    assert maint._pvr_databases() == []


def test_clear_recent_channels_uses_only_current_schema(maint, tmp_path, monkeypatch):
    # End to end: a stale TV45.db holding recent channels must be left ALONE, and the
    # count/clear must come from TV46.db only.
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    _make_pvr_db(dbdir / "TV45.db", [1700000000, 1700000001])  # stale, must not change
    _make_pvr_db(dbdir / "TV46.db", [1700000000])  # current, 1 recent
    monkeypatch.setattr(maint, "databasePath", str(dbdir))
    monkeypatch.setattr(
        maint,
        "_jsonrpc",
        lambda m, p: (
            {"result": {"value": True}} if m == "Settings.GetSettingValue" else {}
        ),
    )
    monkeypatch.setattr(maint.xbmc, "sleep", lambda ms: None)

    assert maint.clearRecentChannels(mode="silent") == 1

    con = sqlite3.connect(str(dbdir / "TV45.db"))
    stale = con.execute(
        "SELECT COUNT(*) FROM channels WHERE iLastWatched > 0"
    ).fetchone()[0]
    con.close()
    assert stale == 2, "the stale schema DB must never be touched"


def test_clear_recent_channels_resets_only_watched(maint, tmp_path, monkeypatch):
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    _make_pvr_db(dbdir / "TV46.db", [1700000000, 0, 1700000100])  # 2 recent, 1 not
    monkeypatch.setattr(maint, "databasePath", str(dbdir))
    calls = []

    def fake_rpc(method, params):
        calls.append((method, params.get("setting"), params.get("value")))
        if method == "Settings.GetSettingValue":
            return {"result": {"value": True}}  # pvr manager currently on
        return {"result": "OK"}

    monkeypatch.setattr(maint, "_jsonrpc", fake_rpc)
    monkeypatch.setattr(maint.xbmc, "sleep", lambda ms: None)

    cleared = maint.clearRecentChannels(mode="silent")
    assert cleared == 2
    con = sqlite3.connect(str(dbdir / "TV46.db"))
    remaining = con.execute(
        "SELECT COUNT(*) FROM channels WHERE iLastWatched > 0"
    ).fetchone()[0]
    con.close()
    assert remaining == 0, "every recent-watch timestamp must be reset"
    sets = [c for c in calls if c[0] == "Settings.SetSettingValue"]
    assert sets == [
        ("Settings.SetSettingValue", "pvrmanager.enabled", False),
        ("Settings.SetSettingValue", "pvrmanager.enabled", True),
    ], "must disable pvrmanager around the write, then re-enable it (clobber-safe)"


def test_clear_recent_channels_noop_when_none_found(maint, tmp_path, monkeypatch):
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    _make_pvr_db(dbdir / "TV46.db", [0, 0])  # nothing recently played
    monkeypatch.setattr(maint, "databasePath", str(dbdir))
    calls = []
    monkeypatch.setattr(maint, "_jsonrpc", lambda m, p: calls.append(m) or {})
    notes = []
    maint.ui.notify = lambda msg, **k: notes.append(msg)

    cleared = maint.clearRecentChannels(mode="verbose")
    assert cleared == 0
    assert notes == ["No recently played channels"]
    assert calls == [], "must not disable pvrmanager when nothing is found"


def test_clear_recent_channels_verbose_offers_restart(maint, tmp_path, monkeypatch):
    """After clearing (verbose), the user is offered a restart - the home widget
    reads the PVR manager's memory and only updates on a Kodi reload."""
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    _make_pvr_db(dbdir / "TV46.db", [1700000000])
    monkeypatch.setattr(maint, "databasePath", str(dbdir))
    monkeypatch.setattr(
        maint,
        "_jsonrpc",
        lambda m, p: (
            {"result": {"value": True}} if m == "Settings.GetSettingValue" else {}
        ),
    )
    monkeypatch.setattr(maint.xbmc, "sleep", lambda ms: None)
    restarts = []
    maint.ui.ask_restart = lambda status="", **k: restarts.append(status)
    maint.clearRecentChannels(mode="verbose")
    assert len(restarts) == 1, "verbose clear must offer a restart"
    assert "reload" in restarts[0].lower()


def test_clear_all_offers_restart_only_when_channels_cleared(maint, monkeypatch):
    monkeypatch.setattr(maint, "clearCache", lambda mode="verbose": None)
    monkeypatch.setattr(maint, "purgePackages", lambda mode="verbose": None)
    monkeypatch.setattr(maint, "deleteThumbnails", lambda mode="verbose": None)
    restarts, notes = [], []
    maint.ui.ask_restart = lambda status="", **k: restarts.append(status)
    maint.ui.notify = lambda msg, **k: notes.append(msg)

    # channels cleared -> restart offered, no plain notify
    monkeypatch.setattr(maint, "clearRecentChannels", lambda mode="verbose": 3)
    maint.clearAll()
    assert len(restarts) == 1 and notes == []

    # nothing cleared -> plain "All Cleaned", no restart
    restarts.clear()
    monkeypatch.setattr(maint, "clearRecentChannels", lambda mode="verbose": 0)
    maint.clearAll()
    assert restarts == [] and notes == ["All Cleaned"]


def test_clear_all_runs_every_cleaner_silently_then_notifies(maint, monkeypatch):
    ran = []
    monkeypatch.setattr(
        maint, "clearCache", lambda mode="verbose": ran.append(("cache", mode))
    )
    monkeypatch.setattr(
        maint, "purgePackages", lambda mode="verbose": ran.append(("pkg", mode))
    )
    monkeypatch.setattr(
        maint, "deleteThumbnails", lambda mode="verbose": ran.append(("thumb", mode))
    )
    monkeypatch.setattr(
        maint, "clearRecentChannels", lambda mode="verbose": ran.append(("chan", mode))
    )
    notes = []
    maint.ui.notify = lambda msg, **k: notes.append(msg)

    maint.clearAll()
    assert [r[0] for r in ran] == ["cache", "pkg", "thumb", "chan"]
    assert all(r[1] == "silent" for r in ran), "sub-cleaners must run silently"
    assert notes == ["All Cleaned"]


def test_clean_tree_swallows_rmtree_and_unlink_errors(maint, tmp_path, monkeypatch):
    root = tmp_path / "junk"
    _mktree(root, {"a.txt": b"x", "sub/b.txt": b"y"})

    def boom(*a, **k):
        raise OSError("locked")

    monkeypatch.setattr(maint.shutil, "rmtree", boom)
    monkeypatch.setattr(maint.os, "unlink", boom)
    maint._clean_tree(str(root))  # must not raise
    assert (root / "a.txt").exists() and (root / "sub" / "b.txt").exists()


def test_determineNextMaintenance_schedules_future_timestamp(maint):
    maint._rec.settings.update({"autoCleanDays": "1", "autoCleanHour": "3"})
    maint.determineNextMaintenance()
    scheduled = maint.getNextMaintenance()
    import time as _time

    assert scheduled > _time.time()


def test_determineNextMaintenance_unset_hour_does_not_crash(maint):
    """REGRESSION: days set, hour MISSING - the crash the `is None` guards never caught.

    Kodi's Addon().getSetting NEVER returns None for an absent/blank setting; it returns
    "". So `if autoCleanHour is None` is dead code and `int("")` raises ValueError. Both
    test fakes model the real "" correctly, but every existing test set both keys, so
    nothing exercised it. Reachability is narrow (settings.xml ships defaults 0/4, so a
    clean install reads "0"/"4") - it needs a degraded profile: a renamed/removed setting
    id or a corrupt profile settings.xml. But the callers are service.py:29,33,459, i.e.
    service STARTUP, so an uncaught ValueError takes down the whole scheduler thread for
    the session. Same failure mode the author already fixed 30 lines below in
    getNextMaintenance with `except (TypeError, ValueError)`.
    """
    maint._rec.settings.clear()
    maint._rec.settings["autoCleanDays"] = "1"  # no autoCleanHour at all

    maint.determineNextMaintenance()  # must not raise

    import time as _time

    scheduled = maint.getNextMaintenance()
    assert scheduled > _time.time(), (
        "a missing hour must fall back to a usable schedule, not abort the service"
    )
    # The fallback hour is NOT arbitrary: settings.xml declares <default>4</default> and
    # Kodi's settings UI shows that default for an absent setting. Falling back to
    # midnight would run maintenance at an hour the user was never shown, plausibly
    # while they are still watching.
    assert _time.localtime(scheduled).tm_hour == maint.DEFAULT_AUTOCLEAN_HOUR == 4, (
        "an unset hour must use the hour settings.xml declares, not midnight"
    )


def test_determineNextMaintenance_unset_days_means_no_schedule(maint):
    # Nothing configured at all -> int("") on the FIRST read. Must degrade to "no
    # schedule" (0), which is what the `is None` branch was trying to express.
    maint._rec.settings.clear()

    maint.determineNextMaintenance()  # must not raise

    assert maint.getNextMaintenance() == 0


def test_determineNextMaintenance_garbage_settings_do_not_crash(maint):
    # A corrupt profile settings.xml can hold non-numeric junk, not just "".
    maint._rec.settings.clear()
    maint._rec.settings.update({"autoCleanDays": "banana", "autoCleanHour": "elephant"})

    maint.determineNextMaintenance()  # must not raise

    assert maint.getNextMaintenance() == 0


def test_determineNextMaintenance_garbage_hour_still_schedules(maint):
    # Days is valid, hour is junk: the schedule must still be set (hour degrades to the
    # declared default) rather than losing the whole schedule to one bad field.
    maint._rec.settings.clear()
    maint._rec.settings.update({"autoCleanDays": "2", "autoCleanHour": "!!"})

    maint.determineNextMaintenance()

    import time as _time

    scheduled = maint.getNextMaintenance()
    assert scheduled > _time.time()
    assert _time.localtime(scheduled).tm_hour == maint.DEFAULT_AUTOCLEAN_HOUR == 4, (
        "a junk hour must degrade to the declared default, not midnight"
    )


def test_service_monitor_sets_schedule_on_init_and_settings_change(service):
    # Monitor.__init__ writes the schedule property BEFORE the loop starts -
    # this is what makes the plugin-side getNextMaintenance read safe once the
    # service is up; onSettingsChanged recomputes it.
    service._rec.settings.update({"autoCleanDays": "1", "autoCleanHour": "3"})
    monitor = service.Monitor()
    first = service._rec.window_props["ezmaintenance.nextMaintenanceTime"]
    assert int(first) > 0
    service._rec.settings["autoCleanDays"] = "0"
    monitor.onSettingsChanged()
    assert service._rec.window_props["ezmaintenance.nextMaintenanceTime"] == "0"


def test_wait_kodi_ready_survives_condvisibility_raising(service, monkeypatch):
    # getCondVisibility blowing up must not kill the wait - it retries until
    # the bound, then lets the service proceed.
    def boom(s):
        raise RuntimeError("gui not ready")

    monkeypatch.setattr(sys.modules["xbmc"], "getCondVisibility", boom)
    monitor = types.SimpleNamespace(
        abortRequested=lambda: False, waitForAbort=lambda t: False
    )
    assert service._wait_kodi_ready(monitor, timeout=4) is True


# ------------------------------------------------------------- plugin routing


def test_plugin_root_menu_renders(monkeypatch, tmp_path):
    # Imports default.py exactly as Kodi does (plugin://, handle, empty qs).
    # Pins the whole import chain (control, ui, maintenance) plus CATEGORIES().
    rec = _Recorder()
    _install_fakes(monkeypatch, tmp_path, rec)
    _import_plugin(
        monkeypatch, tmp_path, rec, ["plugin://script.ezmaintenanceplusplus/", "7", ""]
    )
    # 9 menu rows + the non-clickable version row.
    # (Went 8 -> 9 in 2026.07.13.1 when "Set up this box" was added; 9 -> 10 on
    # 2026-07-16 when the "Tools" folder landed: stale-key purge + backup verify;
    # 10 -> 11 on 2026-07-19 when "Device Name" landed beside "Video Cache Buffer";
    # 11 -> 9 on 2026-07-19 when BOTH moved out: "Tools" was deleted outright once
    # it held a single backup action, which went to Backup/Restore, and "Device
    # Name" moved into "Set up this box" where naming a box belongs.)
    assert len(rec.dir_items) == 10
    assert rec.end_dirs == [True]
    # Parse the action out of each url rather than substring-matching it: "device_name"
    # is a prefix of "device_nameXX", so a substring test passes against a renamed or
    # misspelled action that routes nowhere. (Caught by mutation, 2026-07-19.)
    from urllib.parse import parse_qs, urlparse

    actions = set()
    for url in rec.dir_items:
        actions.update(parse_qs(urlparse(url).query).get("action", []))
    assert "adv_settings" in actions, (
        "the Video Cache Buffer menu item is the on-demand replacement for the "
        "deleted post-restore buffer prompt"
    )
    assert "device_name" not in actions, (
        "Device Name moved into 'Set up this box' on 2026-07-19; it must not also "
        "linger at the top level as a second way in"
    )
    assert "tools" not in actions, (
        "the Tools category was deleted on 2026-07-19, not hidden and not emptied"
    )
    assert "box_setup" in actions, (
        "'Set up this box' is now the only route to Device Name - if this row ever "
        "disappears, the rename capability goes with it"
    )


def test_plugin_box_setup_submenu_offers_device_name_first(monkeypatch, tmp_path):
    """Device Name lives in "Set up this box", at the TOP.

    Moved there 2026-07-19. Naming a box is what that folder is for, and since
    2026.07.19.4 deleted the post-restore rename prompt (restore now PRESERVES the
    existing name instead of asking), this item is the ONLY deliberate way to change
    a name - so it must be reachable and it must not be buried under the bulk
    setup actions."""
    rec = _Recorder()
    _install_fakes(monkeypatch, tmp_path, rec)
    _import_plugin(
        monkeypatch,
        tmp_path,
        rec,
        ["plugin://script.ezmaintenanceplusplus/", "7", "?action=box_setup"],
    )
    from urllib.parse import parse_qs, urlparse

    ordered = []
    for url in rec.dir_items:
        ordered.extend(parse_qs(urlparse(url).query).get("action", []))
    assert "device_name" in ordered, (
        "Device Name must be reachable from 'Set up this box' - it left the top "
        "level, so losing it here loses the rename capability entirely"
    )
    assert ordered[0] == "device_name", (
        "Device Name must come FIRST, above 'Set up everything': naming is the first "
        "thing an owner does with a new box and the item most wanted on its own. "
        "Order was %r" % (ordered,)
    )
    assert "setup_all_box" in ordered
    assert rec.end_dirs == [True]


def test_plugin_maintenance_submenu_renders_without_service(monkeypatch, tmp_path):
    # The plugin process reads getNextMaintenance() BEFORE the service may have
    # set the window property - must render the cleaners, not crash.
    rec = _Recorder()
    _install_fakes(monkeypatch, tmp_path, rec)
    _import_plugin(
        monkeypatch,
        tmp_path,
        rec,
        ["plugin://script.ezmaintenanceplusplus/", "7", "?action=maintenance"],
    )
    # Clear All / Cache / Packages / Thumbnails / Recently Played Channels
    assert len(rec.dir_items) == 5
    actions = [i for i in rec.dir_items]
    assert any("action=clear_all" in a for a in actions)
    assert any("action=clear_channels" in a for a in actions)
    assert rec.end_dirs == [True]
