"""Coverage for the two owner-facing tool actions in default.py.

Tools menu actions added 2026-07-16:
  * "Purge stale tvOS keys" (action purge_stale_tvos_keys): tvOS-gated, hasattr-guarded
    call to nsud.purge_stale_keys(control.USERDATA), reporting the
    (materialized, purged, kept, failed) counts.
  * "Verify backup archive" (action verify_backup_archive): the restore.path picker,
    then a READ-ONLY zip analysis (entry count, backup_manifest.json presence, the
    manifest failed list, IPTV addon_data presence, top-level composition). It never
    extracts and never restores.

The real default.py is imported against fully faked Kodi modules and stubbed sibling
modules (control/ui/maintenance), following the pattern of
test_ezmaintenanceplusplus_tools.py. default.py routes at import time, so the fixture
imports it with a no-op action; each test then drives the factored functions directly.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).parent.parent / "script.ezmaintenanceplusplus"
DEFAULT_PY = ADDON_ROOT / "default.py"


# --------------------------------------------------------------------------- #
# Fixture: import the real default.py under fakes
# --------------------------------------------------------------------------- #
@pytest.fixture
def dmod(monkeypatch):
    """Import default.py fresh under fake Kodi + stub sibling modules.

    Returns a namespace with the module and every scriptable/recording stub:
      mod, xbmc (tvos flag), xbmcvfs (listdir_result), xbmcplugin (items),
      control (settings/select_result/infoDialog_calls/openSettings_calls),
      ui (done_calls/error_calls/confirm_calls/confirm_result/copy_calls/copy_result),
      set_nsud(module_or_None) to install/replace the lazily imported nsud.
    """
    ns = types.SimpleNamespace()

    # ---- xbmc ---- #
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGERROR = 3
    xbmc.log = lambda *a, **k: None
    xbmc.sleep = lambda *a, **k: None
    xbmc.translatePath = lambda p: p
    xbmc._tvos = False
    xbmc.getCondVisibility = lambda cond: bool(xbmc._tvos) if "TVOS" in cond else False
    xbmc.executebuiltin = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "xbmc", xbmc)

    # ---- xbmcgui ---- #
    xbmcgui = types.ModuleType("xbmcgui")

    class _ListItem:
        def __init__(self, name, **k):
            self.name = name

        def setArt(self, *a, **k):
            pass

        def setInfo(self, *a, **k):
            pass

        def setProperty(self, *a, **k):
            pass

    xbmcgui.ListItem = _ListItem
    monkeypatch.setitem(sys.modules, "xbmcgui", xbmcgui)

    # ---- xbmcplugin ---- #
    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.items = []  # (url, name, isFolder)

    def _add_item(handle=0, url="", listitem=None, isFolder=False):
        xbmcplugin.items.append((url, getattr(listitem, "name", ""), isFolder))
        return True

    xbmcplugin.addDirectoryItem = _add_item
    xbmcplugin.endOfDirectory = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "xbmcplugin", xbmcplugin)

    # ---- xbmcvfs ---- #
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs._temp_map = {}  # special:// path -> real path
    xbmcvfs.translatePath = lambda p: xbmcvfs._temp_map.get(p, p)
    xbmcvfs.listdir_result = ([], [])

    def _listdir(path):
        return xbmcvfs.listdir_result

    xbmcvfs.listdir = _listdir
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)

    # ---- stub package tree ---- #
    for pkg in ("resources", "resources.lib", "resources.lib.modules"):
        m = types.ModuleType(pkg)
        m.__path__ = []
        monkeypatch.setitem(sys.modules, pkg, m)
    pkg_mod = sys.modules["resources.lib.modules"]

    def _submodule(name, mod):
        monkeypatch.setitem(sys.modules, "resources.lib.modules.%s" % name, mod)
        setattr(pkg_mod, name, mod)

    # ---- control stub ---- #
    control = types.ModuleType("resources.lib.modules.control")
    control.USERDATA = "/fake/userdata/"
    control._settings = {}
    control.setting = lambda key: control._settings.get(key, "")
    control.addonFanart = lambda: "fanart.jpg"
    control.addonIcon = lambda: "icon.png"
    control.addonInfo = lambda key: {"version": "9.9.9"}.get(key, "")
    control.select_result = -1
    control.select_calls = []

    def _select(options, heading=None):
        control.select_calls.append(list(options))
        return control.select_result

    control.selectDialog = _select
    control.infoDialog_calls = []
    control.infoDialog = lambda msg, *a, **k: control.infoDialog_calls.append(msg)
    control.openSettings_calls = []
    control.openSettings = lambda *a, **k: control.openSettings_calls.append(a)
    _submodule("control", control)

    # ---- ui stub ---- #
    ui = types.ModuleType("resources.lib.modules.ui")
    ui.HEADING = "EZ Maintenance++"
    ui.COPY_OK = "ok"
    ui.COPY_FAILED = "failed"
    ui.COPY_CANCELLED = "cancelled"
    ui.done_calls = []
    ui.error_calls = []
    ui.confirm_calls = []
    ui.confirm_result = True
    ui.copy_calls = []
    ui.copy_result = "ok"
    ui.copy_payload_src = None  # real file whose bytes the fake copy stages

    def _done(message, heading=None):
        ui.done_calls.append(message)

    def _error(message, heading=None):
        ui.error_calls.append(message)

    def _confirm(message, heading=None, yeslabel="", nolabel=""):
        ui.confirm_calls.append((message, yeslabel, nolabel))
        return ui.confirm_result

    class _Progress:
        def __init__(self, message="", heading=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cancelled(self):
            return False

    def _copy_with_progress(src, dst, progress=None):
        ui.copy_calls.append((src, dst))
        if ui.copy_result == ui.COPY_OK and ui.copy_payload_src is not None:
            real_dst = xbmcvfs.translatePath(dst)
            Path(real_dst).write_bytes(Path(ui.copy_payload_src).read_bytes())
        return ui.copy_result

    ui.done = _done
    ui.error = _error
    ui.confirm = _confirm
    ui.confirm_wipe = lambda *a, **k: False
    ui.Progress = _Progress
    ui.copy_with_progress = _copy_with_progress
    ui.restart = lambda: None
    _submodule("ui", ui)

    # ---- maintenance / backtothefuture stubs ---- #
    maintenance = types.ModuleType("resources.lib.modules.maintenance")
    maintenance.getNextMaintenance = lambda: 0
    _submodule("maintenance", maintenance)

    b2f = types.ModuleType("resources.lib.modules.backtothefuture")
    b2f.PY2 = False
    b2f.unicode = str
    _submodule("backtothefuture", b2f)

    def set_nsud(module):
        """Install (or replace) the lazily imported nsud module; None removes it."""
        if module is None:
            monkeypatch.delitem(
                sys.modules, "resources.lib.modules.nsud", raising=False
            )
            if hasattr(pkg_mod, "nsud"):
                delattr(pkg_mod, "nsud")
        else:
            _submodule("nsud", module)

    # ---- import default.py with a no-op action ---- #
    monkeypatch.setattr(
        sys, "argv", ["plugin://script.ezmaintenanceplusplus/", "1", "?action=noop"]
    )
    monkeypatch.delitem(sys.modules, "ezm_default_under_test", raising=False)
    spec = importlib.util.spec_from_file_location("ezm_default_under_test", DEFAULT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ezm_default_under_test"] = mod
    spec.loader.exec_module(mod)

    ns.mod = mod
    ns.xbmc = xbmc
    ns.xbmcvfs = xbmcvfs
    ns.xbmcplugin = xbmcplugin
    ns.control = control
    ns.ui = ui
    ns.set_nsud = set_nsud
    return ns


def _make_zip(path, members, manifest=None):
    """Write a zip at `path` with `members` (name -> bytes/str). A `manifest` dict is
    added as backup_manifest.json at the archive root."""
    with zipfile.ZipFile(str(path), "w") as zf:
        for name, payload in members.items():
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            zf.writestr(name, payload)
        if manifest is not None:
            zf.writestr("backup_manifest.json", json.dumps(manifest))
    return str(path)


def _fake_nsud(result=(0, 0, 0, 0), with_purge=True, exc=None):
    m = types.ModuleType("resources.lib.modules.nsud")
    m.calls = []
    if with_purge:

        def purge_stale_keys(userdata):
            m.calls.append(userdata)
            if exc is not None:
                raise exc
            return result

        m.purge_stale_keys = purge_stale_keys
    return m


# --------------------------------------------------------------------------- #
# Menu wiring
# --------------------------------------------------------------------------- #
def test_categories_menu_has_tools_folder(dmod):
    dmod.mod.CATEGORIES()
    tools = [
        (url, folder) for url, name, folder in dmod.xbmcplugin.items if name == "Tools"
    ]
    assert len(tools) == 1
    url, is_folder = tools[0]
    assert "action=tools" in url
    assert is_folder is True


def test_tools_menu_lists_both_actions(dmod):
    dmod.mod.TOOLS()
    urls = [url for url, _name, _folder in dmod.xbmcplugin.items]
    assert len(urls) == 2
    assert any("action=purge_stale_tvos_keys" in u for u in urls)
    assert any("action=verify_backup_archive" in u for u in urls)
    names = [name for _url, name, _folder in dmod.xbmcplugin.items]
    assert "Purge stale tvOS keys" in names
    assert "Verify backup archive" in names


# --------------------------------------------------------------------------- #
# Purge stale tvOS keys
# --------------------------------------------------------------------------- #
def test_purge_on_non_tvos_explains_and_never_touches_nsud(dmod):
    dmod.xbmc._tvos = False
    nsud = _fake_nsud(result=(9, 9, 9, 9))
    dmod.set_nsud(nsud)
    dmod.mod.PURGE_STALE_TVOS_KEYS()
    assert nsud.calls == []
    assert len(dmod.ui.done_calls) == 1
    assert "Apple TV" in dmod.ui.done_calls[0]
    assert dmod.ui.error_calls == []
    assert dmod.ui.confirm_calls == []  # not even prompted off-tvOS


def test_purge_hasattr_fallback_when_build_lacks_purge(dmod):
    dmod.xbmc._tvos = True
    dmod.set_nsud(_fake_nsud(with_purge=False))
    dmod.mod.PURGE_STALE_TVOS_KEYS()
    assert len(dmod.ui.error_calls) == 1
    assert "not available in this build" in dmod.ui.error_calls[0]
    assert dmod.ui.done_calls == []


def test_purge_on_tvos_reports_all_four_counts(dmod):
    dmod.xbmc._tvos = True
    nsud = _fake_nsud(result=(3, 2, 1, 0))
    dmod.set_nsud(nsud)
    dmod.ui.confirm_result = True
    dmod.mod.PURGE_STALE_TVOS_KEYS()
    # called with the userdata root, exactly once
    assert nsud.calls == [dmod.control.USERDATA]
    assert len(dmod.ui.done_calls) == 1
    msg = dmod.ui.done_calls[0]
    for line in ("Materialized: 3", "Purged: 2", "Kept: 1", "Failed: 0"):
        assert line in msg
    assert dmod.ui.error_calls == []


def test_purge_declined_confirm_never_calls_nsud(dmod):
    dmod.xbmc._tvos = True
    nsud = _fake_nsud(result=(1, 1, 1, 1))
    dmod.set_nsud(nsud)
    dmod.ui.confirm_result = False
    dmod.mod.PURGE_STALE_TVOS_KEYS()
    assert nsud.calls == []
    assert dmod.ui.done_calls == []
    assert dmod.ui.error_calls == []


def test_purge_exception_reports_error_not_crash(dmod):
    dmod.xbmc._tvos = True
    dmod.set_nsud(_fake_nsud(exc=RuntimeError("plist unavailable")))
    dmod.mod.PURGE_STALE_TVOS_KEYS()
    assert len(dmod.ui.error_calls) == 1
    assert "plist unavailable" in dmod.ui.error_calls[0]
    assert dmod.ui.done_calls == []


def test_summarize_purge_result_shapes(dmod):
    f = dmod.mod.summarize_purge_result
    assert f((3, 2, 1, 0)) == "Materialized: 3\nPurged: 2\nKept: 1\nFailed: 0"
    # lists count by len(); mixed int/list accepted
    assert f((["a", "b"], ["a"], [], 1)) == (
        "Materialized: 2\nPurged: 1\nKept: 0\nFailed: 1"
    )
    assert f({"materialized": 4, "purged": 3, "kept": 2, "failed": 1}) == (
        "Materialized: 4\nPurged: 3\nKept: 2\nFailed: 1"
    )
    # unrecognized shapes -> None (caller shows the raw value)
    assert f(None) is None
    assert f((1, 2, 3)) is None
    assert f("done") is None
    assert f({"purged": 1}) is None
    assert f((True, 1, 2, 3)) is None
    assert f((object(), 1, 2, 3)) is None


# --------------------------------------------------------------------------- #
# analyze_backup_zip - the pure, read-only inspection
# --------------------------------------------------------------------------- #
def test_analyze_manifest_present_with_failed_list(dmod, tmp_path):
    zp = _make_zip(
        tmp_path / "b.zip",
        {
            "userdata/guisettings.xml": "<settings/>",
            "userdata/addon_data/pvr.iptvsimple/settings.xml": "<settings/>",
            "addons/plugin.video.x/addon.xml": "<addon/>",
            "media/splash.png": b"\x89PNG",
            "stray.txt": "x",
        },
        manifest={
            "created": "2026-07-16",
            "source_os": "tvOS",
            "entries": 5,
            "failed": ["userdata/keymaps/gen.xml", "userdata/rsstranslator.xml"],
        },
    )
    r = dmod.mod.analyze_backup_zip(zp)
    assert r["total_entries"] == 6  # 5 members + the manifest itself
    assert r["manifest_present"] is True
    assert r["manifest_failed"] == [
        "userdata/keymaps/gen.xml",
        "userdata/rsstranslator.xml",
    ]
    assert r["iptv_present"] is True
    assert r["composition"] == {"userdata": 2, "addons": 1, "media": 1, "other": 2}


def test_analyze_manifest_absent(dmod, tmp_path):
    zp = _make_zip(tmp_path / "b.zip", {"userdata/guisettings.xml": "<s/>"})
    r = dmod.mod.analyze_backup_zip(zp)
    assert r["manifest_present"] is False
    assert r["manifest_failed"] == []


def test_analyze_manifest_present_empty_failed(dmod, tmp_path):
    zp = _make_zip(
        tmp_path / "b.zip",
        {"userdata/guisettings.xml": "<s/>"},
        manifest={"created": "x", "source_os": "android", "entries": 1, "failed": []},
    )
    r = dmod.mod.analyze_backup_zip(zp)
    assert r["manifest_present"] is True
    assert r["manifest_failed"] == []


def test_analyze_corrupt_manifest_json_does_not_crash(dmod, tmp_path):
    zp = _make_zip(
        tmp_path / "b.zip", {"backup_manifest.json": "{not json", "userdata/a": "x"}
    )
    r = dmod.mod.analyze_backup_zip(zp)
    assert r["manifest_present"] is True
    assert r["manifest_failed"] == []


def test_analyze_iptv_absent(dmod, tmp_path):
    zp = _make_zip(
        tmp_path / "b.zip",
        {
            "userdata/addon_data/plugin.video.x/settings.xml": "<s/>",
            "addons/pvr.iptvsimple/addon.xml": "<a/>",  # the ADD-ON, not its data
        },
    )
    r = dmod.mod.analyze_backup_zip(zp)
    assert r["iptv_present"] is False


def test_analyze_iptv_detected_in_userdata_anchored_zip(dmod, tmp_path):
    # A userdata-anchored backup carries addon_data/ at the archive root.
    zp = _make_zip(
        tmp_path / "b.zip",
        {"addon_data/pvr.iptvsimple/instance-settings-1.xml": "<s/>"},
    )
    r = dmod.mod.analyze_backup_zip(zp)
    assert r["iptv_present"] is True
    assert r["composition"]["other"] == 1  # addon_data is not a home-level bucket


def test_analyze_not_a_zip_raises(dmod, tmp_path):
    bad = tmp_path / "not_a_zip.zip"
    bad.write_bytes(b"this is not a zip archive")
    with pytest.raises(Exception):
        dmod.mod.analyze_backup_zip(str(bad))


def test_format_backup_report_lines(dmod):
    report = {
        "total_entries": 12,
        "manifest_present": True,
        "manifest_failed": ["a", "b", "c", "d", "e", "f", "g"],
        "iptv_present": True,
        "composition": {"userdata": 7, "addons": 3, "media": 1, "other": 1},
    }
    text = dmod.mod.format_backup_report(report, "kodi_full.zip")
    assert "Backup archive: kodi_full.zip" in text
    assert "Total entries: 12" in text
    assert "Manifest (backup_manifest.json): present" in text
    assert "Manifest failed items (7): a, b, c, d, e, and 2 more" in text
    assert "IPTV (pvr.iptvsimple) data: yes" in text
    assert "Top level: userdata=7, addons=3, media=1, other=1" in text
    # missing manifest renders loudly
    report["manifest_present"] = False
    report["manifest_failed"] = []
    report["iptv_present"] = False
    text = dmod.mod.format_backup_report(report)
    assert "Manifest (backup_manifest.json): MISSING" in text
    assert "IPTV (pvr.iptvsimple) data: no" in text
    assert "failed items: none" not in text  # no manifest, no failed line


# --------------------------------------------------------------------------- #
# VERIFY_BACKUP_ARCHIVE - the picker flow
# --------------------------------------------------------------------------- #
def test_verify_reports_on_local_zip(dmod, tmp_path):
    zp = _make_zip(
        tmp_path / "kodi_full.zip",
        {"userdata/guisettings.xml": "<s/>"},
        manifest={"created": "x", "source_os": "tvOS", "entries": 1, "failed": []},
    )
    dmod.control._settings["restore.path"] = str(tmp_path)
    dmod.xbmcvfs.listdir_result = ([], ["kodi_full.zip", "notes.txt"])
    dmod.control.select_result = 0
    dmod.mod.VERIFY_BACKUP_ARCHIVE()
    # only .zip files were offered
    assert dmod.control.select_calls == [["kodi_full.zip"]]
    assert len(dmod.ui.done_calls) == 1
    msg = dmod.ui.done_calls[0]
    assert "Backup archive: kodi_full.zip" in msg
    assert "Total entries: 2" in msg
    assert "Manifest (backup_manifest.json): present" in msg
    # read-only: the picked zip still exists untouched
    assert Path(zp).exists()


def test_verify_remote_zip_staged_and_cleaned(dmod, tmp_path):
    src_zip = _make_zip(
        tmp_path / "payload.zip",
        {"addon_data/pvr.iptvsimple/settings.xml": "<s/>"},
    )
    staged = tmp_path / "staged_verify.zip"
    dmod.control._settings["restore.path"] = "nfs://mini/KodiShare/backups"
    dmod.xbmcvfs.listdir_result = ([], ["kodi_full.zip"])
    dmod.xbmcvfs._temp_map["special://temp/ezmpp_verify_kodi_full.zip"] = str(staged)
    dmod.control.select_result = 0
    dmod.ui.copy_payload_src = src_zip
    dmod.mod.VERIFY_BACKUP_ARCHIVE()
    # fetched over VFS from the share, into the temp sidecar
    assert dmod.ui.copy_calls == [
        (
            "nfs://mini/KodiShare/backups/kodi_full.zip",
            "special://temp/ezmpp_verify_kodi_full.zip",
        )
    ]
    assert len(dmod.ui.done_calls) == 1
    assert "IPTV (pvr.iptvsimple) data: yes" in dmod.ui.done_calls[0]
    # the staged temp copy is removed after analysis
    assert not staged.exists()


def test_verify_remote_fetch_cancel_is_silent(dmod):
    dmod.control._settings["restore.path"] = "nfs://mini/KodiShare/backups"
    dmod.xbmcvfs.listdir_result = ([], ["kodi_full.zip"])
    dmod.control.select_result = 0
    dmod.ui.copy_result = dmod.ui.COPY_CANCELLED
    dmod.mod.VERIFY_BACKUP_ARCHIVE()
    assert dmod.ui.done_calls == []
    assert dmod.ui.error_calls == []


def test_verify_without_restore_path_routes_to_settings(dmod):
    dmod.mod.VERIFY_BACKUP_ARCHIVE()
    assert dmod.control.infoDialog_calls == ["Please Setup a Zip Files Location first"]
    assert len(dmod.control.openSettings_calls) == 1
    assert dmod.ui.done_calls == []


def test_verify_no_zips_in_folder_reports_error(dmod, tmp_path):
    dmod.control._settings["restore.path"] = str(tmp_path)
    dmod.xbmcvfs.listdir_result = ([], ["notes.txt"])
    dmod.mod.VERIFY_BACKUP_ARCHIVE()
    assert len(dmod.ui.error_calls) == 1
    assert "No backup zips found" in dmod.ui.error_calls[0]


def test_verify_picker_cancel_does_nothing(dmod, tmp_path):
    dmod.control._settings["restore.path"] = str(tmp_path)
    dmod.xbmcvfs.listdir_result = ([], ["kodi_full.zip"])
    dmod.control.select_result = -1
    dmod.mod.VERIFY_BACKUP_ARCHIVE()
    assert dmod.ui.done_calls == []
    assert dmod.ui.error_calls == []


def test_verify_corrupt_zip_reports_error(dmod, tmp_path):
    (tmp_path / "kodi_full.zip").write_bytes(b"not a zip at all")
    dmod.control._settings["restore.path"] = str(tmp_path)
    dmod.xbmcvfs.listdir_result = ([], ["kodi_full.zip"])
    dmod.control.select_result = 0
    dmod.mod.VERIFY_BACKUP_ARCHIVE()
    assert len(dmod.ui.error_calls) == 1
    assert "Could not read that zip" in dmod.ui.error_calls[0]
    assert dmod.ui.done_calls == []


def test_freshstart_wipe_runs_and_restarts_not_false_failed(dmod, monkeypatch):
    """Regression (QA 2026-07-17): onetap._wipe changed from a 3-tuple to a 4-tuple;
    FRESHSTART still unpacked 3, so it WIPED the box then raised on the unpack, was
    swallowed, and falsely told the user 'the wipe did not run' WITHOUT restarting -
    a wiped box left stranded. Let the wipe run and assert honest completion + restart."""
    import sys
    import types as _t

    onetap = _t.ModuleType("resources.lib.modules.onetap")
    wiped = {"v": False}

    def _wipe(home, excludes, keep=None, progress=None):
        wiped["v"] = True
        return (5, 2, 0, [])  # (files, keys, failed, leftovers) - the new 4-tuple

    onetap._wipe = _wipe
    onetap._wipe_excludes = lambda: set()
    onetap.keep_addon_db = lambda: set()
    monkeypatch.setitem(sys.modules, "resources.lib.modules.onetap", onetap)
    setattr(sys.modules["resources.lib.modules"], "onetap", onetap)

    dmod.ui.confirm_wipe = lambda *a, **k: True
    restarts = []
    dmod.ui.restart = lambda: restarts.append(True)

    dmod.mod.FRESHSTART()

    assert wiped["v"] is True, "the wipe must actually run"
    assert not any("FAILED" in m for m in dmod.ui.done_calls), dmod.ui.done_calls
    assert restarts == [True], "a wiped box MUST be driven to restart"
