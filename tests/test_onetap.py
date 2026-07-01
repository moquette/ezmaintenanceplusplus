"""
Unit tests for One-Tap Restore stage 1: the pin data model, the source picker, and the
READ-ONLY verify check. Self-contained fakes (onetap imports only xbmc*/xbmcvfs + a lazy
dropbox_remote), so none of the destructive apply path is exercised here.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"


class FakeAddon:
    _settings = {}

    def __init__(self, id=None):
        pass

    def getSetting(self, key):
        return FakeAddon._settings.get(key, "")

    def setSetting(self, key, value):
        FakeAddon._settings[key] = value

    def getAddonInfo(self, key):
        return ""


class FakeDialog:
    select_returns = []
    browse_returns = []
    input_returns = []
    ok_calls = []
    notifications = []

    def select(self, heading, opts, *a, **k):
        return FakeDialog.select_returns.pop(0) if FakeDialog.select_returns else -1

    def browse(self, *a, **k):
        return FakeDialog.browse_returns.pop(0) if FakeDialog.browse_returns else ""

    def input(self, *a, **k):
        return FakeDialog.input_returns.pop(0) if FakeDialog.input_returns else ""

    def ok(self, heading, msg, *a, **k):
        FakeDialog.ok_calls.append((heading, msg))
        return True

    def notification(self, heading, msg, *a, **k):
        FakeDialog.notifications.append((heading, msg))

    def yesno(self, *a, **k):
        return True


class FakeDialogProgress:
    def create(self, *a, **k):
        pass

    def update(self, *a, **k):
        pass

    def close(self, *a, **k):
        pass

    def iscanceled(self):
        return False


class FakeStat:
    def __init__(self, size):
        self._s = size

    def st_size(self):
        return self._s


class FakeFile:
    payloads = {}  # path -> bytes

    def __init__(self, path, mode="r"):
        self.path = path
        self._d = FakeFile.payloads.get(path, b"")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def readBytes(self, n=-1):
        return bytearray(self._d if n < 0 else self._d[:n])


def _make_xbmcvfs():
    m = types.ModuleType("xbmcvfs")
    m._exists = {}
    m._sizes = {}
    m.exists = lambda p: m._exists.get(p, False)
    m.Stat = lambda p: FakeStat(m._sizes.get(p, 0))
    m.File = FakeFile
    m.translatePath = lambda p: p
    return m


@pytest.fixture
def ot(monkeypatch):
    FakeAddon._settings = {}
    FakeDialog.select_returns = []
    FakeDialog.browse_returns = []
    FakeDialog.input_returns = []
    FakeDialog.ok_calls = []
    FakeDialog.notifications = []
    FakeFile.payloads = {}

    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 1
    xbmc.LOGERROR = 3
    xbmc.log = lambda *a, **k: None

    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = FakeAddon

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.Dialog = FakeDialog
    xbmcgui.DialogProgress = FakeDialogProgress

    xbmcvfs = _make_xbmcvfs()

    dbx = types.ModuleType("dropbox_remote")
    dbx._list = []
    dbx._raise = False

    def list_backups():
        if dbx._raise:
            raise RuntimeError("boom")
        return list(dbx._list)

    dbx.list_backups = list_backups

    for name, mod in {
        "xbmc": xbmc,
        "xbmcaddon": xbmcaddon,
        "xbmcgui": xbmcgui,
        "xbmcvfs": xbmcvfs,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    for pkg in ("resources", "resources.lib", "resources.lib.modules"):
        m = types.ModuleType(pkg)
        m.__path__ = []
        monkeypatch.setitem(sys.modules, pkg, m)
    sys.modules["resources.lib.modules"].dropbox_remote = dbx
    monkeypatch.setitem(sys.modules, "resources.lib.modules.dropbox_remote", dbx)

    sys.modules.pop("onetap", None)
    spec = importlib.util.spec_from_file_location(
        "onetap", ADDON_ROOT / "resources" / "lib" / "modules" / "onetap.py"
    )
    onetap = importlib.util.module_from_spec(spec)
    sys.modules["onetap"] = onetap
    spec.loader.exec_module(onetap)

    return types.SimpleNamespace(
        onetap=onetap,
        settings=FakeAddon._settings,
        dialog=FakeDialog,
        vfs=xbmcvfs,
        file=FakeFile,
        dbx=dbx,
    )


# --------------------------- data model ----------------------------------- #
def test_model_roundtrip(ot):
    o = ot.onetap
    assert not o.is_set(o.get_pin(1))
    o.save_pin(1, "Golden", "vfs", "nfs://h/s/b.zip", "full", "full . 130 MB")
    p = o.get_pin(1)
    assert o.is_set(p)
    assert (p["name"], p["kind"], p["src"], p["type"], p["meta"]) == (
        "Golden",
        "vfs",
        "nfs://h/s/b.zip",
        "full",
        "full . 130 MB",
    )
    o.clear_pin(1)
    assert not o.is_set(o.get_pin(1))


def test_all_pins_count(ot):
    assert len(ot.onetap.all_pins()) == ot.onetap.SLOTS


def test_infer_type(ot):
    o = ot.onetap
    assert o.infer_type("kodi_userdata_2026.zip") == "userdata"
    assert o.infer_type("full-backup_2026.zip") == "full"
    assert o.infer_type("kodi_backup_2026.zip") == "full"
    assert o.infer_type("random.zip") == "unknown"


def test_fmt_size(ot):
    o = ot.onetap
    assert o.fmt_size(0) == "0 B"
    assert o.fmt_size(1024) == "1 KB"
    assert o.fmt_size(130 * 1024 * 1024).endswith("MB")
    assert o.fmt_size("nope") == "?"


def test_label_for_empty_and_set(ot):
    o = ot.onetap
    assert "empty" in o.label_for(o.get_pin(1)).lower()
    o.save_pin(1, "Golden", "dropbox", "full_2026.zip", "full", "full . Dropbox")
    lbl = o.label_for(o.get_pin(1))
    assert "Golden" in lbl and "Dropbox" in lbl


# --------------------------- verify (read-only) --------------------------- #
def test_verify_empty_slot(ot):
    ok, msg = ot.onetap.verify_pin(ot.onetap.get_pin(2))
    assert ok is False and "empty" in msg.lower()


def test_verify_vfs_missing(ot):
    o = ot.onetap
    o.save_pin(1, "x", "vfs", "nfs://h/gone.zip", "full", "")
    ot.vfs._exists["nfs://h/gone.zip"] = False
    ok, msg = o.verify_pin(o.get_pin(1))
    assert ok is False and "not found" in msg.lower()


def test_verify_vfs_empty_file(ot):
    o = ot.onetap
    path = "nfs://h/b.zip"
    o.save_pin(1, "x", "vfs", path, "full", "")
    ot.vfs._exists[path] = True
    ot.vfs._sizes[path] = 0
    ok, msg = o.verify_pin(o.get_pin(1))
    assert ok is False and "empty" in msg.lower()


def test_verify_vfs_not_zip(ot):
    o = ot.onetap
    path = "nfs://h/b.zip"
    o.save_pin(1, "x", "vfs", path, "full", "")
    ot.vfs._exists[path] = True
    ot.vfs._sizes[path] = 100
    ot.file.payloads[path] = b"NOTZIP.."
    ok, msg = o.verify_pin(o.get_pin(1))
    assert ok is False and "not a zip" in msg.lower()


def test_verify_vfs_valid(ot):
    o = ot.onetap
    path = "nfs://h/b.zip"
    o.save_pin(1, "x", "vfs", path, "full", "")
    ot.vfs._exists[path] = True
    ot.vfs._sizes[path] = 130 * 1024 * 1024
    ot.file.payloads[path] = b"PK\x03\x04rest"
    ok, msg = o.verify_pin(o.get_pin(1))
    assert ok is True and "valid" in msg.lower()


def test_verify_dropbox_present(ot):
    o = ot.onetap
    ot.settings["dropbox_refresh_token"] = "tok"
    o.save_pin(1, "x", "dropbox", "full_2026.zip", "full", "")
    ot.dbx._list = ["full_2026.zip", "other.zip"]
    ok, _ = o.verify_pin(o.get_pin(1))
    assert ok is True


def test_verify_dropbox_missing(ot):
    o = ot.onetap
    ot.settings["dropbox_refresh_token"] = "tok"
    o.save_pin(1, "x", "dropbox", "gone.zip", "full", "")
    ot.dbx._list = ["other.zip"]
    ok, msg = o.verify_pin(o.get_pin(1))
    assert ok is False and "not in dropbox" in msg.lower()


def test_verify_dropbox_signed_out(ot):
    o = ot.onetap
    o.save_pin(1, "x", "dropbox", "x.zip", "full", "")
    ot.settings["dropbox_refresh_token"] = ""
    ok, msg = o.verify_pin(o.get_pin(1))
    assert ok is False and "sign in" in msg.lower()


# --------------------------- pick (configuration) ------------------------- #
def test_pick_vfs_saves(ot):
    o = ot.onetap
    ot.dialog.select_returns = [0]  # choose VFS
    ot.dialog.browse_returns = ["smb://h/share/full-backup_2026.zip"]
    ot.vfs._sizes["smb://h/share/full-backup_2026.zip"] = 5 * 1024 * 1024
    o.pick(3)
    p = o.get_pin(3)
    assert o.is_set(p) and p["kind"] == "vfs"
    assert p["src"].endswith("full-backup_2026.zip") and p["type"] == "full"


def test_pick_vfs_rejects_non_zip(ot):
    o = ot.onetap
    ot.dialog.select_returns = [0]
    ot.dialog.browse_returns = ["smb://h/share/notes.txt"]
    o.pick(3)
    assert not o.is_set(o.get_pin(3))


def test_pick_dropbox_saves(ot):
    o = ot.onetap
    ot.settings["dropbox_refresh_token"] = "tok"
    ot.dialog.select_returns = [1, 0]  # choose Dropbox, then first file
    ot.dbx._list = ["kodi_userdata_2026.zip", "full_2026.zip"]
    o.pick(4)
    p = o.get_pin(4)
    assert o.is_set(p) and p["kind"] == "dropbox"
    assert p["src"] == "kodi_userdata_2026.zip" and p["type"] == "userdata"


def test_verify_silent_returns_bool_no_dialog(ot):
    # verify is read-only and safe to call on an empty slot
    o = ot.onetap
    assert o.verify(5, silent=True) is False
    assert ot.dialog.ok_calls == []


# --------------------------- apply: the safety invariant ------------------- #
def test_wipe_protects_addon_deps_and_temp(ot, tmp_path):
    o = ot.onetap
    (tmp_path / "addons" / "script.ezmaintenanceplusplus").mkdir(parents=True)
    (tmp_path / "addons" / "script.ezmaintenanceplusplus" / "default.py").write_text(
        "x"
    )
    (tmp_path / "addons" / "script.module.requests").mkdir(parents=True)
    (tmp_path / "addons" / "script.module.requests" / "__init__.py").write_text("x")
    (tmp_path / "addons" / "plugin.video.other").mkdir(parents=True)
    (tmp_path / "addons" / "plugin.video.other" / "x.py").write_text("x")
    (tmp_path / "temp").mkdir()
    (tmp_path / "temp" / "staged.zip").write_text("PKstaged")
    (tmp_path / "userdata").mkdir()
    (tmp_path / "userdata" / "guisettings.xml").write_text("x")

    o._wipe(
        str(tmp_path),
        {"script.ezmaintenanceplusplus", "temp", "script.module.requests"},
    )

    # preserved: the add-on, its dep, and the staged snapshot
    assert (
        tmp_path / "addons" / "script.ezmaintenanceplusplus" / "default.py"
    ).exists()
    assert (tmp_path / "addons" / "script.module.requests" / "__init__.py").exists()
    assert (tmp_path / "temp" / "staged.zip").exists()
    # wiped: everything else
    assert not (tmp_path / "addons" / "plugin.video.other" / "x.py").exists()
    assert not (tmp_path / "userdata" / "guisettings.xml").exists()


def test_wipe_keeps_addon_db_file(ot, tmp_path):
    o = ot.onetap
    (tmp_path / "userdata" / "Database").mkdir(parents=True)
    db = tmp_path / "userdata" / "Database" / "Addons33.db"
    db.write_text("state")
    (tmp_path / "userdata" / "guisettings.xml").write_text("x")
    o._wipe(str(tmp_path), {"temp"}, {str(db)})
    assert db.exists()  # the add-on state DB is preserved (stays enabled)
    assert not (
        tmp_path / "userdata" / "guisettings.xml"
    ).exists()  # everything else wiped


def test_apply_empty_slot_never_wipes(ot):
    o = ot.onetap
    wipes = []
    o._wipe = lambda *a, **k: wipes.append(1)
    o.apply(2)  # empty slot -> verify fails -> abort before wipe
    assert wipes == []


def test_apply_bad_zip_never_wipes(ot, tmp_path):
    o = ot.onetap
    path = "nfs://h/b.zip"
    o.save_pin(1, "x", "vfs", path, "full", "")
    ot.vfs._exists[path] = True
    ot.vfs._sizes[path] = 100
    ot.file.payloads[path] = b"PK\x03\x04"  # passes the read-only verify (PK header)
    bad = tmp_path / "staged.zip"
    bad.write_text("NOT A ZIP")  # but the fetched file is not a real zip
    o._stage = lambda pin: str(bad)
    wipes = []
    o._wipe = lambda *a, **k: wipes.append(1)
    o.apply(1)  # is_zipfile(bad) is False -> abort before wipe
    assert wipes == []


# --------------------------- the menu (user-facing entry) ----------------- #
def test_menu_taps_set_pin_opens_actions(ot):
    o = ot.onetap
    o.save_pin(1, "Golden", "vfs", "nfs://h/b.zip", "full", "full . 130 MB")
    ot.dialog.select_returns = [0]  # tap row 0 = slot 1 (set)
    calls = []
    o._pin_actions = lambda slot: calls.append(slot)
    o.menu()
    assert calls == [1]


def test_pin_actions_restore_dispatch(ot):
    o = ot.onetap
    o.save_pin(1, "x", "vfs", "nfs://h/b.zip", "full", "")
    ot.dialog.select_returns = [0]  # "Restore now"
    calls = []
    o.apply = lambda slot: calls.append(slot)
    o._pin_actions(1)
    assert calls == [1]


def test_rename_sets_name(ot):
    o = ot.onetap
    o.save_pin(1, "full-backup.zip  (130 MB)", "vfs", "nfs://h/b.zip", "full", "")
    ot.dialog.input_returns = ["Golden Snapshot"]
    o.rename(1)
    assert o.get_pin(1)["name"] == "Golden Snapshot"


def test_rename_cancel_keeps_name(ot):
    o = ot.onetap
    o.save_pin(1, "orig", "vfs", "nfs://h/b.zip", "full", "")
    ot.dialog.input_returns = [""]  # user cancelled
    o.rename(1)
    assert o.get_pin(1)["name"] == "orig"


def test_pin_actions_rename_dispatch(ot):
    o = ot.onetap
    o.save_pin(1, "x", "vfs", "nfs://h/b.zip", "full", "")
    ot.dialog.select_returns = [1]  # "Rename"
    ot.dialog.input_returns = ["New Name"]
    o._pin_actions(1)
    assert o.get_pin(1)["name"] == "New Name"


def test_remove_clears_pin(ot):
    o = ot.onetap
    o.save_pin(1, "x", "vfs", "nfs://h/b.zip", "full", "")
    o.remove(1)  # FakeDialog.yesno -> True
    assert not o.is_set(o.get_pin(1))


def test_menu_empty_slot_calls_pick(ot):
    o = ot.onetap
    ot.dialog.select_returns = [0]  # slot 1 empty -> pin it
    calls = []
    o.pick = lambda slot: calls.append(slot)
    o.menu()
    assert calls == [1]


def test_menu_plus_option_calls_menu_pick(ot):
    o = ot.onetap
    ot.dialog.select_returns = [o.SLOTS]  # the "[+] Pin..." row (index == slot count)
    calls = []
    o.menu_pick = lambda: calls.append(1)
    o.menu()
    assert calls == [1]
