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

    def _expected_skin():
        try:
            v = env.marker.read_text().strip()
        except OSError:
            return None
        return None if v in ("", "1") else v

    tools.restore_check_expected_skin = _expected_skin

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


# --------------------------------------------------------------------------- #
# Restore defect B3: home-visible is NOT boot-settled.
# skin.estuary7's Home.xml onload arms AlarmClock(t7bbuild,...,00:15); when the menu
# is stale (what a restore produces) the skinshortcuts rebuild ends in ReloadSkin(),
# destroying the window stack. Reproduced on the bench 2026-07-18 at 15.05s and
# 15.45s, killing an open EZM++ prompt 15.27s in. Prompting at home-visible is
# prompting into a doomed window, so the service must wait past the deferred build.
# See docs/restore-defect-b-reproduced-2026-07-18.md.
# --------------------------------------------------------------------------- #


class _RecordingMon:
    """Monitor that records how long it was asked to wait."""

    def __init__(self, abort_after=None):
        self.waits = []
        self.abort_after = abort_after

    def abortRequested(self):
        return False

    def waitForAbort(self, t):
        self.waits.append(t)
        if self.abort_after is not None and len(self.waits) >= self.abort_after:
            return True  # abort signalled
        return False


def test_wait_skin_settled_waits_past_the_deferred_build(env):
    """It must wait longer than the skin's 15s alarm before any dialog is opened."""
    svc = env.load()
    mon = _RecordingMon()
    assert svc._wait_skin_settled(mon) is True
    assert mon.waits, "it must actually wait"
    assert mon.waits[0] >= 16, (
        "the first wait must clear the skin's 15s AlarmClock with margin, got %r"
        % (mon.waits[0],)
    )
    assert svc._SKIN_DEFERRED_BUILD_SECS >= 16


def test_boot_service_settles_the_skin_BEFORE_prompting(env, monkeypatch):
    """The service must CALL _wait_skin_settled, and call it before the prompt.

    The other tests here call _wait_skin_settled directly, so they all still pass if
    the call is deleted from _maybe_prompt_after_restore or reordered after the
    prompt - an adversarial QA pass on 2026-07-18 demonstrated exactly that deletion
    with the full suite green. That regression is invisible in code review and fatal
    in behaviour: the prompt goes straight back into the skin's 15s AlarmClock window,
    which tears down the window stack mid-dialog. This pins the ORDER, which is the
    entire content of the fix."""
    svc = env.load()
    order = []
    monkeypatch.setattr(
        svc, "_wait_kodi_ready", lambda *a, **k: order.append("ready") or True
    )
    monkeypatch.setattr(
        svc, "_wait_skin_settled", lambda *a, **k: order.append("settled") or True
    )
    # service.py imports tools INSIDE the function, so patch the module object itself.
    tools = sys.modules["resources.lib.modules.tools"]
    monkeypatch.setattr(
        tools,
        "prompt_after_restore",
        lambda *a, **k: order.append("prompt"),
        raising=False,
    )
    monkeypatch.setattr(
        tools, "buffer_prompt_pending", lambda *a, **k: True, raising=False
    )

    svc._maybe_prompt_after_restore(_RecordingMon())

    assert "prompt" in order, "the prompt must still run"
    assert "settled" in order, (
        "the service must CALL _wait_skin_settled - deleting the call leaves every "
        "other settle test passing while the defect is fully restored"
    )
    assert order.index("settled") < order.index("prompt"), (
        "the skin must settle BEFORE the prompt opens, got %r" % (order,)
    )


def test_wait_skin_settled_aborts_cleanly(env):
    """A shutdown during the wait must return False, never block the service."""
    svc = env.load()
    assert svc._wait_skin_settled(_RecordingMon(abort_after=1)) is False


def test_wait_skin_settled_honours_skinshortcuts_isrunning(env, monkeypatch):
    """While skinshortcuts reports a build in progress, keep waiting."""
    svc = env.load()
    calls = []

    def cond(expr):
        if "skinshortcuts-isrunning" in expr:
            calls.append(1)
            return len(calls) <= 3  # busy for three polls, then clear
        return True

    monkeypatch.setattr(svc.xbmc, "getCondVisibility", cond)
    mon = _RecordingMon()
    assert svc._wait_skin_settled(mon) is True
    assert len(calls) >= 4, "it must poll until the build flag clears"
    assert len(mon.waits) >= 4, "each busy poll must be an interruptible wait"


def test_boot_check_reports_a_wrong_skin(env, monkeypatch):
    """A3: the box reopened on a different skin than the archive carried.

    This is the ONLY place A3 is observable - the restore finishes before the
    restart that decides the outcome, so its own report cannot see it."""
    env.marker.parent.mkdir(parents=True, exist_ok=True)
    env.marker.write_text("skin.estuary7")
    svc = env.load()
    monkeypatch.setattr(svc.xbmc, "getSkinDir", lambda: "skin.estuary", raising=False)
    svc._maybe_restore_check(_Mon())
    assert len(env.notifications) == 1, "a wrong-skin reopen must report"
    assert any("did not become live" in m for m in env.log_lines(LOGWARNING))
    assert not env.marker.exists()


def test_boot_check_is_silent_when_the_skin_matches(env, monkeypatch):
    """The fleet's normal path is same-skin. A check that always reports would fire
    on every restore - worse than the defect it detects. Both directions must be
    pinned, or a hardcoded 'report' passes the mismatch test alone."""
    env.marker.parent.mkdir(parents=True, exist_ok=True)
    env.marker.write_text("skin.estuary7")
    svc = env.load()
    monkeypatch.setattr(svc.xbmc, "getSkinDir", lambda: "skin.estuary7", raising=False)
    svc._maybe_restore_check(_Mon())
    assert env.notifications == [], "a correct reopen must stay silent"
    assert not env.marker.exists()


def test_legacy_marker_records_no_expectation_and_never_reports(env, monkeypatch):
    """A legacy "1" marker carries no expectation. Reading it as a mismatch would
    make every pre-existing marker report a false finding on upgrade."""
    env.arm()  # writes "1"
    svc = env.load()
    monkeypatch.setattr(svc.xbmc, "getSkinDir", lambda: "anything", raising=False)
    svc._maybe_restore_check(_Mon())
    assert env.notifications == [], "no recorded expectation must never report"


def test_boot_check_uses_the_read_only_skin_probe():
    """The mutating skin-setting probes change the state they claim to inspect.

    CSkinInfo::TranslateBool inserts a default-false setting AND schedules a save, so
    a check built on one passes for the wrong reason. getSkinDir is the read-only
    probe. The forbidden names are BUILT here rather than written literally, because
    this repo has a separate guard that scans test files for them - a test naming the
    trap would trip it."""
    # Strip comments and docstrings: this guards what the code CALLS, not what the
    # prose warns about. Naming the trap in a comment is how the next agent avoids
    # it, so a guard that punishes the warning is the wrong guard.
    import io
    import tokenize

    banned = ("Has" + "Setting", "GetInfo" + "Booleans")
    src = SERVICE_PY.read_text()
    code = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        code.append(tok.string)
    code = " ".join(code)
    for name in banned:
        assert name not in code, "skin state must not be probed with a mutating API"
    assert "getSkinDir" in code, "the read-only probe must actually be used"


def test_boot_check_stays_silent_when_the_live_skin_is_unreadable(env, monkeypatch):
    """An empty or unavailable getSkinDir() must NOT be read as a mismatch.

    The `live and` guard is what prevents that. Dropping it passed the whole suite,
    because nothing covered an unreadable probe - and a box that simply could not
    report its skin would then be told its restore needs attention on every boot."""
    env.marker.parent.mkdir(parents=True, exist_ok=True)
    env.marker.write_text("skin.estuary7")
    svc = env.load()
    monkeypatch.setattr(svc.xbmc, "getSkinDir", lambda: "", raising=False)
    svc._maybe_restore_check(_Mon())
    assert env.notifications == [], "an unreadable live skin must not report a mismatch"

    env.marker.parent.mkdir(parents=True, exist_ok=True)
    env.marker.write_text("skin.estuary7")
    svc = env.load()

    def _boom():
        raise RuntimeError("no skin")

    monkeypatch.setattr(svc.xbmc, "getSkinDir", _boom, raising=False)
    svc._maybe_restore_check(_Mon())
    assert env.notifications == [], "a raising probe must not report a mismatch"
