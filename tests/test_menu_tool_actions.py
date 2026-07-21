"""Coverage for the owner-facing tool actions in default.py.

The owner-facing tool action is:
  * "Verify backup archive" (action verify_backup_archive): the restore.path picker,
    then a READ-ONLY zip analysis (entry count, backup_manifest.json presence, the
    manifest failed list, IPTV addon_data presence, top-level composition). It never
    extracts and never restores. It is reached from the Backup/Restore select dialog
    (its third entry); the Tools category that used to host it is gone.

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
    xbmc.executebuiltin = lambda *a, **k: None

    # Monitor.waitForAbort is Kodi's shutdown signal. The looping Backup/Restore menu
    # consults it because `Quit` is ASYNC: the script outlives it, so the menu must
    # not open a modal into a teardown. `_abort` scripts that; the fake NEVER sleeps,
    # so the guard's settle time costs the suite nothing.
    xbmc._abort = False
    xbmc._abort_waits = []

    class _Monitor:
        def waitForAbort(self, timeout=0):
            xbmc._abort_waits.append(timeout)
            return bool(xbmc._abort)

        def abortRequested(self):
            return bool(xbmc._abort)

    xbmc.Monitor = _Monitor
    # Window.IsActive(addonsettings) - the other async builtin the menu must not
    # fight. `_active_window` scripts which window Kodi reports as active.
    xbmc._active_window = ""

    def _cond(cond):
        if "TVOS" in cond:
            return bool(xbmc._tvos)
        if cond.startswith("Window.IsActive("):
            return cond[len("Window.IsActive(") :].rstrip(")") == xbmc._active_window
        return False

    xbmc.getCondVisibility = _cond
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

    # Home window (10000) properties. wiz.restore() publishes ezm_restore_verdict
    # there, and the looping Backup/Restore menu reads it back to tell "a restore ran
    # on this box" from "she cancelled before one started".
    xbmcgui._props = {}

    class _Window:
        def __init__(self, wid=0):
            self.wid = wid

        def setProperty(self, key, value):
            xbmcgui._props[key] = value

        def getProperty(self, key):
            return xbmcgui._props.get(key, "")

        def clearProperty(self, key):
            xbmcgui._props.pop(key, None)

    xbmcgui.Window = _Window
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
    # Scripted answers, consumed in order; once empty, `select_result` answers every
    # further call. Needed now that the Backup/Restore menu LOOPS: a single fixed
    # answer that is not -1 would re-drive the same branch forever.
    control.select_results = []
    # HARD BOUND. A stub that never returns -1 against a non-terminating menu loop
    # would hang the whole suite instead of failing it, so the stub itself refuses to
    # be called unreasonably often. This is what keeps the mutation check (delete the
    # loop / break, watch it go red) a FAILURE rather than a hang.
    control.select_limit = 25
    control.select_count = [0]

    def _select(options, heading=None):
        control.select_calls.append(list(options))
        control.select_count[0] += 1
        if control.select_count[0] > control.select_limit:
            raise AssertionError(
                "selectDialog called %d times (limit %d): a menu loop is not "
                "terminating" % (control.select_count[0], control.select_limit)
            )
        if control.select_results:
            return control.select_results.pop(0)
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

        def items(self, done, total, note=""):
            pass

        def bytes(self, done, total, note=""):
            pass

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


# --------------------------------------------------------------------------- #
# Menu wiring
# --------------------------------------------------------------------------- #
def test_categories_menu_has_no_tools_folder(dmod):
    """The Tools category is DELETED, not hidden and not emptied.

    Once the manual stale-key purge went in 2026.07.19.5, Tools held exactly one
    item - and that item is a backup operation. A folder a user must open to find
    a single entry is not a category. Nothing in the top-level menu may name it or
    route to it."""
    dmod.mod.CATEGORIES()
    names = [name for _url, name, _folder in dmod.xbmcplugin.items]
    urls = [url for url, _name, _folder in dmod.xbmcplugin.items]
    assert "Tools" not in names, names
    assert not any("action=tools" in u for u in urls), urls
    assert not hasattr(dmod.mod, "TOOLS"), "the TOOLS() builder must be gone entirely"


def test_backup_restore_offers_verify_last(dmod, monkeypatch):
    """ "Verify backup archive" now lives in Backup/Restore, and lives there LAST.

    It is a diagnostic on an archive that already exists, not a primary action, so
    it must sit after BACKUP and RESTORE rather than ahead of them. default.py
    routes at IMPORT time, so drive the real dispatch with the querystring and
    inspect the options the select dialog was actually offered."""
    wiz = types.ModuleType("resources.lib.modules.wiz")
    wiz.backup = lambda *a, **k: None
    wiz.restoreFolder = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "resources.lib.modules.wiz", wiz)
    setattr(sys.modules["resources.lib.modules"], "wiz", wiz)

    dmod.control.select_calls.clear()
    dmod.control.select_result = -1  # cancel the dialog; we only want the options
    monkeypatch.setattr(
        sys,
        "argv",
        ["plugin://script.ezmaintenanceplusplus/", "1", "?action=backup_restore"],
    )
    monkeypatch.delitem(sys.modules, "ezm_backup_restore_under_test", raising=False)
    spec = importlib.util.spec_from_file_location(
        "ezm_backup_restore_under_test", DEFAULT_PY
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ezm_backup_restore_under_test"] = mod
    spec.loader.exec_module(mod)

    assert dmod.control.select_calls, "the Backup/Restore dialog never opened"
    options = dmod.control.select_calls[0]
    assert len(options) == 3, options
    assert options[0] == "BACKUP"
    assert options[1] == "RESTORE"
    assert "VERIFY" in options[2].upper(), (
        "the verify entry must be the LAST option, after backup and restore: %r"
        % (options,)
    )


def test_backup_restore_verify_choice_runs_the_verifier(dmod, monkeypatch):
    """Picking the third entry must actually reach VERIFY_BACKUP_ARCHIVE.

    Without this, the label could be present and wired to nothing (or to the
    restore path, which is the dangerous mis-wire) and the options test above
    would still pass."""
    wiz = types.ModuleType("resources.lib.modules.wiz")
    backups, restores = [], []
    wiz.backup = lambda *a, **k: backups.append(k)
    wiz.restoreFolder = lambda *a, **k: restores.append(True)
    monkeypatch.setitem(sys.modules, "resources.lib.modules.wiz", wiz)
    setattr(sys.modules["resources.lib.modules"], "wiz", wiz)

    # The verify entry, then Back out of the (now looping) Backup/Restore menu.
    dmod.control.select_results = [2, -1]
    monkeypatch.setattr(
        sys,
        "argv",
        ["plugin://script.ezmaintenanceplusplus/", "1", "?action=backup_restore"],
    )
    monkeypatch.delitem(
        sys.modules, "ezm_backup_restore_verify_under_test", raising=False
    )
    spec = importlib.util.spec_from_file_location(
        "ezm_backup_restore_verify_under_test", DEFAULT_PY
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ezm_backup_restore_verify_under_test"] = mod
    spec.loader.exec_module(mod)

    # No restore.path is configured, so the verifier's own first guard fires. That
    # dialog is the proof the verify branch ran, and it proves nothing destructive
    # ran instead.
    assert dmod.control.infoDialog_calls == ["Please Setup a Zip Files Location first"]
    assert restores == [], "the verify entry must never reach restoreFolder"
    assert backups == [], "the verify entry must never reach backup"


# --------------------------------------------------------------------------- #
# The Backup/Restore menu LOOPS (2026.07.19.8)
#
# Owner report: "the verify backup cancel button should take us back inside the
# backup/restore and not the root". Cancelling any sub-action used to end the
# branch, end the script, and drop her at Kodi's root menu.
# --------------------------------------------------------------------------- #
def _drive_backup_restore(monkeypatch, name):
    """Execute default.py's real backup_restore branch under the fixture's fakes."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["plugin://script.ezmaintenanceplusplus/", "1", "?action=backup_restore"],
    )
    monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location(name, DEFAULT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub_wiz(monkeypatch):
    """Install a recording wiz stub; returns (module, backups, restores)."""
    wiz = types.ModuleType("resources.lib.modules.wiz")
    backups, restores = [], []
    wiz.backup = lambda *a, **k: backups.append(k)
    wiz.restoreFolder = lambda *a, **k: restores.append(True)
    monkeypatch.setitem(sys.modules, "resources.lib.modules.wiz", wiz)
    setattr(sys.modules["resources.lib.modules"], "wiz", wiz)
    return wiz, backups, restores


def test_cancelled_verify_re_presents_the_backup_restore_menu(dmod, monkeypatch):
    """THE REPORTED BUG. Cancel out of the verify flow -> back INSIDE Backup/Restore.

    Answers the menu with VERIFY, lets the verifier bail on its own missing-path
    guard (a cancel-shaped exit that restores nothing), then Backs out. The menu must
    have been presented TWICE: once to choose verify, once after it returned. One
    presentation means the script exited to the root menu, which is the defect."""
    _stub_wiz(monkeypatch)
    dmod.control.select_calls.clear()
    dmod.control.select_results = [2, -1]

    _drive_backup_restore(monkeypatch, "ezm_br_loop_verify_uut")

    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 2, (
        "after a cancelled verify the Backup/Restore menu must be re-presented, not "
        "abandoned to the root menu; dialogs seen: %r" % (dmod.control.select_calls,)
    )


def test_cancelled_restore_picker_re_presents_the_backup_restore_menu(
    dmod, monkeypatch
):
    """Same rule for RESTORE. restoreFolder() returns on every cancel path of its
    own (no zip location, empty folder, Back out of the file picker, "Cancel - don't
    restore anything"); whichever one fired, she lands back on this menu."""
    _wiz, _backups, restores = _stub_wiz(monkeypatch)
    dmod.control.select_calls.clear()
    dmod.control.select_results = [1, -1]

    _drive_backup_restore(monkeypatch, "ezm_br_loop_restore_uut")

    assert restores == [True], "the RESTORE entry must still reach restoreFolder once"
    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 2, dmod.control.select_calls


def test_cancelled_backup_mode_falls_back_to_the_menu_not_the_root(dmod, monkeypatch):
    """Backing out of the Full/Addons mode dialog is a sub-dialog cancel too.

    It must land on Backup/Restore, and it must NOT have taken a backup: a fallback
    that runs the default mode anyway would be far worse than the original bug."""
    _wiz, backups, _restores = _stub_wiz(monkeypatch)
    dmod.control.select_calls.clear()
    dmod.control.select_results = [0, -1, -1]  # BACKUP, cancel the mode dialog, Back

    _drive_backup_restore(monkeypatch, "ezm_br_loop_backupmode_uut")

    assert backups == [], "a cancelled mode dialog must not take a backup"
    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 2, (
        "cancelling the backup MODE dialog must fall back to Backup/Restore, not to "
        "the root menu; dialogs seen: %r" % (dmod.control.select_calls,)
    )


def test_cancelling_the_menu_itself_exits_exactly_once(dmod, monkeypatch):
    """The menu's own Back is the ONE exit. It must not re-present itself.

    A loop that re-presented on -1 too would be unescapable - a worse bug than the
    one being fixed, and the mirror-image failure of the original."""
    _stub_wiz(monkeypatch)
    dmod.control.select_calls.clear()
    dmod.control.select_results = [-1]

    _drive_backup_restore(monkeypatch, "ezm_br_loop_exit_uut")

    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 1, (
        "cancelling Backup/Restore must exit immediately; it was presented %d times"
        % len(menus)
    )


def test_an_unexpected_select_return_cannot_spin_the_menu(dmod, monkeypatch):
    """Anything outside 0/1/2/-1 exits rather than looping.

    selectDialog is a fake here and a real one could grow a new sentinel; a menu
    whose exit condition is `== -1` would spin forever on it, with no dialog on
    screen to cancel. The exit condition must be "not a known entry", not "-1".
    The fixture's call limit turns a regression here into a failure, not a hang."""
    _stub_wiz(monkeypatch)
    dmod.control.select_calls.clear()
    dmod.control.select_result = 99  # every call answers 99; nothing ever returns -1

    _drive_backup_restore(monkeypatch, "ezm_br_loop_unknown_uut")

    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 1, (
        "an unrecognised selectDialog return must break the loop, not spin it: %d "
        "presentations" % len(menus)
    )


def test_cancelling_the_zip_picker_re_presents_the_menu(dmod, monkeypatch):
    """The owner's LITERAL path: a configured zip location, a real list of archives,
    and Back out of the picker.

    The other verify test bails at the missing-zip-location guard, which is a
    cancel-SHAPED exit but not the one she hit - she had backups to pick from and
    pressed cancel on the list. Configure the location and stock the folder so the
    verifier reaches its real picker (default.py:302), then cancel THAT."""
    _stub_wiz(monkeypatch)
    dmod.control._settings["restore.path"] = "/fake/zips/"
    dmod.xbmcvfs.listdir_result = ([], ["kodi_backup_1.zip", "kodi_backup_2.zip"])
    dmod.control.select_calls.clear()
    # VERIFY, cancel the zip picker, then back out of the menu.
    dmod.control.select_results = [2, -1, -1]

    _drive_backup_restore(monkeypatch, "ezm_br_loop_picker_uut")

    picked = [c for c in dmod.control.select_calls if "kodi_backup_1.zip" in c]
    assert picked, (
        "the verifier never reached its zip picker, so this test is not exercising "
        "the reported path: %r" % (dmod.control.select_calls,)
    )
    assert dmod.control.infoDialog_calls == [], "no guard dialog should have fired"
    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 2, (
        "cancelling the zip picker must land back inside Backup/Restore: %r"
        % (dmod.control.select_calls,)
    )


def test_a_restore_that_ran_ends_the_menu(dmod, monkeypatch):
    """A restore that REACHED THE BOX must not re-present the menu.

    Every terminal path of wiz.restore() ends in ui.ask_restart(), and accepting it
    fires an ASYNC `Quit` - so Kodi is already tearing down when restoreFolder()
    returns. Re-presenting would open a modal into a shutting-down message pump.
    Declining leaves the box half-applied, which is no state to offer BACKUP in.
    wiz.restore() publishes ezm_restore_verdict on every path that touched the box;
    that property is the signal, since restoreFolder() returns None either way."""
    wiz, _backups, restores = _stub_wiz(monkeypatch)

    def _restore_that_ran(*a, **k):
        restores.append(True)
        # exactly what wiz.restore() does at wiz.py:1769
        sys.modules["xbmcgui"].Window(10000).setProperty(
            "ezm_restore_verdict", "complete"
        )

    wiz.restoreFolder = _restore_that_ran
    dmod.control.select_calls.clear()
    # Answer RESTORE, then keep answering RESTORE. If the menu re-presents, the stub
    # runs another restore and the call bound trips - a failure, not a hang.
    dmod.control.select_result = 1

    _drive_backup_restore(monkeypatch, "ezm_br_loop_restore_ran_uut")

    assert restores == [True], "the restore must run exactly once"
    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 1, (
        "a restore that ran must END the menu, not re-present it: %d presentations"
        % len(menus)
    )


def test_a_cancelled_restore_still_re_presents_the_menu(dmod, monkeypatch):
    """The mirror of the test above, and the owner's case for RESTORE.

    A cancel at the file picker or the how-dialog never reaches wiz.restore(), so
    nothing is published and the menu must stay open. Without this, the exit above
    could be implemented as "always exit after RESTORE" and still look correct."""
    _wiz, _backups, restores = _stub_wiz(monkeypatch)  # restoreFolder publishes nothing
    dmod.control.select_calls.clear()
    dmod.control.select_results = [1, -1]

    _drive_backup_restore(monkeypatch, "ezm_br_loop_restore_cancelled_uut")

    assert restores == [True]
    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 2, (
        "a CANCELLED restore publishes no verdict and must come back to the menu: %r"
        % (dmod.control.select_calls,)
    )


def test_a_stale_verdict_from_an_earlier_restore_does_not_end_the_menu(
    dmod, monkeypatch
):
    """The verdict is cleared before each restore, not just read after it.

    Kodi window properties outlive the script. Without the clear, one restore would
    poison every later RESTORE pick for the rest of the Kodi session: the stale
    property would still be set and the menu would exit on a restore the user
    actually cancelled - the reported bug, reintroduced through the back door."""
    _wiz, _backups, restores = _stub_wiz(monkeypatch)  # a cancel: publishes nothing
    sys.modules["xbmcgui"].Window(10000).setProperty("ezm_restore_verdict", "complete")
    dmod.control.select_calls.clear()
    dmod.control.select_results = [1, -1]

    _drive_backup_restore(monkeypatch, "ezm_br_loop_stale_verdict_uut")

    assert restores == [True]
    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 2, (
        "a verdict left over from an EARLIER restore must not end this menu: %r"
        % (dmod.control.select_calls,)
    )


def test_the_menu_stops_when_kodi_is_shutting_down(dmod, monkeypatch):
    """`Quit` is asynchronous, so the script outlives it. The menu must notice.

    ui.restart() calls executebuiltin("Quit") WITHOUT the wait flag - this codebase
    documents the blocking form as executebuiltin(..., True) (wiz.py:867) - so Kodi's
    teardown runs while this Python is still alive. That is not theoretical: defect A
    is a CApplication::Stop settings flush landing after the add-on returned. Opening
    a modal into a shutting-down message pump is the race this guard exists to avoid,
    and Monitor.waitForAbort is Kodi's own signal for it."""
    _stub_wiz(monkeypatch)
    dmod.control.select_calls.clear()
    dmod.control.select_result = 2  # VERIFY forever; only the abort can stop this
    dmod.xbmc._abort = True

    _drive_backup_restore(monkeypatch, "ezm_br_loop_abort_uut")

    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 1, (
        "the menu must not re-present while Kodi is shutting down: %d presentations"
        % len(menus)
    )
    assert dmod.xbmc._abort_waits, "the shutdown check never ran"


def test_the_menu_stops_when_a_sub_action_opened_settings(dmod, monkeypatch):
    """All three sub-actions bail to Kodi's Settings window when their path setting
    is unconfigured: wiz.backup on download.path (wiz.py:337), wiz.restoreFolder
    (wiz.py:684) and VERIFY_BACKUP_ARCHIVE on restore.path.

    Addon.OpenSettings is ASYNC too, so the sub-action returns immediately and the
    menu would otherwise drop a modal select dialog on top of the settings window the
    user was just sent to - a new defect, created by the loop itself."""
    _stub_wiz(monkeypatch)
    dmod.control.select_calls.clear()
    dmod.control.select_result = 2  # VERIFY forever; only the settings check stops it

    # VERIFY_BACKUP_ARCHIVE's own guard fires (no restore.path) and calls
    # control.openSettings; model Kodi honouring that async builtin.
    def _open_settings(*a, **k):
        dmod.control.openSettings_calls.append(a)
        dmod.xbmc._active_window = "addonsettings"

    dmod.control.openSettings = _open_settings

    _drive_backup_restore(monkeypatch, "ezm_br_loop_settings_uut")

    assert dmod.control.openSettings_calls, "the settings bail never happened"
    menus = [c for c in dmod.control.select_calls if c and c[0] == "BACKUP"]
    assert len(menus) == 1, (
        "the menu must not re-present on top of the Settings window: %d presentations"
        % len(menus)
    )


def test_the_guard_probes_are_best_effort_and_never_break_the_menu(dmod):
    """A guard that raises must not take the menu down with it.

    These probes are diagnostics, not the feature. If Monitor or getCondVisibility
    throws (a fake, an odd platform, a Kodi version without the property), staying on
    the menu is the behaviour the owner asked for, so the failure must be swallowed
    and answered "safe"."""

    class _Boom:
        def waitForAbort(self, timeout=0):
            raise RuntimeError("no monitor here")

    assert dmod.mod._safe_to_re_present(monitor=_Boom()) is True
    # And the verdict read must answer False (keep the menu) when it cannot read.
    dmod.mod.xbmcgui.Window = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    assert dmod.mod._restore_verdict() is False
    dmod.mod._clear_restore_verdict()  # must not raise


def test_the_menu_loop_is_bounded_by_the_stub(dmod, monkeypatch):
    """Self-test of the guard above: prove the fixture BOUNDS a runaway loop.

    Without this, "the suite did not hang" is an unverified claim about the stub. A
    branch that ignored the exit condition would hang forever under an unbounded
    stub; under this one it raises. Driven against the real dialog options so it
    fails if the stub's limit is ever removed."""
    dmod.control.select_limit = 5
    dmod.control.select_result = 0  # never -1: a loop keying only on -1 never exits
    with pytest.raises(AssertionError, match="not terminating"):
        while True:
            dmod.control.selectDialog(["BACKUP", "RESTORE", "VERIFY BACKUP ARCHIVE"])


def test_verify_entry_keeps_its_description_verbatim(dmod):
    """The plain-language copy survives the move.

    The Backup/Restore select dialog has no plot slot, so the description now lives
    beside the entry in default.py. It is good copy and it is not to be reworded or
    dropped as a casualty of the menu move."""
    src = (ADDON_ROOT / "default.py").read_text()
    # Flatten comment markers and wrapping so a reflow cannot fail this test, while
    # a reworded or truncated sentence still does.
    flat = " ".join(line.strip().lstrip("#").strip() for line in src.splitlines())
    flat = " ".join(flat.split())
    assert (
        "Read-only check of a backup zip: entry count, manifest, failed list, "
        "IPTV data, top-level layout. Restores nothing." in flat
    ), "the verify item's description must be preserved verbatim"


def test_retired_tools_category_is_a_silent_no_op(dmod, monkeypatch):
    """A stale favourite/widget pointing at the deleted category must land on a
    benign no-op, never the unknown-action path and never a traceback.

    Mirrors the retired purge action's treatment (2026.07.19.5). default.py routes
    at IMPORT time off sys.argv[2], so the honest test imports it with the retired
    querystring and asserts a clean load: no exception, no dialog of any kind."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["plugin://script.ezmaintenanceplusplus/", "1", "?action=tools"],
    )
    monkeypatch.delitem(sys.modules, "ezm_retired_tools_under_test", raising=False)
    spec = importlib.util.spec_from_file_location(
        "ezm_retired_tools_under_test", DEFAULT_PY
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ezm_retired_tools_under_test"] = mod

    spec.loader.exec_module(mod)  # must not raise

    ui = sys.modules["resources.lib.modules.ui"]
    assert getattr(ui, "error_calls", []) == [], "the retired category must not error"
    assert getattr(ui, "done_calls", []) == [], (
        "the retired category must be SILENT - no dialog at all"
    )
    assert dmod.xbmcplugin.items == [], "the retired category must list nothing"


def test_retired_tools_category_is_explicitly_routed(dmod):
    """Pairs with the behavioural test above, which cannot stand alone.

    Mutation-checked 2026-07-19: DELETING the `elif action == "tools"` branch keeps
    the behavioural test GREEN, because the unknown-action fallthrough is silent
    too. Silence is not the property we want - an explicit route is. Without the
    branch a stale bookmark lands in the unknown-action path and renders an empty
    directory (a visible dead end), which the Kodi fakes cannot distinguish from a
    deliberate no-op. This is the same source-level pin the retired purge action
    carries, and for the same reason."""
    src = (ADDON_ROOT / "default.py").read_text()
    assert 'elif action == "tools":' in src, (
        "the retired category must still be routed, so an old bookmark cannot fall "
        "through to the unknown-action path"
    )
    assert "def TOOLS(" not in src, "the TOOLS() builder must be deleted, not kept"


def test_retired_purge_action_is_a_silent_no_op(dmod):
    """A stale favourite/widget still pointing at action=purge_stale_tvos_keys
    must land on a benign no-op, never an unknown-action path or a traceback."""
    assert not hasattr(dmod.mod, "PURGE_STALE_TVOS_KEYS")
    src = (ADDON_ROOT / "default.py").read_text()
    assert 'elif action == "purge_stale_tvos_keys":' in src, (
        "the retired action must still be routed, so an old bookmark cannot fall "
        "through to the unknown-action path"
    )


def test_retired_purge_action_executes_without_raising(dmod, monkeypatch):
    """BEHAVIOURAL proof of the no-op, not a grep of the source.

    The source assertion above pins that a branch exists; it cannot prove that
    ACTUALLY dispatching the retired action is harmless. default.py routes at
    IMPORT time off sys.argv[2], so the only honest test is to import it with
    the retired querystring and assert the module loads clean: no exception, no
    error dialog, no dialog of any kind. A grandmother's stale home-screen
    shortcut must do nothing visible, not raise.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plugin://script.ezmaintenanceplusplus/",
            "1",
            "?action=purge_stale_tvos_keys",
        ],
    )
    monkeypatch.delitem(sys.modules, "ezm_retired_action_under_test", raising=False)
    spec = importlib.util.spec_from_file_location(
        "ezm_retired_action_under_test", DEFAULT_PY
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ezm_retired_action_under_test"] = mod

    spec.loader.exec_module(mod)  # must not raise

    ui = sys.modules["resources.lib.modules.ui"]
    assert getattr(ui, "error_calls", []) == [], "the retired action must not error"
    assert getattr(ui, "done_calls", []) == [], (
        "the retired action must be SILENT - no dialog at all"
    )


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


def test_freshstart_wipe_runs_and_prompts_shutdown_not_false_failed(dmod, monkeypatch):
    """Regression (QA 2026-07-17): onetap._wipe changed from a 3-tuple to a 4-tuple;
    FRESHSTART still unpacked 3, so it WIPED the box then raised on the unpack, was
    swallowed, and falsely told the user 'the wipe did not run' WITHOUT restarting -
    a wiped box left stranded. Let the wipe run and assert honest completion + the exit.

    2026-07-21: Fresh Start requires stock Estuary (so post-wipe dialogs still render)
    and ends with ui.ask_terminate(), which is now an acknowledge-then-exit NOTICE, not
    a Shut down / Later choice - it always hard-exits via ui.terminate() (os._exit), so
    the exit flush cannot re-dirty the slate and Kodi can never outlive the wipe that
    deleted the databases it holds open. The completion copy must say the box will
    close/reopen, never 'restart'."""
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

    # Guard: the live skin must live OUTSIDE the wipe root to survive (APK-resident).
    dmod.mod.HOME = "/home/.kodi"
    dmod.mod.translatePath = lambda p: (
        "/apk/assets/skin.estuary" if p == "special://skin/" else p
    )
    dmod.ui.confirm_wipe = lambda *a, **k: True
    prompts = []
    dmod.ui.ask_terminate = lambda status="", **k: prompts.append(status) or False

    dmod.mod.FRESHSTART()

    assert wiped["v"] is True, "the wipe must actually run"
    assert not any("FAILED" in m for m in dmod.ui.done_calls), dmod.ui.done_calls
    assert len(prompts) == 1, (
        "a wiped box MUST be driven to the Shut down / Later prompt"
    )
    # Honest appliance copy: the box closes and is reopened, it does NOT self-restart.
    assert "restart" not in prompts[0].lower(), prompts[0]


def test_freshstart_that_dies_mid_wipe_still_exits_and_says_so(dmod, monkeypatch):
    """QA finding 2026-07-21. The one Fresh Start path that still left Kodi ALIVE on a
    wiped tree, and lied about it.

    _wipe deletes the POSIX tree first and sweeps NSUserDefaults keys LAST, so a raise
    from the key pass (tvOS) arrives with every file - including the databases Kodi
    holds open - ALREADY GONE. The old code swallowed it, left wipe_failed at None,
    printed "the wipe did not run. Nothing was removed." and RETURNED without
    terminating. Both halves are wrong: the message is false, and staying up on a tree
    whose open databases were just unlinked is exactly the SIGABRT this release exists
    to prevent (the office Fire TV, 2026-07-21).
    """
    import sys
    import types as _t

    onetap = _t.ModuleType("resources.lib.modules.onetap")
    reached = {"v": False}

    def _wipe(home, excludes, keep=None, progress=None):
        reached["v"] = True  # the destructive pass BEGAN
        raise RuntimeError("NSUserDefaults key sweep blew up after the POSIX delete")

    onetap._wipe = _wipe
    onetap._wipe_excludes = lambda: set()
    onetap.keep_addon_db = lambda: set()
    monkeypatch.setitem(sys.modules, "resources.lib.modules.onetap", onetap)
    setattr(sys.modules["resources.lib.modules"], "onetap", onetap)

    dmod.mod.HOME = "/home/.kodi"
    dmod.mod.translatePath = lambda p: (
        "/apk/assets/skin.estuary" if p == "special://skin/" else p
    )
    dmod.ui.confirm_wipe = lambda *a, **k: True
    prompts = []
    dmod.ui.ask_terminate = lambda status="", **k: prompts.append(status) or False

    dmod.mod.FRESHSTART()

    assert reached["v"] is True
    assert len(prompts) == 1, (
        "a box whose wipe BEGAN must be driven to terminate - it cannot be left "
        "running on unlinked databases"
    )
    assert not any("did not run" in m.lower() for m in dmod.ui.done_calls), (
        "must not claim nothing was removed when the wipe had already started: %s"
        % dmod.ui.done_calls
    )
    assert not any("nothing was removed" in m.lower() for m in prompts), prompts


def test_freshstart_that_never_started_stays_up_and_says_nothing_was_removed(
    dmod, monkeypatch
):
    """The counterpart. If the wipe genuinely never began (import error, or a raise
    before the first delete) Kodi is untouched, so it is SAFE to stay up - and killing
    the app there would be a gratuitous shutdown over a no-op."""
    import sys
    import types as _t

    onetap = _t.ModuleType("resources.lib.modules.onetap")

    def _excludes():
        raise RuntimeError("failed before any delete")

    onetap._wipe = lambda *a, **k: (0, 0, 0, [])
    onetap._wipe_excludes = _excludes
    onetap.keep_addon_db = lambda: set()
    monkeypatch.setitem(sys.modules, "resources.lib.modules.onetap", onetap)
    setattr(sys.modules["resources.lib.modules"], "onetap", onetap)

    dmod.mod.HOME = "/home/.kodi"
    dmod.mod.translatePath = lambda p: (
        "/apk/assets/skin.estuary" if p == "special://skin/" else p
    )
    dmod.ui.confirm_wipe = lambda *a, **k: True
    prompts = []
    dmod.ui.ask_terminate = lambda status="", **k: prompts.append(status) or False

    dmod.mod.FRESHSTART()

    assert prompts == [], "nothing was destroyed, so do not kill Kodi"
    assert any("did not run" in m.lower() for m in dmod.ui.done_calls), (
        dmod.ui.done_calls
    )


def test_freshstart_requires_stock_estuary_skin(dmod, monkeypatch):
    """Fresh Start deletes the ACTIVE skin's files; only stock Estuary survives the wipe
    (it is APK-resident, outside special://home), so from any other skin the post-wipe
    completion prompt could not render. FRESHSTART MUST abort (error, no wipe, no exit
    prompt) unless the live skin is skin.estuary."""
    import sys
    import types as _t

    onetap = _t.ModuleType("resources.lib.modules.onetap")
    wiped = {"v": False}

    def _wipe(home, excludes, keep=None, progress=None):
        wiped["v"] = True
        return (0, 0, 0, [])

    onetap._wipe = _wipe
    onetap._wipe_excludes = lambda: set()
    onetap.keep_addon_db = lambda: set()
    monkeypatch.setitem(sys.modules, "resources.lib.modules.onetap", onetap)
    setattr(sys.modules["resources.lib.modules"], "onetap", onetap)

    # A custom skin installed UNDER the wipe root (special://home/addons) - it would be
    # deleted mid-wipe, so Fresh Start must refuse.
    dmod.mod.HOME = "/home/.kodi"
    dmod.mod.translatePath = lambda p: (
        "/home/.kodi/addons/skin.estuary7" if p == "special://skin/" else p
    )
    dmod.ui.confirm_wipe = lambda *a, **k: True
    prompts = []
    dmod.ui.ask_terminate = lambda status="", **k: prompts.append(status) or False

    dmod.mod.FRESHSTART()

    assert wiped["v"] is False, "the wipe must NOT run from a skin under the wipe root"
    assert prompts == [], "no completion/terminate prompt when the run is refused"
    assert any("estuary" in m.lower() for m in dmod.ui.error_calls), dmod.ui.error_calls


# --------------------------------------------------------------------------- #
# CreateDir art keys. Regression test for the py2 -> py3 port bug found
# 2026-07-18 by looking at an actual screen: every menu row rendered Kodi's
# DefaultVideo.png (the old reel-to-reel movie camera) instead of the add-on's
# own shield icon.
#
# The PY2 branch passes thumbnailImage= as a ListItem CONSTRUCTOR kwarg, which
# really does set the thumbnail. The py3 rewrite turned that kwarg NAME into a
# setArt KEY, and there is no "thumbnailImage" art key, so it was silently
# dropped. With no thumb and setInfo(type="Video"), Kodi fell back to the video
# default. Every setArt fake in this suite is `lambda *a, **k: None`, which is
# precisely why nothing caught it.
# --------------------------------------------------------------------------- #

_VALID_ART_KEYS = {
    "thumb",
    "poster",
    "banner",
    "fanart",
    "clearart",
    "clearlogo",
    "landscape",
    "icon",
}


def _capture_art(dmod):
    """Call CreateDir with a recording ListItem and return the merged art dict."""
    import sys as _sys

    calls = []
    real = _sys.modules["xbmcgui"].ListItem

    class _Recording(real):
        def setArt(self, d):
            calls.append(dict(d))

    _sys.modules["xbmcgui"].ListItem = _Recording
    try:
        dmod.mod.CreateDir("Tools", "url", "tools", "ICON.png", "FAN.jpg", "")
    finally:
        _sys.modules["xbmcgui"].ListItem = real
    merged = {}
    for d in calls:
        merged.update(d)
    return merged


def test_createdir_sets_poster_without_clobbering_folder_glyphs(dmod):
    """The add-on icon must land on 'poster', NOT 'thumb'.

    poster feeds the skin's left info panel (View_50_List.xml:302 binds
    $VAR[IconWallPosterVar], whose chain is poster -> thumb, with a hardcoded
    fallback="DefaultVideo.png"). thumb would ALSO satisfy that panel, but the list
    view prefers thumb over icon, so setting thumb wipes out the per-row folder
    glyphs. Both halves matter: shield in the panel, folders in the list."""
    art = _capture_art(dmod)
    assert "poster" in art, (
        "no 'poster' art key set, so the left info panel falls back to "
        "DefaultVideo.png (the reel-to-reel camera): %r" % (art,)
    )
    assert art["poster"] == "ICON.png"
    assert "thumb" not in art, (
        "do NOT set thumb here: the list view prefers thumb over icon, so setting it "
        "replaces the per-row FOLDER glyphs with the add-on icon. Regression 2026-07-18."
    )
    assert art["icon"] == "DefaultFolder.png", "the per-row folder glyph must survive"


def test_createdir_uses_no_invalid_art_keys(dmod):
    """'thumbnailImage' is a constructor kwarg, never an art key. Kodi ignores it
    silently, which is how the wrong icon shipped unnoticed."""
    art = _capture_art(dmod)
    bogus = set(art) - _VALID_ART_KEYS
    assert not bogus, "invalid setArt keys are silently ignored by Kodi: %s" % (
        sorted(bogus),
    )
