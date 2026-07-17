# -*- coding: utf-8 -*-
"""service._maybe_restore_check: the first-boot-after-restore self-check.

Silent on a clean pass (log only), a notification on a real finding, the marker
consumed exactly once - and only when the check actually ran. These tests exist
because the first cut shipped a NameError (AddonTitle undefined) that the outer
except swallowed, silencing the notification path entirely; a real fake driving
the real service.py catches that class.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SERVICE_PY = HERE.parent / "script.ezmaintenanceplusplus" / "service.py"

LOGINFO, LOGWARNING = 1, 2


class _Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.logs = []  # (level, msg)
        self.notifications = []  # (heading, message)
        self.userdata = str(tmp_path / "userdata")
        self.dup_hits = []  # what restorecheck.duplicate_listing_hits returns
        self.ready = True  # _wait_kodi_ready result
        self.marker = (
            tmp_path
            / "userdata/addon_data/script.ezmaintenanceplusplus/.ezm_restore_check"
        )

    def arm(self):
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_text("1")

    def log_lines(self, level):
        return [m for lv, m in self.logs if lv == level]


def _load_service(monkeypatch, env):
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
    xbmc.LOGNOTICE = 1
    xbmc.log = lambda msg, level=0: env.logs.append((level, msg))
    xbmc.translatePath = lambda p: p.replace("special://home/", str(env.tmp) + "/")
    xbmc.getCondVisibility = lambda cond: True
    xbmc.executebuiltin = lambda *a, **k: None
    xbmc.executeJSONRPC = lambda *a, **k: "{}"
    xbmc.sleep = lambda ms: None
    xbmc.Player = lambda *a, **k: types.SimpleNamespace(isPlayingVideo=lambda: False)
    xbmc.Monitor = type(
        "Monitor",
        (),
        {"abortRequested": lambda self: False, "waitForAbort": lambda self, t: False},
    )

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _FakeAddon:
        def __init__(self, *a, **k):
            pass

        def getSetting(self, key):
            return ""

        def setSetting(self, key, value):
            pass

        def getAddonInfo(self, key):
            return {"id": "script.ezmaintenanceplusplus", "version": "0"}.get(key, "")

    xbmcaddon.Addon = _FakeAddon

    xbmcgui = types.ModuleType("xbmcgui")

    def _dialog(*a, **k):
        return types.SimpleNamespace(
            yesno=lambda *a, **k: 0,
            select=lambda *a, **k: -1,
            ok=lambda *a, **k: None,
            notification=lambda heading, message, *a, **k: env.notifications.append(
                (heading, message)
            ),
        )

    xbmcgui.Dialog = _dialog

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = xbmc.translatePath
    xbmcvfs.exists = lambda p: Path(p).exists()

    pkgs = {}
    for name in ("resources", "resources.lib", "resources.lib.modules"):
        m = types.ModuleType(name)
        m.__path__ = []
        pkgs[name] = m

    b2f = types.ModuleType("resources.lib.modules.backtothefuture")
    b2f.PY2 = False
    b2f.unicode = str

    maintenance = types.ModuleType("resources.lib.modules.maintenance")
    for fn in (
        "logMaintenance",
        "determineNextMaintenance",
        "getNextMaintenance",
        "clearCache",
        "purgePackages",
        "deleteThumbnails",
    ):
        setattr(maintenance, fn, lambda *a, **k: None)

    control = types.ModuleType("resources.lib.modules.control")
    control.USERDATA = env.userdata

    tools = types.ModuleType("resources.lib.modules.tools")
    tools.restore_check_pending = lambda: env.marker.exists()

    def _clear():
        try:
            env.marker.unlink()
        except OSError:
            pass

    tools.clear_restore_check_marker = _clear

    restorecheck = types.ModuleType("resources.lib.modules.restorecheck")
    restorecheck.duplicate_listing_hits = lambda: list(env.dup_hits)

    mods = dict(pkgs)
    mods.update(
        {
            "xbmc": xbmc,
            "xbmcaddon": xbmcaddon,
            "xbmcgui": xbmcgui,
            "xbmcvfs": xbmcvfs,
            "resources.lib.modules.backtothefuture": b2f,
            "resources.lib.modules.maintenance": maintenance,
            "resources.lib.modules.control": control,
            "resources.lib.modules.tools": tools,
            "resources.lib.modules.restorecheck": restorecheck,
        }
    )
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    pkgs["resources"].lib = pkgs["resources.lib"]
    pkgs["resources.lib"].modules = pkgs["resources.lib.modules"]
    for attr in ("backtothefuture", "maintenance", "control", "tools", "restorecheck"):
        setattr(
            pkgs["resources.lib.modules"], attr, mods["resources.lib.modules." + attr]
        )

    monkeypatch.delitem(sys.modules, "ezm_service_restore_check_uut", raising=False)
    spec = importlib.util.spec_from_file_location(
        "ezm_service_restore_check_uut", SERVICE_PY
    )
    mod = importlib.util.module_from_spec(spec)
    # Force _wait_kodi_ready to env.ready without racing a real GUI wait.
    spec.loader.exec_module(mod)
    mod._wait_kodi_ready = lambda monitor, *a, **k: env.ready
    return mod


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = _Env(tmp_path)
    e.load = lambda: _load_service(monkeypatch, e)
    return e


class _Mon:
    def abortRequested(self):
        return False

    def waitForAbort(self, t):
        return False


def test_no_marker_is_a_noop(env):
    svc = env.load()
    svc._maybe_restore_check(_Mon())
    assert env.notifications == []
    assert env.logs == []


def test_clean_check_is_silent_but_logs(env):
    env.arm()
    env.dup_hits = []
    svc = env.load()
    svc._maybe_restore_check(_Mon())
    assert env.notifications == [], "a clean self-check must not speak"
    assert any("verified clean" in m for m in env.log_lines(LOGINFO))
    assert not env.marker.exists(), "the one-shot marker is consumed"


def test_finding_raises_the_notification(env):
    """The regression guard for the AddonTitle NameError: a real finding MUST
    produce the notification, not be swallowed by the guard."""
    env.arm()
    env.dup_hits = [
        "special://profile/addon_data/pvr.iptvsimple/instance-settings-1.xml"
    ]
    svc = env.load()
    svc._maybe_restore_check(_Mon())
    assert len(env.notifications) == 1, "a finding must reach the notification"
    heading, message = env.notifications[0]
    assert "EZ Maintenance++" in heading
    assert "needs attention" in message
    # No count/path jargon in the user-facing string.
    assert "instance-settings" not in message
    assert any("ATTENTION" in m for m in env.log_lines(LOGWARNING))
    assert not env.marker.exists()


def test_marker_survives_when_gui_never_ready(env):
    """An aborted/interrupted boot must NOT consume the one-shot marker - the
    check is still owed on the next start (Finding 3)."""
    env.arm()
    env.ready = False
    svc = env.load()
    svc._maybe_restore_check(_Mon())
    assert env.notifications == []
    assert env.marker.exists(), "marker must survive a boot where the check never ran"


def test_probe_exception_does_not_break_boot_and_clears_marker(env, monkeypatch):
    env.arm()
    svc = env.load()
    rc = sys.modules["resources.lib.modules.restorecheck"]

    def _boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(rc, "duplicate_listing_hits", _boom)
    svc._maybe_restore_check(_Mon())  # must not raise
    assert not env.marker.exists()
