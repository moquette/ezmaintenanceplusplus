"""Coverage for script.ezmaintenanceplusplus tools.py after the IPTV removal.

EZ Maintenance++ has ZERO IPTV behavior. The former post-restore IPTV auto-enable intent
flag and the unattended boot gate (autoenable_iptv_after_restore) were REMOVED - they
auto-enabled an IPTV client that crashed natively on a real box. These tests prove, by
construction, that none of that machinery remains, and that the surviving buffer-prompt
marker helpers still work. Real tools.py is imported against faked Kodi modules.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).parent.parent / "script.ezmaintenanceplusplus"


@pytest.fixture
def tools(monkeypatch, tmp_path):
    settings = {}  # the fake Addon settings store (shared across Addon() calls)

    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 3
    xbmc.LOGERROR = 1
    xbmc.LOGWARNING = 2
    xbmc.log = lambda *a, **k: None
    xbmc.sleep = lambda ms: None
    xbmc.translatePath = lambda p: p
    xbmc.getInfoLabel = lambda *a, **k: ""
    xbmc.executeJSONRPC = lambda *a, **k: "{}"
    monkeypatch.setitem(sys.modules, "xbmc", xbmc)

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _Addon:
        def setSetting(self, k, v):
            settings[k] = v

        def getSetting(self, k):
            return settings.get(k, "")

        def getAddonInfo(self, _k):
            return ""

    xbmcaddon.Addon = lambda *a, **k: _Addon()
    monkeypatch.setitem(sys.modules, "xbmcaddon", xbmcaddon)

    xbmcgui = types.ModuleType("xbmcgui")

    class _DP:
        def create(self, *a, **k):
            pass

        def update(self, *a, **k):
            pass

        def close(self, *a, **k):
            pass

        def iscanceled(self):
            return False

    xbmcgui.DialogProgress = _DP
    xbmcgui.Dialog = lambda *a, **k: types.SimpleNamespace(
        select=lambda *a, **k: -1,
        ok=lambda *a, **k: None,
        notification=lambda *a, **k: None,
        input=lambda *a, **k: "",
    )
    monkeypatch.setitem(sys.modules, "xbmcgui", xbmcgui)

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: str(tmp_path / p.replace("special://home/", ""))
    xbmcvfs.exists = lambda p: True
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)

    control = types.ModuleType("resources.lib.modules.control")
    control.USERDATA = str(tmp_path / "userdata")
    monkeypatch.setitem(sys.modules, "resources.lib.modules.control", control)

    # ui is imported at module top; give a minimal stub.
    ui = types.ModuleType("resources.lib.modules.ui")
    monkeypatch.setitem(sys.modules, "resources.lib.modules.ui", ui)
    b2f = types.ModuleType("resources.lib.modules.backtothefuture")
    b2f.unicode = str
    b2f.PY2 = False
    monkeypatch.setitem(sys.modules, "resources.lib.modules.backtothefuture", b2f)

    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name.endswith(".tools") and "ezmaintenance" in str(
            getattr(sys.modules[name], "__file__", "")
        ):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delitem(sys.modules, "resources.lib.modules.tools", raising=False)
    mod = importlib.import_module("resources.lib.modules.tools")

    return types.SimpleNamespace(mod=mod, settings=settings)


def test_no_iptv_autoenable_api_remains(tools):
    # By construction: every IPTV auto-enable symbol is gone from tools.
    for gone in (
        "autoenable_iptv_after_restore",
        "mark_iptv_autoenable_pending",
        "iptv_autoenable_pending",
        "clear_iptv_autoenable_pending",
        "IPTV_PENDING",
    ):
        assert not hasattr(tools.mod, gone), "tools must not expose %s" % gone


def test_tools_source_has_no_iptv_tokens():
    src = (ADDON_ROOT / "resources" / "lib" / "modules" / "tools.py").read_text(
        encoding="utf-8"
    )
    for token in ("autoenable", "stage_iptv", "pvr_is_enabled", "set_pvr_enabled"):
        assert token not in src, "tools.py must not contain %r" % token


def test_no_post_restore_prompt_machinery_survives(tools):
    """The deleted popup must stay deleted, in code and not just in behaviour.

    Every name here was load-bearing for a boot-time modal that asked the user to
    repair values the restore had just cloned. They are gone together with the flow;
    a partial resurrection (say, re-adding the marker "just to record state") is how
    an unattended boot dialog comes back."""
    src = (ADDON_ROOT / "resources" / "lib" / "modules" / "tools.py").read_text(
        encoding="utf-8"
    )
    for token in (
        "BUFFER_PROMPT_MARKER",
        "mark_buffer_prompt_pending",
        "buffer_prompt_pending",
        "clear_buffer_prompt_marker",
        "prompt_buffer_after_restore",
        "prompt_devicename_after_restore",
        "prompt_after_restore",
        "arm_first_run_tuneup",
        "FIRST_RUN_FLAG",
        "_PROMPT_MAX_ATTEMPTS",
        "_PROMPT_MAX_BOOTS",
    ):
        assert token not in src, (
            "tools.py must not contain %r - the post-restore prompt was deleted, "
            "not disabled" % token
        )


def test_capture_device_identity_reads_both_live_values(tools, monkeypatch):
    """The capture reads THIS box's own name and buffer from the live settings."""
    monkeypatch.setattr(tools.mod, "_get_devicename", lambda: "Living Room")
    monkeypatch.setattr(tools.mod, "_get_cache_mb", lambda: 96)
    assert tools.mod.capture_device_identity() == {
        "services.devicename": "Living Room",
        "filecache.memorysize": 96,
    }


def test_capture_device_identity_omits_what_it_could_not_read(tools, monkeypatch):
    """An unreadable value is OMITTED, never defaulted.

    A default here would be written back over the archive as though it were this
    box's own value, which is worse than leaving the archive's: it would invent a
    name or a buffer size nobody chose."""
    monkeypatch.setattr(tools.mod, "_get_devicename", lambda: "")
    monkeypatch.setattr(tools.mod, "_get_cache_mb", lambda: None)
    assert tools.mod.capture_device_identity() == {}


def test_capture_device_identity_never_raises(tools, monkeypatch):
    """It runs as the first statement of restore(); a raise there would abort a
    restore over a cosmetic setting."""

    def boom():
        raise RuntimeError("json-rpc down")

    monkeypatch.setattr(tools.mod, "_get_devicename", boom)
    monkeypatch.setattr(tools.mod, "_get_cache_mb", boom)
    assert tools.mod.capture_device_identity() == {}


def test_restore_check_marker_round_trips_the_expected_skin(tools):
    """The marker must carry the archive's skin so the boot check has an expectation.

    Defect A3: the restore writes the archive's skin to disk and Kodi's shutdown flush
    then serializes the PRE-restore skin from live memory over it, so the box can
    reopen on the wrong one. The restore finishes BEFORE that restart, so the boot
    check is the only place the outcome is observable - and it can only report a
    mismatch if the expectation was recorded here."""
    t = tools.mod
    assert t.mark_restore_check_pending("skin.estuary7") is True
    assert t.restore_check_pending() is True
    assert t.restore_check_expected_skin() == "skin.estuary7"
    t.clear_restore_check_marker()
    assert t.restore_check_pending() is False
    assert t.restore_check_expected_skin() is None


def test_legacy_marker_carries_no_expectation(tools):
    """Markers written before A3 hold "1". Reading that as a skin name would make
    every pre-existing marker report a false wrong-skin finding on upgrade."""
    t = tools.mod
    assert t.mark_restore_check_pending() is True
    assert t.restore_check_pending() is True
    assert t.restore_check_expected_skin() is None, (
        'a legacy "1" marker must record NO expectation, never a skin named "1"'
    )
    t.clear_restore_check_marker()
    assert t.mark_restore_check_pending("") is True
    assert t.restore_check_expected_skin() is None, (
        "an empty skin (a restore that did not change the skin) records no expectation"
    )
