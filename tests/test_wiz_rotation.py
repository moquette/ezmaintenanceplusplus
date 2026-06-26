"""
Adversarial tests for wiz.py destination routing + keep-N rotation.

The safety invariant under test: rotation deletes the OLDEST only, never a newer
backup, and (by construction in _backup_dropbox) only AFTER a confirmed upload.
We import wiz.py with stubbed control/maintenance/tools so we exercise the real
_rotate_vfs / _rotate_dropbox / _backup_dropbox logic.
"""

import importlib
import os
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"


# ---- fake kodi (minimal, enough for wiz import + rotation) -------------------
def _install_fake_kodi(monkeypatch):
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGERROR = 3
    LOGS = []
    xbmc.log = lambda msg, level=1: LOGS.append((level, msg))
    xbmc.translatePath = lambda p: p
    xbmc.executebuiltin = lambda *a, **k: None
    xbmc.getInfoLabel = lambda *a, **k: "20.0"
    xbmc.getSkinDir = lambda: "skin.estuary"
    xbmc.sleep = lambda *a, **k: None
    xbmc._logs = LOGS

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _Addon:
        _settings = {}

        def __init__(self, id=None):
            pass

        def getSetting(self, k):
            return _Addon._settings.get(k, "")

        def setSetting(self, k, v):
            _Addon._settings[k] = v

        def getAddonInfo(self, k):
            return {
                "name": "EZ Maintenance++",
                "id": "script.ezmaintenanceplusplus",
            }.get(k, "")

        def openSettings(self):
            return None

    xbmcaddon.Addon = _Addon

    xbmcgui = types.ModuleType("xbmcgui")

    class _Dialog:
        def ok(self, *a, **k):
            return True

        def yesno(self, *a, **k):
            return True

        def select(self, *a, **k):
            return -1

        def notification(self, *a, **k):
            return None

        def input(self, *a, **k):
            return ""

    class _DP:
        def create(self, *a, **k):
            return None

        def update(self, *a, **k):
            return None

        def close(self, *a, **k):
            return None

        def iscanceled(self):
            return False

    xbmcgui.Dialog = _Dialog
    xbmcgui.DialogProgress = _DP
    xbmcgui.DialogProgressBG = _DP
    xbmcgui.WindowDialog = lambda *a, **k: None
    xbmcgui.Window = lambda *a, **k: types.SimpleNamespace(getFocusId=lambda: "0")
    xbmcgui.ListItem = lambda *a, **k: None
    xbmcgui.ControlButton = lambda *a, **k: None
    xbmcgui.ControlImage = lambda *a, **k: None
    xbmcgui.getCurrentWindowId = lambda: 0

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs._deleted = []
    xbmcvfs.delete = lambda p: xbmcvfs._deleted.append(p)
    xbmcvfs._listdir_result = ([], [])
    xbmcvfs.listdir = lambda p: xbmcvfs._listdir_result
    xbmcvfs.File = lambda *a, **k: None
    xbmcvfs.copy = lambda a, b: True
    xbmcvfs.mkdir = lambda p: True
    xbmcvfs.rmdir = lambda p: True

    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.addDirectoryItem = lambda *a, **k: True
    xbmcplugin.endOfDirectory = lambda *a, **k: None
    xbmcplugin.setContent = lambda *a, **k: None
    xbmcplugin.setProperty = lambda *a, **k: None
    xbmcplugin.setResolvedUrl = lambda *a, **k: None

    for n, m in dict(
        xbmc=xbmc,
        xbmcaddon=xbmcaddon,
        xbmcgui=xbmcgui,
        xbmcvfs=xbmcvfs,
        xbmcplugin=xbmcplugin,
    ).items():
        monkeypatch.setitem(sys.modules, n, m)
    return types.SimpleNamespace(xbmc=xbmc, xbmcvfs=xbmcvfs, addon=_Addon)


def _install_stub_modules(monkeypatch, settings):
    """Stub resources.lib.modules.{control,maintenance,tools,backtothefuture}."""
    for pkg in ("resources", "resources.lib", "resources.lib.modules"):
        m = types.ModuleType(pkg)
        m.__path__ = []
        monkeypatch.setitem(sys.modules, pkg, m)

    b = types.ModuleType("resources.lib.modules.backtothefuture")
    b.PY2 = False
    b.unicode = str
    monkeypatch.setitem(sys.modules, "resources.lib.modules.backtothefuture", b)

    control = types.ModuleType("resources.lib.modules.control")
    control.HOME = "/home"
    control.USERDATA = "/home/userdata"
    control.setting = lambda k: settings.get(k, "")
    control.infoDialog = lambda *a, **k: None
    control.openSettings = lambda *a, **k: None
    control.selectDialog = lambda lst, **k: -1
    monkeypatch.setitem(sys.modules, "resources.lib.modules.control", control)

    maintenance = types.ModuleType("resources.lib.modules.maintenance")
    maintenance.clearCache = lambda *a, **k: None
    maintenance.deleteThumbnails = lambda *a, **k: None
    maintenance.purgePackages = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "resources.lib.modules.maintenance", maintenance)

    tools = types.ModuleType("resources.lib.modules.tools")
    tools._get_keyboard = lambda default="", heading="", cancel="": default
    monkeypatch.setitem(sys.modules, "resources.lib.modules.tools", tools)

    return control


def _import_wiz(monkeypatch, settings):
    _install_fake_kodi(monkeypatch)
    control = _install_stub_modules(monkeypatch, settings)
    sys.modules.pop("wiz", None)
    spec = importlib.util.spec_from_file_location(
        "wiz", ADDON_ROOT / "resources" / "lib" / "modules" / "wiz.py"
    )
    wiz = importlib.util.module_from_spec(spec)
    sys.modules["wiz"] = wiz
    spec.loader.exec_module(wiz)
    return wiz, control


# ============================================================ keep-N parse ===
def test_keep_n_default_and_parse(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "5"})
    assert wiz._keep_n() == 5
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": ""})
    assert wiz._keep_n() == 0
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "garbage"})
    assert wiz._keep_n() == 0


# ============================================================ vfs rotation ===
def test_rotate_vfs_deletes_oldest_only(monkeypatch):
    settings = {"backup.keep": "2"}
    wiz, _ = _import_wiz(monkeypatch, settings)
    # 4 zips, name-sorted ascending == oldest first (date stamp in name)
    files = [
        "kodi_backup_202601010101.zip",
        "kodi_backup_202601020101.zip",
        "kodi_backup_202601030101.zip",
        "kodi_backup_202601040101.zip",
    ]
    wiz.xbmcvfs._listdir_result = ([], list(files))
    wiz.xbmcvfs._deleted = []
    wiz._rotate_vfs("/backups")
    # keep 2 newest -> delete the 2 oldest
    deleted = wiz.xbmcvfs._deleted
    assert any("202601010101" in d for d in deleted)
    assert any("202601020101" in d for d in deleted)
    # newest two NEVER deleted
    assert not any("202601030101" in d for d in deleted)
    assert not any("202601040101" in d for d in deleted)
    assert len(deleted) == 2


def test_rotate_vfs_keep_zero_keeps_all(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "0"})
    wiz.xbmcvfs._listdir_result = ([], ["a_1.zip", "b_2.zip"])
    wiz.xbmcvfs._deleted = []
    wiz._rotate_vfs("/backups")
    assert wiz.xbmcvfs._deleted == []  # 0 == keep all


def test_rotate_vfs_fewer_than_keep_deletes_nothing(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "5"})
    wiz.xbmcvfs._listdir_result = ([], ["a_1.zip", "b_2.zip"])
    wiz.xbmcvfs._deleted = []
    wiz._rotate_vfs("/backups")
    assert wiz.xbmcvfs._deleted == []


def test_rotate_vfs_ignores_non_zip(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "1"})
    # SAME name prefix so the date stamp drives the sort (the only case the code
    # gets right). readme.txt must be ignored.
    wiz.xbmcvfs._listdir_result = (
        [],
        ["readme.txt", "kodi_backup_202601010101.zip", "kodi_backup_202601020101.zip"],
    )
    wiz.xbmcvfs._deleted = []
    wiz._rotate_vfs("/backups")
    # keep 1 -> delete oldest zip only; txt untouched
    assert len(wiz.xbmcvfs._deleted) == 1
    assert any("202601010101" in d for d in wiz.xbmcvfs._deleted)
    assert not any("readme" in d for d in wiz.xbmcvfs._deleted)


# ============================================================ FIX: timestamp-sort rotation ===
def test_rotate_vfs_keeps_newest_when_names_differ(monkeypatch):
    """FIX (HIGH-2): rotation must sort by the in-name _YYYYMMDDHHMM stamp, NOT
    the full filename. With different name prefixes (the 'Name your Backup'
    keyboard invites this) the NEWEST by date must survive and the older ones be
    pruned - never the reverse."""
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "1"})
    files = [
        "My_Build_202601010101.zip",  # OLDEST by date
        "zzz_202601020101.zip",  # middle by date
        "kodi_backup_202601030101.zip",  # NEWEST by date
    ]
    wiz.xbmcvfs._listdir_result = ([], list(files))
    wiz.xbmcvfs._deleted = []
    wiz._rotate_vfs("/backups")
    deleted = wiz.xbmcvfs._deleted
    # keep=1 -> the truly-newest (2026-01-03) is KEPT; the two older are deleted.
    newest = "202601030101"
    assert not any(newest in d for d in deleted), (
        "rotation deleted the NEWEST backup - timestamp sort is broken"
    )
    assert any("202601010101" in d for d in deleted)  # oldest pruned
    assert any("202601020101" in d for d in deleted)  # middle pruned
    assert len(deleted) == 2


def test_rotate_vfs_unstamped_pruned_before_stamped(monkeypatch):
    """A backup whose name has no parseable stamp must be treated as OLDEST and
    pruned before any stamped, newer backup."""
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "1"})
    files = [
        "legacy_no_stamp.zip",  # unstamped -> oldest
        "kodi_backup_202601020101.zip",  # newest
    ]
    wiz.xbmcvfs._listdir_result = ([], list(files))
    wiz.xbmcvfs._deleted = []
    wiz._rotate_vfs("/backups")
    deleted = wiz.xbmcvfs._deleted
    assert any("legacy_no_stamp" in d for d in deleted)  # unstamped pruned
    assert not any("202601020101" in d for d in deleted)  # stamped newest kept
    assert len(deleted) == 1


# ============================================================ dropbox rotation ===
class _FakeDbx:
    def __init__(self, names):
        self._names = list(names)  # already newest-first (what list_backups returns)
        self.deleted = []

    def list_backups(self):
        return list(self._names)

    def delete(self, name):
        self.deleted.append(name)


def test_rotate_dropbox_deletes_oldest_only(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "2"})
    # list_backups returns NEWEST-FIRST
    fdbx = _FakeDbx(
        [
            "z_202601040101.zip",  # newest
            "z_202601030101.zip",
            "z_202601020101.zip",
            "z_202601010101.zip",  # oldest
        ]
    )
    wiz._rotate_dropbox(fdbx)
    # keep 2 newest -> delete index [2:] -> the 2 oldest
    assert fdbx.deleted == ["z_202601020101.zip", "z_202601010101.zip"]
    # never the 2 newest
    assert "z_202601040101.zip" not in fdbx.deleted
    assert "z_202601030101.zip" not in fdbx.deleted


def test_rotate_dropbox_keep_zero_keeps_all(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "0"})
    fdbx = _FakeDbx(["a.zip", "b.zip", "c.zip"])
    wiz._rotate_dropbox(fdbx)
    assert fdbx.deleted == []


def test_rotate_dropbox_fewer_than_keep(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "5"})
    fdbx = _FakeDbx(["a.zip", "b.zip"])
    wiz._rotate_dropbox(fdbx)
    assert fdbx.deleted == []


def test_rotate_dropbox_exactly_keep_deletes_nothing(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"backup.keep": "3"})
    fdbx = _FakeDbx(["a.zip", "b.zip", "c.zip"])
    wiz._rotate_dropbox(fdbx)
    assert fdbx.deleted == []


# ============================================================ routing ===
def test_destination_parsing(monkeypatch):
    wiz, _ = _import_wiz(monkeypatch, {"destination": "2"})
    assert wiz._destination() == 2
    wiz, _ = _import_wiz(monkeypatch, {"destination": ""})
    assert wiz._destination() == 0
    wiz, _ = _import_wiz(monkeypatch, {"destination": "bogus"})
    assert wiz._destination() == 0


# ============================================================ SAFETY: failure path ===
def test_dropbox_upload_failure_does_not_rotate(monkeypatch):
    """If upload raises, _backup_dropbox must NOT call rotation (prior backup sacred)."""
    settings = {
        "destination": "2",
        "backup.keep": "2",
        "dropbox_refresh_token": "RT",
    }
    wiz, _ = _import_wiz(monkeypatch, settings)

    rotate_called = {"n": 0}
    monkeypatch.setattr(
        wiz,
        "_rotate_dropbox",
        lambda dbx: rotate_called.__setitem__("n", rotate_called["n"] + 1),
    )

    # stub dropbox_remote module with a failing upload + a tracking delete
    dbx_mod = types.ModuleType("resources.lib.modules.dropbox_remote")
    dbx_mod.delete_calls = []

    def _upload(local, name):
        raise RuntimeError("network down")

    dbx_mod.upload = _upload
    dbx_mod.list_backups = lambda: ["a.zip", "b.zip", "c.zip"]
    dbx_mod.delete = lambda n: dbx_mod.delete_calls.append(n)
    monkeypatch.setitem(sys.modules, "resources.lib.modules.dropbox_remote", dbx_mod)

    # make CreateZip a no-op that returns "not canceled"
    monkeypatch.setattr(wiz, "CreateZip", lambda *a, **k: False)
    # os.remove of staged temp should be guarded

    monkeypatch.setattr(wiz.os, "remove", lambda p: None)

    wiz._backup_dropbox("full", "kodi_backup", "/home")
    # upload failed -> rotation NEVER called, nothing deleted
    assert rotate_called["n"] == 0
    assert dbx_mod.delete_calls == []


def test_dropbox_canceled_zip_does_not_upload_or_rotate(monkeypatch):
    settings = {"destination": "2", "backup.keep": "2", "dropbox_refresh_token": "RT"}
    wiz, _ = _import_wiz(monkeypatch, settings)
    rotate_called = {"n": 0}
    monkeypatch.setattr(
        wiz, "_rotate_dropbox", lambda dbx: rotate_called.__setitem__("n", 1)
    )
    dbx_mod = types.ModuleType("resources.lib.modules.dropbox_remote")
    upload_calls = {"n": 0}
    dbx_mod.upload = lambda l, n: upload_calls.__setitem__("n", 1)
    monkeypatch.setitem(sys.modules, "resources.lib.modules.dropbox_remote", dbx_mod)
    # CreateZip returns True (canceled)
    monkeypatch.setattr(wiz, "CreateZip", lambda *a, **k: True)
    monkeypatch.setattr(wiz.os, "remove", lambda p: None)
    wiz._backup_dropbox("full", "kodi_backup", "/home")
    assert upload_calls["n"] == 0  # never uploaded
    assert rotate_called["n"] == 0  # never rotated


def test_dropbox_success_rotates_and_cleans_temp(monkeypatch):
    settings = {"destination": "2", "backup.keep": "2", "dropbox_refresh_token": "RT"}
    wiz, _ = _import_wiz(monkeypatch, settings)
    rotate_called = {"n": 0}
    monkeypatch.setattr(
        wiz, "_rotate_dropbox", lambda dbx: rotate_called.__setitem__("n", 1)
    )
    dbx_mod = types.ModuleType("resources.lib.modules.dropbox_remote")
    dbx_mod.upload = lambda l, n: True
    monkeypatch.setitem(sys.modules, "resources.lib.modules.dropbox_remote", dbx_mod)
    monkeypatch.setattr(wiz, "CreateZip", lambda *a, **k: False)
    removed = []
    monkeypatch.setattr(wiz.os, "remove", lambda p: removed.append(p))
    wiz._backup_dropbox("full", "kodi_backup", "/home")
    assert rotate_called["n"] == 1  # rotated after confirmed upload
    assert len(removed) == 1  # temp cleaned in finally


def _wire_failing_vfs_copy(wiz, monkeypatch, tmp_path, copy_ret):
    """Stage a real source dir, route special://temp to a writable dir, and make
    xbmcvfs.copy report `copy_ret`. Returns (src_path, copy_calls)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    tmpdir = tmp_path / "temp"
    tmpdir.mkdir()
    monkeypatch.setattr(
        wiz,
        "translatePath",
        lambda p: str(tmpdir / os.path.basename(p)) if "special://temp" in p else p,
    )
    copy_calls = {"n": 0, "ret": copy_ret}
    monkeypatch.setattr(
        wiz.xbmcvfs,
        "copy",
        lambda a, b: (
            copy_calls.__setitem__("n", copy_calls["n"] + 1),
            copy_calls["ret"],
        )[1],
    )
    return src, copy_calls


def test_create_zip_raises_on_vfs_copy_failure(monkeypatch, tmp_path):
    """FIX (HIGH-1): CreateZip's remote branch captures xbmcvfs.copy()'s result;
    a False (share offline / no space / no permission) must raise VfsCopyError,
    NOT be swallowed and reported as a successful (canceled=False) backup."""
    wiz, _ = _import_wiz(monkeypatch, {})
    src, copy_calls = _wire_failing_vfs_copy(wiz, monkeypatch, tmp_path, copy_ret=False)
    target = "smb://server/share/backup_202601010101.zip"
    with pytest.raises(wiz.VfsCopyError):
        wiz.CreateZip(str(src), target, "h", "m", [""], [".log"])
    assert copy_calls["n"] == 1  # the copy was attempted and failed


def test_create_zip_ok_when_vfs_copy_succeeds(monkeypatch, tmp_path):
    """A successful VFS copy returns canceled=False (no raise)."""
    wiz, _ = _import_wiz(monkeypatch, {})
    src, copy_calls = _wire_failing_vfs_copy(wiz, monkeypatch, tmp_path, copy_ret=True)
    target = "smb://server/share/backup_202601010101.zip"
    canceled = wiz.CreateZip(str(src), target, "h", "m", [""], [".log"])
    assert canceled is False
    assert copy_calls["n"] == 1


def test_backup_vfs_copy_failure_no_success_no_rotation(monkeypatch, tmp_path):
    """End-to-end (HIGH-1): a failed VFS ship via backup() must NOT report
    success and must NOT trigger rotation - the prior good backup is untouched."""
    settings = {
        "destination": "1",  # Network
        "download.path": "smb://server/share/",
        "backup.keep": "1",
    }
    wiz, _ = _import_wiz(monkeypatch, settings)
    # CreateZip raises VfsCopyError (the post-fix behavior on a failed ship)
    monkeypatch.setattr(
        wiz,
        "CreateZip",
        lambda *a, **k: (_ for _ in ()).throw(wiz.VfsCopyError("copy failed")),
    )
    rotate_called = {"n": 0}
    monkeypatch.setattr(wiz, "_rotate_vfs", lambda d: rotate_called.__setitem__("n", 1))
    ok_calls = []
    monkeypatch.setattr(wiz.dialog, "ok", lambda *a, **k: ok_calls.append(a))
    # BACKUPDATA must exist so backup() proceeds; control.HOME == "/home"
    monkeypatch.setattr(wiz.os.path, "exists", lambda p: True)

    wiz.backup("full")

    assert rotate_called["n"] == 0, "rotation ran after a failed ship"
    blob = " ".join(str(x) for args in ok_calls for x in args)
    assert "complete" not in blob.lower(), "reported success after a failed ship"
    assert "fail" in blob.lower(), "did not surface the failure to the user"


def test_dropbox_no_refresh_token_aborts_early(monkeypatch):
    settings = {"destination": "2", "backup.keep": "2", "dropbox_refresh_token": ""}
    wiz, _ = _import_wiz(monkeypatch, settings)
    dbx_mod = types.ModuleType("resources.lib.modules.dropbox_remote")
    up = {"n": 0}
    dbx_mod.upload = lambda l, n: up.__setitem__("n", 1)
    monkeypatch.setitem(sys.modules, "resources.lib.modules.dropbox_remote", dbx_mod)
    czip = {"n": 0}
    monkeypatch.setattr(wiz, "CreateZip", lambda *a, **k: czip.__setitem__("n", 1))
    wiz._backup_dropbox("full", "kodi_backup", "/home")
    assert up["n"] == 0  # never uploaded
    assert czip["n"] == 0  # never even zipped
