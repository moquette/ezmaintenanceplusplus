"""Coverage for script.ezmaintenanceplusplus's wiz.py port-stripping fix.

wiz.py's backup()/restoreFolder() read download.path/restore.path - both
Kodi "type=folder" settings, browse-only, with no manual text entry at all.
Kodi's own network-browse dialog bakes an explicit port into the nfs:// URL
it hands back (e.g. nfs://host:2049/export/path), and that explicit-port
form breaks Kodi's own NFS client write path - live-proven, independently,
on two different boxes (a VfsCopyError / 0-byte copy every time). Since the
setting can only ever be set via that same dialog, this can recur on any
future box; _strip_nfs_port() defangs it at the two read sites.

This is a large, pre-existing third-party add-on this repo forks/patches
(CLAUDE.md: "standardize on the repo's ++ fork"), with no existing test
harness of its own. The fixture below fakes just enough of xbmc*/xbmcaddon/
xbmcgui/xbmcvfs/xbmcplugin for wiz.py's own import chain (control.py,
maintenance.py, tools.py, ui.py) to succeed, so _strip_nfs_port can be
exercised as the real function inside the real module, not a copy-pasted
reimplementation of its regex.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
ADDON_ROOT = REPO_ROOT / "script.ezmaintenanceplusplus"


@pytest.fixture
def wiz(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    xbmc = types.ModuleType("xbmc")
    xbmc.translatePath = lambda p: p.replace("special://", str(tmp_path) + "/")
    xbmc.getLocalizedString = lambda i: str(i)
    xbmc.getInfoLabel = lambda s: ""
    xbmc.getCondVisibility = lambda s: False
    xbmc.getSkinDir = lambda: "skin.estuary"
    xbmc.log = lambda *a, **k: None
    xbmc.executebuiltin = lambda *a, **k: None
    xbmc.executeJSONRPC = lambda cmd: "{}"
    xbmc.LOGERROR = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGINFO = 3
    xbmc.LOGDEBUG = 4
    xbmc.LOGFATAL = 0
    xbmc.LOGNONE = 5
    xbmc.LOGNOTICE = 3
    xbmc.PLAYLIST_VIDEO = 1
    xbmc.sleep = lambda ms: None
    xbmc.Keyboard = lambda *a, **k: types.SimpleNamespace(
        doModal=lambda: None, isConfirmed=lambda: False, getText=lambda: ""
    )
    xbmc.PlayList = lambda *a, **k: types.SimpleNamespace(
        clear=lambda: None, add=lambda *a: None
    )
    xbmc.Player = lambda *a, **k: types.SimpleNamespace(play=lambda *a, **k: None)
    xbmc.Monitor = type(
        "Monitor",
        (),
        {"abortRequested": lambda self: False, "waitForAbort": lambda self, t: False},
    )

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _FakeAddon:
        def getLocalizedString(self, i):
            return str(i)

        def getSetting(self, key):
            return ""

        def setSetting(self, key, value):
            pass

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
        def ok(self, *a, **k):
            return False

        def yesno(self, *a, **k):
            return False

        def notification(self, *a, **k):
            pass

        def select(self, *a, **k):
            return -1

    xbmcgui.DialogProgress = _FakeDialogProgress
    xbmcgui.DialogProgressBG = _FakeDialogProgress
    xbmcgui.Dialog = _FakeDialog
    xbmcgui.ListItem = lambda *a, **k: types.SimpleNamespace(
        setArt=lambda *a, **k: None
    )
    xbmcgui.ControlButton = lambda *a, **k: None
    xbmcgui.ControlImage = lambda *a, **k: None

    class _FakeWindow:
        def __init__(self, *a, **k):
            pass

        def getProperty(self, k):
            return ""

        def setProperty(self, k, v):
            pass

        def clearProperty(self, k):
            pass

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
    xbmcvfs.copy = lambda s, d: True
    xbmcvfs.File = lambda *a, **k: types.SimpleNamespace(
        read=lambda *a: b"", write=lambda *a: True, close=lambda: None, size=lambda: 0
    )

    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.addDirectoryItem = lambda *a, **k: None
    xbmcplugin.endOfDirectory = lambda *a, **k: None
    xbmcplugin.setContent = lambda *a, **k: None
    xbmcplugin.setProperty = lambda *a, **k: None
    xbmcplugin.setResolvedUrl = lambda *a, **k: None

    for name, mod in (
        ("xbmc", xbmc),
        ("xbmcaddon", xbmcaddon),
        ("xbmcgui", xbmcgui),
        ("xbmcvfs", xbmcvfs),
        ("xbmcplugin", xbmcplugin),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    return importlib.import_module("resources.lib.modules.wiz")


def test_strip_nfs_port_removes_explicit_port(wiz):
    assert (
        wiz._strip_nfs_port("nfs://192.168.7.2:2049/Users/moquette/Kodi/Backup/atv-2/")
        == "nfs://192.168.7.2/Users/moquette/Kodi/Backup/atv-2/"
    )


def test_strip_nfs_port_no_port_unchanged(wiz):
    path = "nfs://192.168.7.2/Users/moquette/Kodi/Backup/atv-2/"
    assert wiz._strip_nfs_port(path) == path


def test_strip_nfs_port_leaves_non_nfs_paths_alone(wiz):
    assert (
        wiz._strip_nfs_port("smb://192.168.7.2/KodiBackup/atv-2/")
        == "smb://192.168.7.2/KodiBackup/atv-2/"
    )
    assert wiz._strip_nfs_port("/local/path") == "/local/path"


def test_strip_nfs_port_handles_empty_and_none(wiz):
    assert wiz._strip_nfs_port("") == ""
    assert wiz._strip_nfs_port(None) is None


def test_strip_nfs_port_bare_host_no_trailing_slash(wiz):
    # A port on a bare host with no path at all must still be stripped.
    assert wiz._strip_nfs_port("nfs://192.168.7.2:2049") == "nfs://192.168.7.2"


def test_backup_uses_stripped_port_path(wiz, monkeypatch, tmp_path):
    """End-to-end: backup() must pass the STRIPPED path to CreateZip, not the
    raw (possibly port-carrying) download.path setting value."""
    backupdata = tmp_path / "home"
    backupdata.mkdir()
    monkeypatch.setattr(wiz.control, "HOME", str(backupdata))
    monkeypatch.setattr(
        wiz.control,
        "setting",
        lambda key: (
            "nfs://192.168.7.2:2049/Users/moquette/Kodi/Backup/atv-2/"
            if key == "download.path"
            else ""
        ),
    )
    monkeypatch.setattr(wiz.tools, "_get_keyboard", lambda **k: "mybackup")
    monkeypatch.setattr(wiz.ui, "confirm", lambda *a, **k: True)  # accept the name

    captured = {}

    def _fake_create_zip(src, dst, *a, **k):
        captured["dst"] = dst
        return False

    monkeypatch.setattr(wiz, "CreateZip", _fake_create_zip)
    monkeypatch.setattr(wiz, "_rotate_vfs", lambda *a, **k: None)
    monkeypatch.setattr(
        wiz,
        "xbmcaddon",
        types.SimpleNamespace(
            Addon=lambda: types.SimpleNamespace(getSetting=lambda k: "false")
        ),
    )

    wiz.backup(mode="full")
    assert "dst" in captured, "CreateZip must have been called"
    assert ":2049" not in captured["dst"]
    assert captured["dst"].startswith(
        "nfs://192.168.7.2/Users/moquette/Kodi/Backup/atv-2/"
    )


def _stub_backup_env(wiz, monkeypatch, tmp_path, keyboard_name="mybackup"):
    """Common backup() stubs: a HOME to zip, an nfs download.path, a keyboard
    name, and no-op rotation + addon settings. Returns nothing; callers add the
    ui.confirm / CreateZip stubs they care about."""
    backupdata = tmp_path / "home"
    backupdata.mkdir()
    monkeypatch.setattr(wiz.control, "HOME", str(backupdata))
    monkeypatch.setattr(
        wiz.control,
        "setting",
        lambda key: (
            "nfs://192.168.7.2/Users/moquette/Kodi/Backup/atv-2/"
            if key == "download.path"
            else ""
        ),
    )
    monkeypatch.setattr(wiz.tools, "_get_keyboard", lambda **k: keyboard_name)
    monkeypatch.setattr(wiz, "_rotate_vfs", lambda *a, **k: None)
    monkeypatch.setattr(
        wiz,
        "xbmcaddon",
        types.SimpleNamespace(
            Addon=lambda: types.SimpleNamespace(getSetting=lambda k: "false")
        ),
    )


def test_backup_aborts_when_name_confirm_declined(wiz, monkeypatch, tmp_path):
    """Declining the new name-confirm prompt must abort BEFORE any zip is built -
    parity with restore's confirm, and no partial work on a cancel."""
    _stub_backup_env(wiz, monkeypatch, tmp_path)
    called = {"zip": False}

    def _no_zip(*a, **k):
        called["zip"] = True
        return False

    monkeypatch.setattr(wiz, "CreateZip", _no_zip)
    monkeypatch.setattr(wiz.ui, "confirm", lambda *a, **k: False)  # user cancels
    wiz.backup(mode="full")
    assert called["zip"] is False, "declining the confirm must abort before CreateZip"


def test_backup_confirm_shows_final_filename_then_proceeds(wiz, monkeypatch, tmp_path):
    """Confirming proceeds to the zip build, and the confirm message shows the
    FINAL filename (spaces->_, auto timestamp, .zip) so the user reviews it."""
    _stub_backup_env(wiz, monkeypatch, tmp_path, keyboard_name="Living Room")
    seen = {}

    def _capture_confirm(message, **k):
        seen["msg"] = message
        return True

    monkeypatch.setattr(wiz.ui, "confirm", _capture_confirm)
    captured = {}
    monkeypatch.setattr(
        wiz, "CreateZip", lambda src, dst, *a, **k: captured.__setitem__("dst", dst)
    )
    wiz.backup(mode="full")
    assert "Living_Room" in seen.get("msg", ""), "confirm must show the final name"
    assert seen["msg"].rstrip().endswith(".zip"), "confirm must show the .zip filename"
    assert "dst" in captured, "confirming must proceed to CreateZip"


def test_backup_dropbox_has_name_confirm_before_zip():
    """The Dropbox backup path must gate the same way: a ui.confirm on the final
    name BEFORE the zip build. Asserted at the source level (the runtime path
    imports dropbox_remote, whose module-load side effects are out of scope
    here); the local path's confirm behavior is exercised end-to-end above."""
    import inspect

    from resources.lib.modules import wiz as wizmod

    src = inspect.getsource(wizmod._backup_dropbox)
    assert "ui.confirm" in src, "_backup_dropbox must confirm the name"
    assert src.index("ui.confirm") < src.index("CreateZip"), (
        "the confirm must come BEFORE the zip build"
    )


def test_backup_opens_native_settings_when_path_unset(wiz, monkeypatch, tmp_path):
    """backup() with an empty download.path must open the (now-working) NATIVE
    settings dialog via control.openSettings, not the retired custom screen."""
    backupdata = tmp_path / "home"
    backupdata.mkdir()
    monkeypatch.setattr(wiz.control, "HOME", str(backupdata))
    monkeypatch.setattr(wiz.control, "setting", lambda key: "")

    calls = []
    monkeypatch.setattr(wiz.control, "openSettings", lambda *a, **k: calls.append(True))

    wiz.backup(mode="full")
    assert calls == [True]


def test_restore_opens_native_settings_when_path_unset(wiz, monkeypatch):
    """restoreFolder() with an empty restore.path must open the NATIVE settings
    dialog via control.openSettings, not the retired custom screen."""
    monkeypatch.setattr(wiz.control, "setting", lambda key: "")

    calls = []
    monkeypatch.setattr(wiz.control, "openSettings", lambda *a, **k: calls.append(True))

    wiz.restoreFolder()
    assert calls == [True]


def test_restore_does_not_rewrite_settings_verbatim_restore(wiz, monkeypatch, tmp_path):
    """A restore now restores the backup EXACTLY as taken - it does NOT re-stamp
    download.path/restore.path/destination afterward. The user sets the backup path
    themselves (the native settings dialog works), so restore stays a plain, predictable
    extract with no magic touching the restored settings."""
    import zipfile as _zip

    writes = []
    monkeypatch.setattr(wiz.control, "setting", lambda key: "")
    monkeypatch.setattr(wiz.control, "setSetting", lambda k, v: writes.append((k, v)))
    monkeypatch.setattr(wiz.ui, "ask_restart", lambda *a, **k: None)

    src = tmp_path / "some_backup.zip"
    with _zip.ZipFile(src, "w") as z:
        z.writestr(
            "userdata/addon_data/script.ezmaintenanceplusplus/settings.xml",
            "<settings><setting id='download.path'>"
            "nfs://192.168.7.2/Kodi/Backup/office/</setting></settings>",
        )
        z.writestr("userdata/guisettings.xml", "<settings />")

    wiz.restore(str(src), confirm=False)

    # restore() must NOT setSetting any box-local key - the extracted settings.xml stands.
    assert not any(
        k in ("download.path", "restore.path", "destination") for k, _ in writes
    ), f"restore should not re-stamp box-local settings, but wrote: {writes}"


# --------------------------------------------------------------------------- #
# "Wipe clean before restore" (clean-clone) path + the extract crash fix.
# --------------------------------------------------------------------------- #
class _RecordingProgress:
    """A fake ui.Progress that records every items() note and never cancels, so the
    extract's dialog-update throttle can be asserted off-device."""

    def __init__(self):
        self.notes = []

    def cancelled(self):
        return False

    def items(self, done, total, note=""):
        self.notes.append(note)


def _make_valid_zip(path, files):
    import zipfile as _zip

    with _zip.ZipFile(path, "w") as z:
        for name, body in files:
            z.writestr(name, body)
    return path


def _load_onetap():
    return importlib.import_module("resources.lib.modules.onetap")


def test_restore_wipe_does_not_wipe_on_bad_zip(wiz, monkeypatch, tmp_path):
    """(a) restore(wipe=True) with a corrupt/short zip must ABORT with the box UNTOUCHED
    - validation fails, so the wipe is never reached."""
    onetap = _load_onetap()

    wiped = []
    restarted = []
    monkeypatch.setattr(onetap, "_wipe", lambda *a, **k: wiped.append(a))
    monkeypatch.setattr(wiz.ui, "ask_restart", lambda *a, **k: restarted.append(True))
    monkeypatch.setattr(wiz.control, "HOME", str(tmp_path / "home"))

    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"this is not a zip file at all")  # size > 0 but not a real zip

    wiz.restore(str(bad), confirm=False, wipe=True)

    assert wiped == [], "the box must NOT be wiped when the zip is invalid"
    assert restarted == [], "a bad zip must not reach the restart prompt"


def test_restore_wipe_validates_then_wipes_then_extracts(wiz, monkeypatch, tmp_path):
    """(b) restore(wipe=True) with a valid zip must wipe ONLY after validation, then run
    the (uninterruptible) extract, then reach the restart prompt - in that order."""
    onetap = _load_onetap()

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(wiz.control, "HOME", str(home))

    events = []
    monkeypatch.setattr(onetap, "_wipe", lambda *a, **k: events.append("wipe"))

    captured = {}

    def _fake_extract(_in, _out, progress, **kw):
        events.append("extract")
        captured["cancelable"] = kw.get("cancelable", True)
        return False

    monkeypatch.setattr(wiz, "ExtractWithProgress", _fake_extract)
    monkeypatch.setattr(wiz.ui, "ask_restart", lambda *a, **k: events.append("restart"))

    src = tmp_path / "backup.zip"
    _make_valid_zip(
        src,
        [
            ("userdata/guisettings.xml", "<settings />"),
            ("addons/foo/addon.xml", "<a/>"),
        ],
    )

    wiz.restore(str(src), confirm=False, wipe=True)

    assert events == ["wipe", "extract", "restart"], events
    # A wiped box must be driven by an UNINTERRUPTIBLE extract (post_wipe semantics).
    assert captured["cancelable"] is False


def test_wipe_excludes_preserves_addon_deps_and_temp(wiz):
    """(c) the reused One-Tap wipe excludes must preserve this add-on, its runtime deps,
    and special://temp (where the validated zip is staged)."""
    onetap = _load_onetap()
    ex = onetap._wipe_excludes()
    assert "temp" in ex
    assert "script.module.requests" in ex
    assert "script.ezmaintenanceplusplus" in ex


def test_extract_progress_note_is_throttled(wiz, tmp_path):
    """(d) the extract must NOT redraw the progress dialog with a new filename every file
    (the Fire OS 8 SIGSEGV). The note is refreshed at most every N files and never carries
    a per-file basename."""
    src = tmp_path / "many.zip"
    files = [("data/file%03d.txt" % i, "x") for i in range(200)]
    _make_valid_zip(src, files)

    out = tmp_path / "out"
    out.mkdir()
    p = _RecordingProgress()
    wiz.ExtractWithProgress(str(src), str(out), p)

    # Far fewer dialog updates than files (throttled), but still moving.
    assert 0 < len(p.notes) <= 200 // 10
    # No note carries a source basename - only the short static "Extracting file X of Y".
    assert all(n.startswith("Extracting file ") for n in p.notes)
    assert not any(".txt" in n for n in p.notes)
    # Every file was still actually extracted.
    assert len(list(out.rglob("*.txt"))) == 200


def test_order_userdata_first_puts_settings_before_addons(wiz):
    """(e) userdata/ entries must be ordered before addons/ so an interrupted extract
    keeps the irreplaceable settings."""
    infos = [
        types.SimpleNamespace(filename="addons/a/x.py"),
        types.SimpleNamespace(filename="userdata/guisettings.xml"),
        types.SimpleNamespace(filename="media/logo.png"),
        types.SimpleNamespace(filename="addons/b/y.py"),
        types.SimpleNamespace(filename="userdata/sources.xml"),
    ]
    names = [i.filename for i in wiz._order_userdata_first(infos)]
    last_userdata = max(i for i, n in enumerate(names) if n.startswith("userdata/"))
    first_addon = min(i for i, n in enumerate(names) if n.startswith("addons/"))
    assert last_userdata < first_addon, names


# --------------------------------------------------------------------------- #
# Post-restore, per-device video-cache-buffer retune.
# --------------------------------------------------------------------------- #
def test_restore_writes_buffer_prompt_marker(wiz, monkeypatch, tmp_path):
    """(a) a successful restore drops the persistent buffer-prompt marker (AFTER the
    extract, before the restart) so the boot service knows to retune the buffer."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(wiz.control, "HOME", str(home))
    monkeypatch.setattr(wiz, "ExtractWithProgress", lambda *a, **k: False)
    monkeypatch.setattr(wiz.ui, "ask_restart", lambda *a, **k: None)

    tools = wiz.tools
    # Ensure a clean slate.
    tools.clear_buffer_prompt_marker()
    assert not tools.buffer_prompt_pending()

    src = tmp_path / "backup.zip"
    _make_valid_zip(src, [("userdata/guisettings.xml", "<settings />")])

    wiz.restore(str(src), confirm=False, wipe=False)

    assert tools.buffer_prompt_pending(), "restore must drop the buffer-prompt marker"


def test_prompt_buffer_sets_recommended_and_clears(wiz, monkeypatch):
    """(b) with the marker present, choosing 'Set' calls _set_cache_mb(_recommended_mb())
    and deletes the marker (so it fires exactly once)."""
    tools = wiz.tools
    tools.mark_buffer_prompt_pending()
    assert tools.buffer_prompt_pending()

    monkeypatch.setattr(tools, "_recommended_mb", lambda: 128)
    sets = []
    monkeypatch.setattr(tools, "_set_cache_mb", lambda mb: sets.append(mb) or True)
    monkeypatch.setattr(tools.dialog, "select", lambda *a, **k: 0)

    shown = tools.prompt_buffer_after_restore()

    assert shown is True
    assert sets == [128], "must set the device-recommended size"
    assert not tools.buffer_prompt_pending(), "marker must be cleared after prompting"


def test_prompt_buffer_no_marker_no_prompt(wiz, monkeypatch):
    """(c) no marker => no prompt: the dialog is never shown and nothing is set."""
    tools = wiz.tools
    tools.clear_buffer_prompt_marker()
    assert not tools.buffer_prompt_pending()

    calls = []
    monkeypatch.setattr(
        tools.dialog, "select", lambda *a, **k: calls.append("select") or -1
    )
    monkeypatch.setattr(tools, "_set_cache_mb", lambda mb: calls.append("set") or True)

    shown = tools.prompt_buffer_after_restore()

    assert shown is False
    assert calls == [], "no marker must mean no dialog and no cache change"


def test_prompt_buffer_let_me_choose_opens_screen_and_clears(wiz, monkeypatch):
    """'Let me choose' routes to the existing Buffer Size screen and still clears the
    marker (so a manual choice also disarms the one-time prompt)."""
    tools = wiz.tools
    tools.mark_buffer_prompt_pending()

    opened = []
    monkeypatch.setattr(tools, "advancedSettings", lambda: opened.append(True))
    monkeypatch.setattr(
        tools,
        "_set_cache_mb",
        lambda mb: (_ for _ in ()).throw(
            AssertionError("must not auto-set on 'Let me choose'")
        ),
    )
    monkeypatch.setattr(tools.dialog, "select", lambda *a, **k: 1)

    shown = tools.prompt_buffer_after_restore()

    assert shown is True
    assert opened == [True]
    assert not tools.buffer_prompt_pending()


def test_prompt_buffer_keep_current_changes_nothing_but_clears(wiz, monkeypatch):
    """'Keep current' (or cancel) changes nothing yet still clears the marker."""
    tools = wiz.tools
    tools.mark_buffer_prompt_pending()

    monkeypatch.setattr(
        tools,
        "_set_cache_mb",
        lambda mb: (_ for _ in ()).throw(
            AssertionError("must not set the cache on 'Keep current'")
        ),
    )
    monkeypatch.setattr(
        tools,
        "advancedSettings",
        lambda: (_ for _ in ()).throw(
            AssertionError("must not open the screen on 'Keep current'")
        ),
    )
    monkeypatch.setattr(tools.dialog, "select", lambda *a, **k: 2)

    shown = tools.prompt_buffer_after_restore()

    assert shown is True
    assert not tools.buffer_prompt_pending()


def test_restore_no_wipe_still_overlays(wiz, monkeypatch, tmp_path):
    """(f) the normal (wipe=False) path is unchanged: it never wipes, it extracts, and it
    reaches the restart prompt."""
    onetap = _load_onetap()

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(wiz.control, "HOME", str(home))

    wiped = []
    monkeypatch.setattr(onetap, "_wipe", lambda *a, **k: wiped.append(a))

    extracted = []
    monkeypatch.setattr(
        wiz, "ExtractWithProgress", lambda *a, **k: extracted.append(True) or False
    )
    restarted = []
    monkeypatch.setattr(wiz.ui, "ask_restart", lambda *a, **k: restarted.append(True))

    src = tmp_path / "backup.zip"
    _make_valid_zip(src, [("userdata/guisettings.xml", "<settings />")])

    wiz.restore(str(src), confirm=False, wipe=False)

    assert wiped == [], "the no-wipe path must never wipe"
    assert extracted == [True], "the no-wipe path must still extract"
    assert restarted == [True], "the no-wipe path must still offer a restart"


# --------------------------------------------------------------------------- #
# Post-restore, per-device DEVICE-NAME prompt (runs before the buffer prompt in
# the combined post-restore tune-up, gated by the SAME marker).
# --------------------------------------------------------------------------- #
def test_get_devicename_reads_value(wiz, monkeypatch):
    """_get_devicename returns the live core-setting value."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_jsonrpc", lambda m, p: {"result": {"value": "Box7"}})
    assert tools._get_devicename() == "Box7"


def test_get_devicename_bad_shape_returns_empty(wiz, monkeypatch):
    """A JSON-RPC error / wrong id yields '' (never raises) so callers stay guarded."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_jsonrpc", lambda m, p: {})
    assert tools._get_devicename() == ""


def test_set_devicename_success_writes_both_live_and_file(wiz, monkeypatch):
    """A successful live set ALSO writes guisettings.xml (both-ways persistence: the live
    set is durable on tvOS, the file write survives a Fire TV / Android unclean shutdown)."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_jsonrpc", lambda m, p: {"result": True})
    from resources.lib.modules import _kodisettings

    wrote = []
    monkeypatch.setattr(
        _kodisettings,
        "write_guisetting",
        lambda path, sid, val: wrote.append((sid, val)) or True,
    )
    assert tools._set_devicename("NewName") is True
    assert wrote == [("services.devicename", "NewName")], "must persist to the file too"


def test_set_devicename_failure_does_not_touch_file(wiz, monkeypatch):
    """When the live set fails, the file is NOT written (no half-applied name on disk)."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_jsonrpc", lambda m, p: {"result": False})
    from resources.lib.modules import _kodisettings

    monkeypatch.setattr(
        _kodisettings,
        "write_guisetting",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not write the file when the live set failed")
        ),
    )
    assert tools._set_devicename("NewName") is False


def test_prompt_devicename_rename_sets_notifies_and_prefills(wiz, monkeypatch):
    """'Rename' -> keyboard PREFILLED with the current name -> _set_devicename(entered),
    a confirmation notification, returns True."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_get_devicename", lambda: "OfficeBox")
    monkeypatch.setattr(tools.dialog, "select", lambda *a, **k: 0)  # Rename

    kb = {}

    def fake_kb(default="", heading="", hidden=False, cancel=""):
        kb["default"] = default
        kb["cancel"] = cancel
        return "Living Room"

    monkeypatch.setattr(tools, "_get_keyboard", fake_kb)
    sets = []
    monkeypatch.setattr(tools, "_set_devicename", lambda n: sets.append(n) or True)
    notes = []
    monkeypatch.setattr(tools.dialog, "notification", lambda *a, **k: notes.append(a))

    assert tools.prompt_devicename_after_restore() is True
    assert sets == ["Living Room"]
    assert kb["default"] == "OfficeBox", (
        "keyboard must be prefilled with the current name"
    )
    assert kb["cancel"] == "OfficeBox", "cancel must fall back to the current name"
    assert notes, "a confirmation notification must be shown"


@pytest.mark.parametrize(
    "select_ret, kb_ret, why",
    [
        (1, "Living Room", "Keep"),
        (-1, "Living Room", "cancel/back on the first select"),
        (0, "", "empty entry"),
        (0, "   ", "whitespace-only entry"),
        (0, "OfficeBox", "name unchanged"),
    ],
)
def test_prompt_devicename_no_change_paths(wiz, monkeypatch, select_ret, kb_ret, why):
    """Every non-rename path leaves the device name untouched (no _set_devicename call)."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_get_devicename", lambda: "OfficeBox")
    monkeypatch.setattr(tools.dialog, "select", lambda *a, **k: select_ret)
    monkeypatch.setattr(tools, "_get_keyboard", lambda **k: kb_ret)
    monkeypatch.setattr(
        tools,
        "_set_devicename",
        lambda n: (_ for _ in ()).throw(AssertionError("must not set on: " + why)),
    )
    assert tools.prompt_devicename_after_restore() is False, why


def test_prompt_devicename_set_fails_shows_error_no_notification(wiz, monkeypatch):
    """A rejected name (live set returns False) surfaces an error and shows NO success
    notification (the silent-no-op gap the reviewers flagged)."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_get_devicename", lambda: "OfficeBox")
    monkeypatch.setattr(tools.dialog, "select", lambda *a, **k: 0)
    monkeypatch.setattr(tools, "_get_keyboard", lambda **k: "Living Room")
    monkeypatch.setattr(tools, "_set_devicename", lambda n: False)
    notes = []
    monkeypatch.setattr(tools.dialog, "notification", lambda *a, **k: notes.append(a))
    errs = []
    monkeypatch.setattr(tools.ui, "error", lambda *a, **k: errs.append(a))

    assert tools.prompt_devicename_after_restore() is False
    assert notes == [], "no success notification when the set failed"
    assert errs, "a failure message must be surfaced"


def test_prompt_devicename_notification_raise_still_true(wiz, monkeypatch):
    """If the set succeeds but the notification call raises, the rename still counts (True)."""
    tools = wiz.tools
    monkeypatch.setattr(tools, "_get_devicename", lambda: "OfficeBox")
    monkeypatch.setattr(tools.dialog, "select", lambda *a, **k: 0)
    monkeypatch.setattr(tools, "_get_keyboard", lambda **k: "Living Room")
    monkeypatch.setattr(tools, "_set_devicename", lambda n: True)

    def boom(*a, **k):
        raise RuntimeError("notification backend down")

    monkeypatch.setattr(tools.dialog, "notification", boom)
    assert tools.prompt_devicename_after_restore() is True


def test_prompt_after_restore_runs_devicename_before_buffer(wiz, monkeypatch):
    """The combined flow runs the device-name step BEFORE the buffer step (identity first)."""
    tools = wiz.tools
    tools.mark_buffer_prompt_pending()
    order = []
    monkeypatch.setattr(
        tools, "prompt_devicename_after_restore", lambda: order.append("devicename")
    )
    monkeypatch.setattr(
        tools, "prompt_buffer_after_restore", lambda: order.append("buffer") or True
    )
    assert tools.prompt_after_restore() is True
    assert order == ["devicename", "buffer"], order


def test_prompt_after_restore_no_marker_noop(wiz, monkeypatch):
    """No marker => neither step runs and nothing is prompted."""
    tools = wiz.tools
    tools.clear_buffer_prompt_marker()
    calls = []
    monkeypatch.setattr(
        tools, "prompt_devicename_after_restore", lambda: calls.append("d")
    )
    monkeypatch.setattr(
        tools, "prompt_buffer_after_restore", lambda: calls.append("b") or False
    )
    assert tools.prompt_after_restore() is False
    assert calls == []


def test_prompt_after_restore_devicename_raise_still_clears_marker(wiz, monkeypatch):
    """Exactly-once holds even if the device-name step RAISES: the buffer step still runs
    and clears the marker, so the whole flow never re-fires on the next boot."""
    tools = wiz.tools
    tools.mark_buffer_prompt_pending()

    def boom():
        raise RuntimeError("devicename step blew up")

    monkeypatch.setattr(tools, "prompt_devicename_after_restore", boom)
    monkeypatch.setattr(
        tools.dialog, "select", lambda *a, **k: 2
    )  # buffer: Keep current

    assert tools.prompt_after_restore() is True
    assert not tools.buffer_prompt_pending(), "marker must be cleared despite the raise"


def test_write_guisetting_updates_existing_and_clears_default(wiz, tmp_path):
    """write_guisetting overwrites an existing <setting> and drops its default='true' marker
    so Kodi treats the value as user-set."""
    import xml.etree.ElementTree as ET

    from resources.lib.modules import _kodisettings

    p = tmp_path / "guisettings.xml"
    p.write_text(
        '<settings version="2">'
        '<setting id="services.devicename" default="true">Kodi</setting>'
        "</settings>"
    )
    assert _kodisettings.write_guisetting(str(p), "services.devicename", "Living Room")
    node = [
        n
        for n in ET.parse(str(p)).getroot().iter("setting")
        if n.get("id") == "services.devicename"
    ][0]
    assert node.text == "Living Room"
    assert node.get("default") is None


def test_write_guisetting_creates_missing_element(wiz, tmp_path):
    """If the setting isn't present yet, it is created."""
    import xml.etree.ElementTree as ET

    from resources.lib.modules import _kodisettings

    p = tmp_path / "guisettings.xml"
    p.write_text(
        '<settings version="2"><setting id="other.thing">x</setting></settings>'
    )
    assert _kodisettings.write_guisetting(str(p), "services.devicename", "Box9")
    node = [
        n
        for n in ET.parse(str(p)).getroot().iter("setting")
        if n.get("id") == "services.devicename"
    ]
    assert node and node[0].text == "Box9"


def test_write_guisetting_missing_file_returns_false(wiz, tmp_path):
    """A missing guisettings.xml is a guarded no-op (returns False, never raises)."""
    from resources.lib.modules import _kodisettings

    assert (
        _kodisettings.write_guisetting(
            str(tmp_path / "nope.xml"), "services.devicename", "X"
        )
        is False
    )


# --------------------------------------------------------------------------- #
# Extract-root contract (bugs #1/#2/#3/#7): a restore must extract to the root the zip is
# anchored at, and drop stray HOME-root pollution.
# --------------------------------------------------------------------------- #
def test_archive_anchor_home_vs_userdata(wiz):
    assert wiz._archive_anchor(["userdata/guisettings.xml", "addons/x/y"]) == "home"
    assert (
        wiz._archive_anchor(
            ["guisettings.xml", "addon_data/pvr.iptvsimple/instance-settings-1.xml"]
        )
        == "userdata"
    )
    assert wiz._archive_anchor([], hint="home") == "home"
    assert wiz._archive_anchor([]) == "userdata"  # degenerate default


def test_extract_skip_predicate(wiz):
    skip_home = wiz._extract_skip("home", "temp/")
    assert skip_home("temp/x.zip") is True  # temp self-ref
    assert skip_home("userdata/guisettings.xml") is False  # allowed
    assert (
        skip_home("addon_data/pvr.iptvsimple/instance-settings-1.xml") is True
    )  # stray
    assert skip_home("guisettings.xml") is True  # stray root file
    skip_ud = wiz._extract_skip("userdata", None)
    # on a userdata anchor addon_data/ and guisettings.xml ARE the real content -> keep
    assert skip_ud("addon_data/pvr.iptvsimple/instance-settings-1.xml") is False
    assert skip_ud("guisettings.xml") is False


def _prep_restore(wiz, monkeypatch, tmp_path):
    """control.HOME + control.USERDATA as real tmp dirs; ask_restart stubbed."""
    home = tmp_path / "home"
    (home / "userdata").mkdir(parents=True)
    monkeypatch.setattr(wiz.control, "HOME", str(home))
    monkeypatch.setattr(wiz.control, "USERDATA", str(home / "userdata"))
    monkeypatch.setattr(wiz.ui, "ask_restart", lambda *a, **k: None)
    return home


def test_restore_userdata_zip_lands_under_userdata_not_home(wiz, monkeypatch, tmp_path):
    """THE regression guard: a userdata-anchored 'kodi_settings' zip must extract UNDER
    userdata/, never scattered at the HOME root (the bug that bricked the box)."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    src = tmp_path / "kodi_settings_202607081313.zip"
    _make_valid_zip(
        src,
        [
            ("guisettings.xml", "<settings/>"),
            ("addon_data/pvr.iptvsimple/instance-settings-1.xml", "<i/>"),
            ("sources.xml", "<sources/>"),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert (home / "userdata" / "guisettings.xml").exists()
    assert (
        home / "userdata" / "addon_data" / "pvr.iptvsimple" / "instance-settings-1.xml"
    ).exists()
    assert not (home / "guisettings.xml").exists(), "must NOT scatter into HOME root"
    assert not (home / "addon_data").exists(), "must NOT scatter into HOME root"


def test_restore_full_zip_still_lands_at_home(wiz, monkeypatch, tmp_path):
    """A home-anchored full backup extracts to HOME unchanged (regression guard)."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    src = tmp_path / "kodi_backup_202607081313.zip"
    _make_valid_zip(
        src,
        [
            ("userdata/guisettings.xml", "<settings/>"),
            ("addons/plugin.x/addon.xml", "<a/>"),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert (home / "userdata" / "guisettings.xml").exists()
    assert (home / "addons" / "plugin.x" / "addon.xml").exists()


def test_extract_filter_drops_stray_root_pollution(wiz, monkeypatch, tmp_path):
    """A polluted FULL backup carrying BOTH the real userdata/ copy AND stray root copies:
    only the userdata/ copies land; the strays are dropped (breaks the crash feedback loop)."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    src = tmp_path / "kodi_backup_polluted.zip"
    _make_valid_zip(
        src,
        [
            ("userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml", "<real/>"),
            ("userdata/guisettings.xml", "<settings/>"),
            ("addons/plugin.x/addon.xml", "<a/>"),
            # stray HOME-root pollution (must be dropped):
            ("addon_data/pvr.iptvsimple/instance-settings-1.xml", "<stray/>"),
            ("guisettings.xml", "<stray/>"),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert (
        home / "userdata" / "addon_data" / "pvr.iptvsimple" / "instance-settings-1.xml"
    ).exists()
    assert not (home / "addon_data").exists(), "stray root addon_data must be dropped"
    assert not (home / "guisettings.xml").exists(), (
        "stray root guisettings must be dropped"
    )


def test_createzip_prunes_home_root(wiz, tmp_path):
    """A FULL backup (prune_home_root=True) captures only allowed home-level dirs; stray
    root pollution is NOT re-captured. prune_home_root=False keeps everything (userdata mode)."""
    import zipfile as _zip

    home = tmp_path / "home"
    (home / "userdata" / "addon_data").mkdir(parents=True)
    (home / "userdata" / "guisettings.xml").write_text("<s/>")
    (home / "addons" / "plugin.x").mkdir(parents=True)
    (home / "addons" / "plugin.x" / "addon.xml").write_text("<a/>")
    # stray pollution at home root:
    (home / "addon_data" / "pvr.iptvsimple").mkdir(parents=True)
    (home / "addon_data" / "pvr.iptvsimple" / "instance-settings-1.xml").write_text(
        "<x/>"
    )
    (home / "guisettings.xml").write_text("<stray/>")

    class _P:
        def cancelled(self):
            return False

        def items(self, *a, **k):
            pass

    import contextlib

    @contextlib.contextmanager
    def _prog(*a, **k):
        yield _P()

    import unittest.mock as mock

    with mock.patch.object(wiz.ui, "Progress", _prog):
        out = tmp_path / "full.zip"
        wiz.CreateZip(
            str(home), str(out), "h", "m", ["temp"], [".log"], prune_home_root=True
        )
        names = set(_zip.ZipFile(out).namelist())
        out2 = tmp_path / "nopr.zip"
        wiz.CreateZip(str(home), str(out2), "h", "m", ["temp"], [".log"])
        names2 = set(_zip.ZipFile(out2).namelist())

    assert "userdata/guisettings.xml" in names and "addons/plugin.x/addon.xml" in names
    assert not any(n.startswith("addon_data/") for n in names), "stray root pruned"
    assert "guisettings.xml" not in names, "loose root file pruned"
    # without pruning the strays ARE captured (proves userdata-mode is unaffected):
    assert any(n.startswith("addon_data/") for n in names2)
    assert "guisettings.xml" in names2


def test_createzip_never_embeds_ezm_own_settings(wiz, tmp_path):
    """The backup must NEVER carry EZM's own settings.xml (Dropbox token + the
    source box's paths). Regression for the secret leak backup_lint caught on a
    real Fire TV 2026-07-16: the exclusion existed only on the tvOS NSUD path, not
    on the POSIX walk. Covers both a full and a userdata-mode backup, and a
    per-profile copy."""
    import contextlib
    import unittest.mock as mock
    import zipfile as _zip

    home = tmp_path / "home"
    ez = home / "userdata" / "addon_data" / "script.ezmaintenanceplusplus"
    ez.mkdir(parents=True)
    (ez / "settings.xml").write_text("<settings><token/></settings>")
    (ez / "data.json").write_text("{}")  # non-secret sibling: MUST be captured
    prof = (
        home
        / "userdata"
        / "profiles"
        / "kid"
        / "addon_data"
        / "script.ezmaintenanceplusplus"
    )
    prof.mkdir(parents=True)
    (prof / "settings.xml").write_text("<settings><token/></settings>")
    (home / "userdata" / "guisettings.xml").write_text("<s/>")

    class _P:
        def cancelled(self):
            return False

        def items(self, *a, **k):
            pass

    @contextlib.contextmanager
    def _prog(*a, **k):
        yield _P()

    with mock.patch.object(wiz.ui, "Progress", _prog):
        full = tmp_path / "full.zip"
        wiz.CreateZip(str(home), str(full), "h", "m", ["temp"], [".log"])
        names = set(_zip.ZipFile(full).namelist())

    secret_tail = "addon_data/script.ezmaintenanceplusplus/settings.xml"
    assert not any(n.endswith(secret_tail) for n in names), (
        "EZM's own settings.xml (top-level OR per-profile) must never be backed up"
    )
    # the non-secret sibling and other userdata are still captured
    assert any(n.endswith("script.ezmaintenanceplusplus/data.json") for n in names)
    assert any(n.endswith("userdata/guisettings.xml") for n in names)


def test_sweep_and_iptv_removed_from_wiz(wiz):
    """SAFETY BY CONSTRUCTION: the boot-time home-root delete sweep and all IPTV
    enable/disable/stage automation are gone from wiz. Nothing here deletes files at
    boot, and a restore never toggles the IPTV client (or any add-on). The ONLY
    IPTV-adjacent behavior left is the restore-side duplicate-instance sweep
    (_sweep_iptv_instances), which removes stale instance-settings-*.xml so the
    restored state equals the archive - covered by its own tests below."""
    # The sweep function no longer exists as an attribute (nothing can call it).
    assert not hasattr(wiz, "sweep_home_root_pollution")
    assert not hasattr(wiz, "_USERDATA_STRAY_NAMES")

    src = (ADDON_ROOT / "resources" / "lib" / "modules" / "wiz.py").read_text(
        encoding="utf-8"
    )
    for gone in (
        "stage_iptv_disabled",
        "mark_iptv_autoenable_pending",
        "set_pvr_enabled",
        "pvr_is_enabled",
        "def sweep_home_root_pollution",
    ):
        assert gone not in src, "wiz.py must no longer contain %r" % gone


def test_restore_does_not_toggle_any_addon(wiz, monkeypatch, tmp_path):
    """A restore must not enable or disable ANY add-on. The restored files are placed and the
    settings are made durable, but no client state is flipped (that is what crashed the box)."""
    _prep_restore(wiz, monkeypatch, tmp_path)

    calls = []
    monkeypatch.setattr(
        wiz.xbmc,
        "executeJSONRPC",
        lambda payload: calls.append(payload) or '{"result":"OK"}',
    )
    monkeypatch.setattr(wiz, "ExtractWithProgress", lambda *a, **k: False)  # completes

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>")])

    wiz.restore(str(src), confirm=False, wipe=False)

    assert not any("SetAddonEnabled" in c for c in calls), (
        "a restore must never enable/disable an add-on"
    )


def test_post_restore_step_numbers_match_the_order_they_run_in():
    """`prompt_after_restore` runs the device-name step FIRST, then the buffer step.
    The headings said the opposite, so the first thing a restored box asked was
    labelled "2 of 2"."""
    from pathlib import Path

    src = (
        Path(__file__).parent.parent
        / "script.ezmaintenanceplusplus"
        / "resources"
        / "lib"
        / "modules"
        / "tools.py"
    ).read_text(encoding="utf-8")

    flow = src[src.index("def prompt_after_restore") :]
    assert flow.index("prompt_devicename_after_restore()") < flow.index(
        "prompt_buffer_after_restore()"
    ), "device name runs first"

    assert '"Finish setup (1 of 2): Device name"' in src
    assert '"Finish setup (2 of 2): Video quality"' in src


# --------------------------------------------------------------------------- #
# Honest backup: per-file failure accounting (A), manifest (C), root-only temp
# exclusion (D), and the loud tvOS capture contract (B).
# --------------------------------------------------------------------------- #
class _NoopProgress:
    def cancelled(self):
        return False

    def items(self, *a, **k):
        pass


def _run_create_zip(wiz, src, dst, exclude_dirs=("temp",), prune=False):
    """Drive the REAL CreateZip with ui.Progress mocked out (no dialog)."""
    import contextlib
    import unittest.mock as mock

    @contextlib.contextmanager
    def _prog(*a, **k):
        yield _NoopProgress()

    with mock.patch.object(wiz.ui, "Progress", _prog):
        return wiz.CreateZip(
            str(src),
            str(dst),
            "h",
            "m",
            list(exclude_dirs),
            [".log"],
            prune_home_root=prune,
        )


def _load_nsub():
    return importlib.import_module("resources.lib.modules.nsub")


def _home_with(tmp_path, files, name="srchome"):
    """A home tree with the given (relpath, body) files; returns its Path."""
    home = tmp_path / name
    for rel, body in files:
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return home


def test_createzip_per_file_failure_keeps_rest_of_directory(wiz, tmp_path):
    """(A) One unreadable file is COUNTED and NAMED; it never silently drops the
    rest of its directory (the old per-directory except swallowed every file after
    the first failure)."""
    import os as _os
    import zipfile as _zip

    if _os.geteuid() == 0:
        pytest.skip("running as root: chmod 000 cannot make a file unreadable")
    home = _home_with(
        tmp_path,
        [
            ("userdata/a.xml", "<a/>"),
            ("userdata/b.xml", "<b/>"),
            ("userdata/c.xml", "<c/>"),
        ],
    )
    (home / "userdata" / "b.xml").chmod(0)
    out = tmp_path / "out.zip"
    try:
        result = _run_create_zip(wiz, home, out)
    finally:
        (home / "userdata" / "b.xml").chmod(0o644)

    names = set(_zip.ZipFile(out).namelist())
    assert "userdata/a.xml" in names and "userdata/c.xml" in names, (
        "the files AFTER the unreadable one must still be captured"
    )
    assert "userdata/b.xml" not in names
    assert result.canceled is False
    assert result.failed == ["userdata/b.xml"], "the failure must be named"


def test_createzip_manifest_records_failures(wiz, tmp_path):
    """(C) The embedded manifest carries created/source_os/entries/failed, with
    failed naming exactly what the backup could not capture."""
    import json as _json
    import os as _os
    import zipfile as _zip

    if _os.geteuid() == 0:
        pytest.skip("running as root: chmod 000 cannot make a file unreadable")
    home = _home_with(
        tmp_path, [("userdata/good.xml", "<g/>"), ("userdata/bad.xml", "<b/>")]
    )
    (home / "userdata" / "bad.xml").chmod(0)
    out = tmp_path / "out.zip"
    try:
        result = _run_create_zip(wiz, home, out)
    finally:
        (home / "userdata" / "bad.xml").chmod(0o644)

    with _zip.ZipFile(out) as z:
        names = z.namelist()
        assert wiz.MANIFEST_NAME in names
        manifest = _json.loads(z.read(wiz.MANIFEST_NAME).decode("utf-8"))
    assert manifest["source_os"] == "other"
    assert manifest["failed"] == ["userdata/bad.xml"]
    assert manifest["entries"] == len([n for n in names if n != wiz.MANIFEST_NAME])
    assert manifest["created"], "an ISO created stamp must be present"
    assert result.failed == ["userdata/bad.xml"]


def test_createzip_manifest_clean_backup(wiz, tmp_path):
    """(C) A clean backup's manifest has an accurate entry count and no failures."""
    import json as _json
    import zipfile as _zip

    home = _home_with(
        tmp_path, [("userdata/a.xml", "<a/>"), ("addons/x/addon.xml", "<x/>")]
    )
    out = tmp_path / "out.zip"
    result = _run_create_zip(wiz, home, out)

    with _zip.ZipFile(out) as z:
        names = z.namelist()
        manifest = _json.loads(z.read(wiz.MANIFEST_NAME).decode("utf-8"))
    assert manifest["failed"] == []
    assert manifest["entries"] == 2
    assert len([n for n in names if n != wiz.MANIFEST_NAME]) == 2
    assert result.failed == [] and result.entries == 2


def test_createzip_prunes_temp_only_at_walk_root(wiz, tmp_path):
    """(D) exclude_dirs=["temp"] means special://home/temp - the WALK ROOT only. A
    nested dir that merely happens to be named temp is real content."""
    import zipfile as _zip

    home = _home_with(
        tmp_path,
        [
            ("temp/junk.txt", "x"),
            ("userdata/addon_data/plugin.x/temp/keep.txt", "y"),
        ],
    )
    out = tmp_path / "out.zip"
    _run_create_zip(wiz, home, out)

    names = set(_zip.ZipFile(out).namelist())
    assert "userdata/addon_data/plugin.x/temp/keep.txt" in names, (
        "a NESTED temp dir must be captured"
    )
    assert not any(n.startswith("temp/") for n in names), (
        "the root temp dir must be excluded"
    )


def test_createzip_tvos_capture_exception_fails_backup(wiz, monkeypatch, tmp_path):
    """(B) On tvOS a raising NSUserDefaults capture FAILS the backup loudly
    (BackupCaptureError) and removes the partial zip."""
    nsub = _load_nsub()
    monkeypatch.setattr(wiz, "_source_os", lambda: "tvos")

    def boom(*a, **k):
        raise RuntimeError("plist unreadable")

    monkeypatch.setattr(nsub, "capture_nsud_userdata", boom)
    home = _home_with(tmp_path, [("userdata/a.xml", "<a/>")])
    out = tmp_path / "out.zip"
    with pytest.raises(wiz.BackupCaptureError):
        _run_create_zip(wiz, home, out)
    assert not out.exists(), "the partial zip must be removed on a failed capture"


def test_createzip_tvos_capture_failed_entries_fail_backup(wiz, monkeypatch, tmp_path):
    """(B) On tvOS a capture reporting failed entries fails the backup - the zip
    would be missing settings the owner cares about."""
    nsub = _load_nsub()
    monkeypatch.setattr(wiz, "_source_os", lambda: "tvos")
    monkeypatch.setattr(nsub, "capture_nsud_userdata", lambda *a, **k: (3, 2, 1))
    home = _home_with(tmp_path, [("userdata/a.xml", "<a/>")])
    with pytest.raises(wiz.BackupCaptureError):
        _run_create_zip(wiz, home, tmp_path / "out.zip")


def test_createzip_tvos_missing_store_fails_backup(wiz, monkeypatch, tmp_path):
    """(B) On tvOS the NSUserDefaults store ALWAYS exists; a capture that finds
    nothing at all means it was never read - fail the backup."""
    nsub = _load_nsub()
    monkeypatch.setattr(wiz, "_source_os", lambda: "tvos")
    monkeypatch.setattr(nsub, "capture_nsud_userdata", lambda *a, **k: (0, 0, 0))
    home = _home_with(tmp_path, [("userdata/a.xml", "<a/>")])
    with pytest.raises(wiz.BackupCaptureError):
        _run_create_zip(wiz, home, tmp_path / "out.zip")


def test_createzip_non_tvos_capture_error_is_noop(wiz, monkeypatch, tmp_path):
    """(B) Off tvOS the capture is a true no-op: a hiccup is logged, the backup
    completes cleanly with no failures recorded."""
    import zipfile as _zip

    nsub = _load_nsub()

    def boom(*a, **k):
        raise RuntimeError("no plist here")

    monkeypatch.setattr(nsub, "capture_nsud_userdata", boom)
    home = _home_with(tmp_path, [("userdata/a.xml", "<a/>")])
    out = tmp_path / "out.zip"
    result = _run_create_zip(wiz, home, out)
    assert result.canceled is False and result.failed == []
    names = set(_zip.ZipFile(out).namelist())
    assert "userdata/a.xml" in names and wiz.MANIFEST_NAME in names


def _stub_local_backup_env(wiz, monkeypatch, tmp_path, home):
    """backup() stubs with a LOCAL download.path (no VFS ship) over a real home."""
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setattr(wiz.control, "HOME", str(home))
    monkeypatch.setattr(
        wiz.control,
        "setting",
        lambda key: str(dest) if key == "download.path" else "",
    )
    monkeypatch.setattr(wiz.tools, "_get_keyboard", lambda **k: "mybackup")
    monkeypatch.setattr(wiz.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(
        wiz,
        "xbmcaddon",
        types.SimpleNamespace(
            Addon=lambda: types.SimpleNamespace(getSetting=lambda k: "false")
        ),
    )
    return dest


def test_backup_tvos_capture_failure_no_success_no_rotation(wiz, monkeypatch, tmp_path):
    """(B) backup(): a tvOS capture failure surfaces an error dialog, never claims
    success, and never rotates the previous good backup."""
    nsub = _load_nsub()
    home = _home_with(tmp_path, [("userdata/a.xml", "<a/>")], name="home")
    _stub_local_backup_env(wiz, monkeypatch, tmp_path, home)
    monkeypatch.setattr(wiz, "_source_os", lambda: "tvos")

    def boom(*a, **k):
        raise RuntimeError("plist unreadable")

    monkeypatch.setattr(nsub, "capture_nsud_userdata", boom)
    rotations = []
    monkeypatch.setattr(wiz, "_rotate_vfs", lambda *a, **k: rotations.append(a))
    oks = []
    monkeypatch.setattr(
        wiz.dialog, "ok", lambda *a, **k: oks.append(" ".join(map(str, a)))
    )

    wiz.backup(mode="full")

    assert rotations == [], "a failed backup must never rotate the previous one"
    assert oks, "an error dialog must be shown"
    assert "FAILED" in oks[-1]
    assert not any("Backup complete" in m for m in oks)


def test_backup_reports_uncaptured_files_before_claiming_success(
    wiz, monkeypatch, tmp_path
):
    """(A/C) backup(): a backup with unreadable files says EXACTLY what is missing
    in the completion dialog instead of a bare 'Backup complete'."""
    import os as _os

    if _os.geteuid() == 0:
        pytest.skip("running as root: chmod 000 cannot make a file unreadable")
    home = _home_with(
        tmp_path,
        [("userdata/good.xml", "<g/>"), ("userdata/bad.xml", "<b/>")],
        name="home",
    )
    _stub_local_backup_env(wiz, monkeypatch, tmp_path, home)
    monkeypatch.setattr(wiz, "_rotate_vfs", lambda *a, **k: None)
    oks = []
    monkeypatch.setattr(
        wiz.dialog, "ok", lambda *a, **k: oks.append(" ".join(map(str, a)))
    )
    (home / "userdata" / "bad.xml").chmod(0)
    try:
        wiz.backup(mode="full")
    finally:
        (home / "userdata" / "bad.xml").chmod(0o644)

    assert oks, "a completion dialog must be shown"
    assert "userdata/bad.xml" in oks[-1], "the missing file must be NAMED"
    assert "could NOT be captured" in oks[-1]


# --------------------------------------------------------------------------- #
# Truthful restore reporting (E) + manifest verification (F).
# --------------------------------------------------------------------------- #
def _record_restore_report(wiz, monkeypatch, retry=False):
    """Capture ask_restart statuses, dialog.ok messages, and dialog.yesno prompts
    from restore(). `retry` is the canned answer to the locked Try Again prompt."""
    statuses = []
    monkeypatch.setattr(
        wiz.ui, "ask_restart", lambda status="", **k: statuses.append(status)
    )
    oks = []
    monkeypatch.setattr(
        wiz.dialog, "ok", lambda *a, **k: oks.append(" ".join(map(str, a)))
    )
    yesnos = []

    def _yesno(*a, **k):
        yesnos.append(" ".join(str(x) for x in a))
        return retry

    monkeypatch.setattr(wiz.dialog, "yesno", _yesno)
    return statuses, oks, yesnos


def test_restore_member_failure_asks_with_locked_problem_copy(
    wiz, monkeypatch, tmp_path
):
    """(E) A member that fails to extract is a HARD problem: the user sees the
    LOCKED Problem prompt (Try Again / Close) and nothing else - no counts, no
    paths, no 'INCOMPLETE' (those live in the log). Declining still drives the
    restart prompt and never claims Complete."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch, retry=False)
    # Extraction target for blocked.xml is an existing DIRECTORY -> extract fails.
    (home / "userdata" / "blocked.xml").mkdir(parents=True)

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>"), ("blocked.xml", "<b/>")])

    wiz.restore(str(src), confirm=False)

    assert yesnos and wiz.MSG_PROBLEM in yesnos[0], yesnos
    assert oks == [], "declining Try Again must not stack another dialog"
    assert statuses == [""], "the restart prompt still runs, with no status jargon"
    for shown in yesnos + statuses:
        assert "INCOMPLETE" not in shown and "blocked.xml" not in shown


def test_restore_member_failure_retry_twice_then_problem(wiz, monkeypatch, tmp_path):
    """(E) Accepting Try Again re-runs the whole restore once; a second HARD failure
    (backup content still did not restore) shows the locked PROBLEM wording - never a
    third attempt, never the softer needs-attention (audit Finding C: a hard content
    loss must say 'couldn't be restored', not 'needs attention')."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch, retry=True)
    (home / "userdata" / "blocked.xml").mkdir(parents=True)

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>"), ("blocked.xml", "<b/>")])

    wiz.restore(str(src), confirm=False)

    assert len(yesnos) == 1, "the Problem prompt asks exactly once (2-attempt cap)"
    assert oks == [wiz.AddonTitle + " " + wiz.MSG_PROBLEM]
    assert statuses == [""]


def test_restore_success_shows_only_locked_complete(wiz, monkeypatch, tmp_path):
    """(E) A clean restore shows EXACTLY the locked Complete status - no counts,
    no settings tally, no extra dialogs. The numbers live in the log."""
    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(
        src,
        [
            ("guisettings.xml", "<s/>"),
            ("sources.xml", "<s/>"),
            ("RssFeeds.xml", "<r/>"),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert statuses == [wiz.MSG_COMPLETE], statuses
    assert oks == [] and yesnos == []


def test_restore_manifest_mismatch_reports_partial(wiz, monkeypatch, tmp_path):
    """(F) A manifest whose entry count does not match the archive is surfaced as a
    problem: the report is INCOMPLETE even though every member extracted."""
    import json as _json

    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch, retry=False)

    src = tmp_path / "kodi_settings_x.zip"
    manifest = {"created": "t", "source_os": "tvos", "entries": 5, "failed": []}
    _make_valid_zip(
        src,
        [
            ("guisettings.xml", "<s/>"),
            (wiz.MANIFEST_NAME, _json.dumps(manifest)),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert yesnos and wiz.MSG_PROBLEM in yesnos[0], yesnos
    assert statuses == [""]
    for shown in yesnos + oks + statuses:
        assert "manifest" not in shown, "manifest detail belongs in the log"


def test_restore_manifest_backup_gaps_surface(wiz, monkeypatch, tmp_path):
    """(F) A manifest recording backup-time failures tells the user the RESTORE
    cannot contain those items - surfaced, never silently dropped."""
    import json as _json

    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch, retry=False)

    src = tmp_path / "kodi_settings_x.zip"
    manifest = {
        "created": "t",
        "source_os": "tvos",
        "entries": 1,
        "failed": ["userdata/secret.xml"],
    }
    _make_valid_zip(
        src,
        [
            ("guisettings.xml", "<s/>"),
            (wiz.MANIFEST_NAME, _json.dumps(manifest)),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert yesnos and wiz.MSG_PROBLEM in yesnos[0], yesnos
    assert statuses == [""]
    for shown in yesnos + oks + statuses:
        assert "secret.xml" not in shown, "the failed path belongs in the log"


def test_restore_matching_manifest_reports_complete_and_skips_manifest(
    wiz, monkeypatch, tmp_path
):
    """(F) A consistent manifest verifies cleanly; the manifest member itself is
    metadata and is never extracted to disk. Archives WITHOUT a manifest are
    tolerated (covered by the other restore tests)."""
    import json as _json

    home = _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, _oks, _yesnos = _record_restore_report(wiz, monkeypatch)

    src = tmp_path / "kodi_settings_x.zip"
    manifest = {"created": "t", "source_os": "other", "entries": 2, "failed": []}
    _make_valid_zip(
        src,
        [
            ("guisettings.xml", "<s/>"),
            ("sources.xml", "<s/>"),
            (wiz.MANIFEST_NAME, _json.dumps(manifest)),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert statuses == [wiz.MSG_COMPLETE], statuses
    assert not (home / "userdata" / wiz.MANIFEST_NAME).exists()
    assert not (home / wiz.MANIFEST_NAME).exists()


def test_restore_complete_despite_stale_key_purge_failures(wiz, monkeypatch, tmp_path):
    """A stale-key purge that cannot clear PRE-EXISTING vector-everything-era keys
    (undecodable, or a tvOS async-flush confirm miss) must NOT downgrade the
    restore to INCOMPLETE - the purge is hygiene of old cruft this restore did not
    create. Regression for the false 'Restore INCOMPLETE' seen on atv2 2026-07-16,
    where extract/sweep/rewrite were all 0-failed but the purge reported failures."""
    from resources.lib.modules import nsud

    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)
    # The purge reports 3 UNRESOLVED pre-existing keys; the rewrite is clean.
    monkeypatch.setattr(nsud, "purge_stale_keys", lambda root, log=None: (0, 5, 2, 3))
    monkeypatch.setattr(
        nsud, "rewrite_userdata_xml", lambda root, log=None: (0, 0, 0, 0)
    )

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>"), ("sources.xml", "<s/>")])

    wiz.restore(str(src), confirm=False)

    assert statuses and statuses[0].startswith("Restore Complete"), statuses
    assert not any(s.startswith("Restore INCOMPLETE") for s in statuses)
    assert oks == []  # no INCOMPLETE dialog


def test_backup_restore_roundtrip_reports_complete(wiz, monkeypatch, tmp_path):
    """End-to-end: a real CreateZip backup (manifest included) restores with a
    clean manifest verification and a truthful Complete report."""
    srchome = _home_with(
        tmp_path,
        [("userdata/guisettings.xml", "<s/>"), ("addons/plugin.x/addon.xml", "<a/>")],
    )
    out = tmp_path / "kodi_backup_202607161200.zip"
    _run_create_zip(wiz, srchome, out, prune=True)

    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)

    wiz.restore(str(out), confirm=False)

    assert statuses == [wiz.MSG_COMPLETE], statuses
    assert oks == [] and yesnos == []


# --------------------------------------------------------------------------- #
# Restore-side IPTV duplicate-instance sweep (G).
# --------------------------------------------------------------------------- #
def test_iptv_profile_prefixes(wiz):
    """The sweep scope comes from the ARCHIVE: top-level and/or per-profile, and
    only when pvr.iptvsimple addon_data is actually present."""
    assert wiz._iptv_profile_prefixes(
        ["userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml"], "home"
    ) == {""}
    assert wiz._iptv_profile_prefixes(
        [
            "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml",
            "userdata/profiles/Kids/addon_data/pvr.iptvsimple/instance-settings-2.xml",
        ],
        "home",
    ) == {"", "profiles/Kids/"}
    assert wiz._iptv_profile_prefixes(
        ["addon_data/pvr.iptvsimple/customTVGroups.xml"], "userdata"
    ) == {""}
    # home anchor: a bare addon_data/ member is stray pollution, not userdata content
    assert (
        wiz._iptv_profile_prefixes(
            ["addon_data/pvr.iptvsimple/instance-settings-1.xml"], "home"
        )
        == set()
    )
    assert wiz._iptv_profile_prefixes(["userdata/guisettings.xml"], "home") == set()


def test_restore_sweeps_stale_iptv_instances(wiz, monkeypatch, tmp_path):
    """(G) When the archive carries pvr.iptvsimple config, the TARGET's existing
    instance-settings-*.xml are removed first, so instance numbering can never
    accumulate (the 2026-07-08 duplicate-instance brick). settings.xml and the
    archive's own instance files land normally."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    iptv = home / "userdata" / "addon_data" / "pvr.iptvsimple"
    iptv.mkdir(parents=True)
    (iptv / "instance-settings-1.xml").write_text("<old/>")
    (iptv / "instance-settings-7.xml").write_text("<stale/>")
    (iptv / "settings.xml").write_text("<keep/>")

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(
        src,
        [
            ("addon_data/pvr.iptvsimple/instance-settings-1.xml", "<new/>"),
            ("guisettings.xml", "<s/>"),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert not (iptv / "instance-settings-7.xml").exists(), (
        "a stale instance NOT in the archive must be swept"
    )
    assert (iptv / "instance-settings-1.xml").read_text() == "<new/>"
    assert (iptv / "settings.xml").read_text() == "<keep/>", (
        "the sweep only touches instance-settings-*.xml"
    )


def test_restore_without_iptv_leaves_target_instances_alone(wiz, monkeypatch, tmp_path):
    """(G) No pvr.iptvsimple entries in the archive -> no sweep: the target's IPTV
    config is not EZM's to touch."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    iptv = home / "userdata" / "addon_data" / "pvr.iptvsimple"
    iptv.mkdir(parents=True)
    (iptv / "instance-settings-7.xml").write_text("<keep/>")

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>")])

    wiz.restore(str(src), confirm=False)

    assert (iptv / "instance-settings-7.xml").read_text() == "<keep/>"


def test_restore_sweep_scopes_per_profile_to_archive(wiz, monkeypatch, tmp_path):
    """(G) Per-profile sweep only for profiles the archive carries; a top-level
    instance file survives when the archive has no top-level IPTV entries."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    top = home / "userdata" / "addon_data" / "pvr.iptvsimple"
    top.mkdir(parents=True)
    (top / "instance-settings-8.xml").write_text("<top/>")
    kids = home / "userdata" / "profiles" / "Kids" / "addon_data" / "pvr.iptvsimple"
    kids.mkdir(parents=True)
    (kids / "instance-settings-9.xml").write_text("<stale/>")

    src = tmp_path / "kodi_backup_x.zip"
    _make_valid_zip(
        src,
        [
            (
                "userdata/profiles/Kids/addon_data/pvr.iptvsimple/"
                "instance-settings-1.xml",
                "<k/>",
            ),
            ("addons/plugin.x/addon.xml", "<a/>"),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert not (kids / "instance-settings-9.xml").exists(), (
        "the archive's profile must be swept"
    )
    assert (kids / "instance-settings-1.xml").read_text() == "<k/>"
    assert (top / "instance-settings-8.xml").read_text() == "<top/>", (
        "a profile the archive does NOT carry is left alone"
    )


def test_restore_sweep_drops_nsud_key_without_posix_file(wiz, monkeypatch, tmp_path):
    """(G, tvOS) An instance-settings key that exists ONLY in NSUserDefaults (no
    disk file) is still swept: the key is dropped via the special:// path (the
    sanctioned two-layer delete). Non-IPTV keys are never touched."""
    _prep_restore(wiz, monkeypatch, tmp_path)
    # The sweep machinery lives in nsud (wiz delegates); patch ITS plist reader.
    from resources.lib.modules import nsud

    store = {
        "/userdata/addon_data/pvr.iptvsimple/instance-settings-3.xml": b"x",
        "/userdata/guisettings.xml": b"g",
    }
    monkeypatch.setattr(nsud, "_find_nsud_plist", lambda: ("plist", store))
    monkeypatch.setattr(nsud, "_load_plist", lambda _p: {})  # post-delete re-read: gone
    deleted = []
    monkeypatch.setattr(nsud.xbmcvfs, "delete", lambda p: deleted.append(p))

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(
        src,
        [
            ("addon_data/pvr.iptvsimple/instance-settings-1.xml", "<new/>"),
            ("guisettings.xml", "<s/>"),
        ],
    )

    wiz.restore(str(src), confirm=False)

    assert (
        "special://home/userdata/addon_data/pvr.iptvsimple/instance-settings-3.xml"
        in deleted
    ), "the key-only stale instance must be dropped"
    assert not any("guisettings" in d for d in deleted), (
        "the sweep must never delete non-IPTV userdata"
    )


# --------------------------------------------------------------------------- #
# The restore UX contract (owner-locked 2026-07-17): four messages total, no
# jargon, verification before reporting, silent auto-fix, skin as boot state.
# Born from the atv2 round-trip where the honest-but-raw reporting read as
# breakage and its modal ate Kodi's keep-skin confirmation.
# --------------------------------------------------------------------------- #
def test_locked_vocabulary_is_pinned(wiz):
    """The owner-edited strings, byte for byte. Implementation may not reword."""
    assert wiz.MSG_COMPLETE == "Restore Complete"
    assert wiz.MSG_PROBLEM == (
        "Restore Problem\n"
        "Some of your backup couldn't be restored, so this box may not work "
        "the way it did before."
    )
    assert wiz.MSG_NEEDS_ATTENTION == (
        "Restore Problem\nThis box needs attention - open EZ Maintenance++."
    )


def test_attention_only_findings_auto_fix_silently(wiz, monkeypatch, tmp_path):
    """A fixable finding (e.g. a surviving stale key) triggers ONE silent fresh
    pass - no dialog, no question ("when this occurs, we should just fix it").
    When the second pass verifies clean, the user only ever sees Complete."""
    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)

    from resources.lib.modules import restorecheck

    calls = []

    def _fake_verify(leftovers, names, anchor):
        calls.append(1)
        if len(calls) == 1:
            return (["1 stale NSUserDefaults key(s) still shadow restored"], [])
        return ([], [])

    monkeypatch.setattr(restorecheck, "verify_restored_state", _fake_verify)

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>"), ("sources.xml", "<s/>")])
    wiz.restore(str(src), confirm=False)

    assert len(calls) == 2, "exactly one silent auto-fix pass"
    assert yesnos == [] and oks == [], "the auto-fix never surfaces a dialog"
    assert statuses == [wiz.MSG_COMPLETE]


def test_attention_surviving_auto_fix_needs_attention(wiz, monkeypatch, tmp_path):
    """If the silent fresh pass cannot clear the finding, the user sees exactly
    the locked needs-attention line - no counts, no key paths."""
    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)

    from resources.lib.modules import restorecheck

    monkeypatch.setattr(
        restorecheck,
        "verify_restored_state",
        lambda *a, **k: (["1 stale key still shadows userdata/x.xml"], []),
    )

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>")])
    wiz.restore(str(src), confirm=False)

    assert yesnos == [], "attention findings never ask - they auto-fix"
    assert oks == [wiz.AddonTitle + " " + wiz.MSG_NEEDS_ATTENTION]
    assert statuses == [""]
    assert "x.xml" not in oks[0], "key paths belong in the log"


def test_restore_arms_both_boot_markers(wiz, monkeypatch, tmp_path):
    """A finished restore arms the tune-up marker AND the new restore self-check
    marker (final certainty lives after the restart, where settings are live)."""
    _prep_restore(wiz, monkeypatch, tmp_path)
    _record_restore_report(wiz, monkeypatch)
    armed = []
    monkeypatch.setattr(
        wiz.tools, "mark_buffer_prompt_pending", lambda: armed.append("buffer")
    )
    monkeypatch.setattr(
        wiz.tools, "mark_restore_check_pending", lambda: armed.append("check")
    )

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>")])
    wiz.restore(str(src), confirm=False)

    assert armed == ["buffer", "check"]


def _record_window_props(wiz, monkeypatch):
    """Swap the fake Window for a recorder so a test can read Window(10000) properties
    (notably ezm_boot_skin). Returns the shared prop dict."""
    props = {}

    class _RecWindow:
        def __init__(self, *a, **k):
            pass

        def setProperty(self, key, value):
            props[key] = value

        def getProperty(self, key):
            return props.get(key, "")

        def clearProperty(self, key):
            props.pop(key, None)

    monkeypatch.setattr(wiz.xbmcgui, "Window", _RecWindow, raising=False)
    return props


def _no_skin_live_switch(monkeypatch, wiz):
    """Record every JSON-RPC + builtin so a test can prove _apply_boot_skin does NO live
    skin switch and answers NO keep-skin dialog. Returns (rpc_calls, builtins)."""
    rpc = []
    builtins = []

    def _jsonrpc(payload):
        rpc.append(payload)
        return "{}"

    monkeypatch.setattr(wiz.xbmc, "executeJSONRPC", _jsonrpc, raising=False)
    monkeypatch.setattr(
        wiz.xbmc, "executebuiltin", lambda cmd: builtins.append(cmd), raising=False
    )
    return rpc, builtins


def test_boot_skin_persists_restored_skin_to_disk_no_live_switch(
    wiz, monkeypatch, tmp_path
):
    """THE fix (atv2, 2026-07-17): the restored skin is PERSISTED, never live-switched.

    _apply_boot_skin writes the captured skin straight into guisettings.xml on disk (so a
    force-quit reopen boots it) and does NOT live-set lookandfeel.skin or answer any
    keep-skin dialog - the flaky mechanism that reverted the box to stock is gone."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    gp = home / "userdata" / "guisettings.xml"
    # Simulate the post-apply_guisettings state: the on-disk file carries STOCK.
    gp.write_text(
        '<settings><setting id="lookandfeel.skin">skin.estuary</setting></settings>'
    )
    props = _record_window_props(wiz, monkeypatch)
    rpc, builtins = _no_skin_live_switch(monkeypatch, wiz)

    persisted = []
    from resources.lib.modules import nsud

    monkeypatch.setattr(
        nsud, "persist_one", lambda rel, log=None: persisted.append(rel) or True
    )

    logs = []
    wiz._apply_boot_skin(lambda m: logs.append(m), "skin.estuary7")

    # (1) written straight to disk (write_guisetting), the last-step durable write.
    import xml.etree.ElementTree as ET

    root = ET.parse(str(gp)).getroot()
    got = next(
        n.text for n in root.iter("setting") if n.get("id") == "lookandfeel.skin"
    )
    assert got == "skin.estuary7", "the restored skin must be written to disk"
    # (2) vectored into NSUserDefaults via persist_one (no-op off tvOS).
    assert persisted == ["guisettings.xml"]
    # (3) NO live Settings.SetSettingValue for the skin, NO SendClick / keep-skin nav.
    assert not any("SetSettingValue" in p for p in rpc), "no live skin switch"
    assert not any("Settings." in p for p in rpc), "no live settings RPC at all"
    assert builtins == [], "no SendClick / navigation / keep-skin handling remains"
    # A readable diagnostic is published for JSON-RPC inspection.
    assert props.get("ezm_boot_skin") == "written:skin.estuary7"


def test_boot_skin_vectors_via_persist_one_on_tvos(wiz, monkeypatch, tmp_path):
    """The tvOS durability path: the restored skin is vectored into NSUserDefaults via
    nsud.persist_one('guisettings.xml') - the same tvOS-safe primitive boxsetup uses."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    (home / "userdata" / "guisettings.xml").write_text(
        '<settings><setting id="lookandfeel.skin">skin.estuary</setting></settings>'
    )
    _record_window_props(wiz, monkeypatch)
    _no_skin_live_switch(monkeypatch, wiz)

    from resources.lib.modules import nsud

    calls = []
    monkeypatch.setattr(
        nsud, "persist_one", lambda rel, log=None: calls.append(rel) or True
    )

    wiz._apply_boot_skin(lambda m: None, "skin.estuary7")
    assert calls == ["guisettings.xml"], (
        "guisettings.xml must be vectored into NSUserDefaults (persist_one) on tvOS"
    )


def test_boot_skin_missing_or_empty_is_a_clean_noop(wiz, monkeypatch, tmp_path):
    """A missing / absent / empty lookandfeel.skin means there is no skin to assert:
    _read_target_skin returns None and _apply_boot_skin touches nothing, reporting 'none'."""
    _prep_restore(wiz, monkeypatch, tmp_path)
    props = _record_window_props(wiz, monkeypatch)
    rpc, builtins = _no_skin_live_switch(monkeypatch, wiz)

    from resources.lib.modules import _kodisettings, nsud

    monkeypatch.setattr(
        _kodisettings,
        "write_guisetting",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not write on no-op")
        ),
    )
    monkeypatch.setattr(
        nsud,
        "persist_one",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not vector on no-op")
        ),
    )

    for empty in (None, "", "   "):
        wiz._apply_boot_skin(lambda m: None, empty)
    assert rpc == [] and builtins == [], "a no-op never touches Kodi"
    assert props.get("ezm_boot_skin") == "none"


def test_read_target_skin_captures_absent_and_present(wiz, tmp_path):
    """_read_target_skin: the archive's skin when present, None when the file is missing,
    unparseable, or the setting is absent / empty."""
    present = tmp_path / "present.xml"
    present.write_text(
        '<settings><setting id="lookandfeel.skin">skin.estuary7</setting></settings>'
    )
    assert wiz._read_target_skin(str(present)) == "skin.estuary7"

    absent_setting = tmp_path / "absent.xml"
    absent_setting.write_text('<settings><setting id="other">x</setting></settings>')
    assert wiz._read_target_skin(str(absent_setting)) is None

    empty_setting = tmp_path / "empty.xml"
    empty_setting.write_text(
        '<settings><setting id="lookandfeel.skin">   </setting></settings>'
    )
    assert wiz._read_target_skin(str(empty_setting)) is None

    assert wiz._read_target_skin(str(tmp_path / "does-not-exist.xml")) is None


def test_boot_skin_failure_never_breaks_the_restore(wiz, monkeypatch, tmp_path):
    """Fully guarded: a raising write_guisetting only logs and records failed:<Error>."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    (home / "userdata" / "guisettings.xml").write_text(
        '<settings><setting id="lookandfeel.skin">skin.estuary</setting></settings>'
    )
    props = _record_window_props(wiz, monkeypatch)

    from resources.lib.modules import _kodisettings

    def _boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(_kodisettings, "write_guisetting", _boom)

    logs = []
    wiz._apply_boot_skin(lambda m: logs.append(m), "skin.estuary7")  # must not raise
    assert any("boot-skin" in m for m in logs)
    assert props.get("ezm_boot_skin", "").startswith("failed:")


def test_restore_captures_skin_before_apply_and_writes_it_back_last(
    wiz, monkeypatch, tmp_path
):
    """End-to-end: restore() captures the archive's skin BEFORE apply_guisettings can
    rewrite the file, then persists it as the LAST userdata write (after apply, purge,
    and the tvOS re-vector) - never a live switch."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    _record_window_props(wiz, monkeypatch)
    _no_skin_live_switch(monkeypatch, wiz)

    from resources.lib.modules import _kodisettings, nsud

    order = []
    # apply_guisettings simulates real Kodi stamping STOCK over the archive's skin on disk.
    gp = home / "userdata" / "guisettings.xml"

    def _apply(path):
        order.append("apply")
        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(str(gp))
            r = tree.getroot()
            for n in r.iter("setting"):
                if n.get("id") == "lookandfeel.skin":
                    n.text = "skin.estuary"  # the clobber the fix must survive
            tree.write(str(gp))
        except Exception:
            pass
        return 0

    monkeypatch.setattr(_kodisettings, "apply_guisettings", _apply)
    monkeypatch.setattr(
        nsud,
        "purge_stale_keys",
        lambda *a, **k: order.append("purge") or (0, 0, 0, 0),
    )
    monkeypatch.setattr(
        nsud,
        "rewrite_userdata_xml",
        lambda *a, **k: order.append("rewrite") or (0, 0, 0),
    )
    wg = []
    real_wg = _kodisettings.write_guisetting

    def _wg(path, sid, val):
        order.append("write_guisetting")
        wg.append((sid, val))
        return real_wg(path, sid, val)

    monkeypatch.setattr(_kodisettings, "write_guisetting", _wg)
    monkeypatch.setattr(
        nsud,
        "persist_one",
        lambda rel, log=None: order.append("persist_one") or True,
    )

    src = tmp_path / "kodi_settings_skin.zip"
    _make_valid_zip(
        src,
        [
            (
                "guisettings.xml",
                '<settings><setting id="lookandfeel.skin">'
                "skin.estuary7</setting></settings>",
            )
        ],
    )
    wiz.restore(str(src), confirm=False)

    # The restored skin was captured (skin.estuary7) despite apply stamping stock, and
    # written back via write_guisetting.
    assert ("lookandfeel.skin", "skin.estuary7") in wg
    # It is the LAST userdata write: write_guisetting + persist_one come AFTER apply,
    # purge, and the re-vector.
    assert order.index("write_guisetting") > order.index("apply")
    assert order.index("write_guisetting") > order.index("rewrite")
    assert order.index("persist_one") > order.index("rewrite")
    # And the file on disk ends on the restored skin, not the stock clobber.
    import xml.etree.ElementTree as ET

    r = ET.parse(str(gp)).getroot()
    got = next(n.text for n in r.iter("setting") if n.get("id") == "lookandfeel.skin")
    assert got == "skin.estuary7"


def test_no_live_skin_switch_mechanism_remains_in_sources(wiz):
    """The flaky live-switch-and-confirm is fully removed from wiz.py: no SendClick, no
    keep-skin dialog handling, and no live SetSettingValue for lookandfeel.skin."""
    src = (ADDON_ROOT / "resources" / "lib" / "modules" / "wiz.py").read_text()
    # The live-switch mechanism's actual code constructs must all be gone (the docstring
    # may still name SendClick to explain WHY - so match the call form, not the bare word).
    assert "SendClick(11)" not in src, "the keep-skin SendClick confirm must be gone"
    assert "IsActive(yesnodialog)" not in src, "the keep-skin dialog probe must be gone"
    assert "Action(Select)" not in src, "the keep-skin nav confirm must be gone"
    # No live skin switch: lookandfeel.skin is never handed to Settings.SetSettingValue.
    assert '"setting": "lookandfeel.skin"' not in src


def test_no_mid_flight_wipe_warning_dialog_remains(wiz):
    """The raw wipe warning is gone from the sources: leftovers are triaged by
    the verification, never surfaced as a fear."""
    ADDON = ADDON_ROOT / "resources" / "lib" / "modules"
    combined = (ADDON / "onetap.py").read_text() + (ADDON / "wiz.py").read_text()
    # The two retired user-facing strings, by their distinctive tails (comments
    # may still DESCRIBE the old behavior; the dialogs may not SHOW it).
    assert "The restore will proceed." not in combined
    assert "shadow or pollute the restored state" not in combined


def test_merge_cancel_after_completed_pass_still_arms(wiz, monkeypatch, tmp_path):
    """Finding 4: merge restore, pass 1 completes with a fixable finding, the silent
    auto-fix retry's extract is canceled. The box WAS restored by pass 1, so the
    tune-up + restore-check markers and the boot skin must still arm - and the pass's
    own 'Restore Canceled' dialog is the only message (never a stray Complete)."""
    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)

    # Extract: pass 1 lands clean, pass 2 (the auto-fix retry) is canceled.
    calls = {"n": 0}

    def _extract(_in, _out, progress, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return wiz.ExtractResult(canceled=False, extracted=1, total=1)
        return wiz.ExtractResult(canceled=True, extracted=-1)

    monkeypatch.setattr(wiz, "ExtractWithProgress", _extract)

    # Force a fixable (attention-only) finding on pass 1 so a silent retry runs.
    from resources.lib.modules import restorecheck

    vcalls = {"n": 0}

    def _verify(*a, **k):
        vcalls["n"] += 1
        return (
            (["1 stale key still shadows something"], [])
            if vcalls["n"] == 1
            else ([], [])
        )

    monkeypatch.setattr(restorecheck, "verify_restored_state", _verify)

    armed = []
    monkeypatch.setattr(
        wiz.tools, "mark_buffer_prompt_pending", lambda: armed.append("buffer")
    )
    monkeypatch.setattr(
        wiz.tools, "mark_restore_check_pending", lambda: armed.append("check")
    )
    skinned = []
    monkeypatch.setattr(
        wiz, "_apply_boot_skin", lambda rlog, target=None: skinned.append(True)
    )

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>")])
    result = wiz.restore(str(src), confirm=False)  # merge (no wipe/post_wipe)

    assert calls["n"] == 2, "the auto-fix retry ran"
    assert armed == ["buffer", "check"], (
        "a restored box arms its markers even on a canceled retry"
    )
    assert skinned == [True], "the boot skin still applies"
    assert not any(s == wiz.MSG_COMPLETE for s in statuses), (
        "a canceled retry never claims Complete"
    )
    assert result.get("canceled") is True
    # The pass's own 'Restore Canceled' dialog fired inside the pass; no extra Problem dialog.
    assert not any(wiz.MSG_NEEDS_ATTENTION in o for o in oks)


def test_failed_member_is_named_in_the_log(wiz, monkeypatch, tmp_path):
    """The 'named, in the log' half of the honesty contract: a member that fails to
    extract must be logged by name even though the UI shows only the locked Problem."""
    home = _prep_restore(wiz, monkeypatch, tmp_path)
    _record_restore_report(wiz, monkeypatch, retry=False)
    logs = []
    monkeypatch.setattr(wiz.xbmc, "log", lambda msg, level=0: logs.append(msg))
    (home / "userdata" / "blocked.xml").mkdir(parents=True)  # extract target is a dir

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>"), ("blocked.xml", "<b/>")])
    wiz.restore(str(src), confirm=False)

    assert any("failed member" in m and "blocked.xml" in m for m in logs), (
        "the failed member must be named in the log"
    )


def test_revector_miss_on_merge_path_is_attention_not_silent(wiz, monkeypatch, tmp_path):
    """audit Finding A/B: on the MERGE path (add-on-top / Dropbox) a tvOS re-vector
    miss can leave a SURVIVING stale key shadowing the restored file - a silent loss.
    It MUST surface as needs-attention, never a silent 'Restore Complete'."""
    from resources.lib.modules import nsud

    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)
    monkeypatch.setattr(nsud, "purge_stale_keys", lambda root, log=None: (0, 0, 0, 0))
    monkeypatch.setattr(
        nsud, "rewrite_userdata_xml", lambda root, log=None: (0, 0, 2, 0)
    )  # 2 files did not re-vector

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>"), ("sources.xml", "<s/>")])
    wiz.restore(str(src), confirm=False)  # MERGE: wipe=False

    assert oks == [wiz.AddonTitle + " " + wiz.MSG_NEEDS_ATTENTION], oks
    assert not any(s == wiz.MSG_COMPLETE for s in statuses)


def test_revector_miss_on_wipe_path_is_harmless_complete(wiz, monkeypatch, tmp_path):
    """On the WIPE path the wipe already cleared every NSUD key, so a re-vector miss
    leaves the restored POSIX file served with NO shadow - harmless. It must NOT
    alarm; the restore says Complete (this is exactly the atv2 clean-restore case)."""
    from resources.lib.modules import nsud

    _prep_restore(wiz, monkeypatch, tmp_path)
    statuses, oks, yesnos = _record_restore_report(wiz, monkeypatch)
    monkeypatch.setattr(nsud, "purge_stale_keys", lambda root, log=None: (0, 0, 0, 0))
    monkeypatch.setattr(
        nsud, "rewrite_userdata_xml", lambda root, log=None: (0, 0, 2, 0)
    )

    src = tmp_path / "kodi_settings_x.zip"
    _make_valid_zip(src, [("guisettings.xml", "<s/>"), ("sources.xml", "<s/>")])
    wiz.restore(str(src), confirm=False, post_wipe=True)  # WIPE path

    assert statuses == [wiz.MSG_COMPLETE], statuses
    assert oks == [] and yesnos == []
