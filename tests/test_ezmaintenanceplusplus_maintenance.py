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


def test_startup_checks_prompts_and_purges_over_threshold(service, tmp_path):
    # Three zips split across TWO subfolders: the old per-folder `count = 0`
    # reset would have reported 1 in the prompt; the total is 3.
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
    service._rec.yesno_answer = 1
    purged = []
    maint_mod = sys.modules["resources.lib.modules.maintenance"]
    orig = maint_mod.purgePackages
    maint_mod.purgePackages = lambda *a, **k: purged.append(True)
    try:
        service._startup_checks()
    finally:
        maint_mod.purgePackages = orig
    assert len(service._rec.yesno_calls) == 1
    assert purged == [True]
    # The prompt must report the TOTAL zip count across subfolders.
    msg = service._rec.yesno_calls[0][1]
    assert "3[/COLOR] zip files" in msg


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


def test_startup_checks_thumbnails_prompt_branch(service, tmp_path):
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
    service._rec.yesno_answer = 1
    wiped = []
    maint_mod = sys.modules["resources.lib.modules.maintenance"]
    orig = maint_mod.deleteThumbnails
    maint_mod.deleteThumbnails = lambda *a, **k: wiped.append(True)
    try:
        service._startup_checks()
    finally:
        maint_mod.deleteThumbnails = orig
    assert len(service._rec.yesno_calls) == 1
    assert wiped == [True]


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


def test_maybe_prompt_after_restore_no_marker_is_quiet(service):
    # No pending-restore marker: must return without prompting or waiting.
    monitor = types.SimpleNamespace(
        abortRequested=lambda: False, waitForAbort=lambda t: False
    )
    service._maybe_prompt_after_restore(monitor)
    assert service._rec.yesno_calls == []


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


def test_maybe_prompt_after_restore_pending_runs_tuneup(service):
    # service imports tools lazily inside the function; pre-import it so the
    # test can stub the two entry points on the same module object.
    tools_mod = importlib.import_module("resources.lib.modules.tools")
    ran = []
    orig_pending = tools_mod.buffer_prompt_pending
    orig_prompt = tools_mod.prompt_after_restore
    tools_mod.buffer_prompt_pending = lambda: True
    tools_mod.prompt_after_restore = lambda: ran.append(True)
    try:
        monitor = types.SimpleNamespace(
            abortRequested=lambda: False, waitForAbort=lambda t: False
        )
        service._maybe_prompt_after_restore(monitor)
    finally:
        tools_mod.buffer_prompt_pending = orig_pending
        tools_mod.prompt_after_restore = orig_prompt
    assert ran == [True]


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
    # (Went 8 -> 9 in 2026.07.13.1 when "Set up this box" was added.)
    assert len(rec.dir_items) == 10
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
