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


def test_buffer_prompt_marker_roundtrip(tools):
    # The surviving, non-IPTV post-restore marker still works end to end.
    assert tools.mod.buffer_prompt_pending() is False
    tools.mod.mark_buffer_prompt_pending()
    assert tools.mod.buffer_prompt_pending() is True
    tools.mod.clear_buffer_prompt_marker()
    assert tools.mod.buffer_prompt_pending() is False


def test_first_run_arms_tuneup_on_fresh_install(tools):
    """A genuinely fresh install (no flag, no settings.xml) arms the tune-up marker
    and writes the first-run flag, exactly once."""
    import os

    assert tools.mod.buffer_prompt_pending() is False
    assert tools.mod.arm_first_run_tuneup() is True
    assert tools.mod.buffer_prompt_pending() is True
    assert os.path.exists(tools.mod.FIRST_RUN_FLAG)
    # Second boot: flag present, no re-arm even after the prompt cleared the marker.
    tools.mod.clear_buffer_prompt_marker()
    assert tools.mod.arm_first_run_tuneup() is False
    assert tools.mod.buffer_prompt_pending() is False


def test_first_run_suppressed_on_upgraded_box(tools):
    """A box that ran an older EZM++ (its settings.xml already exists when the flag is
    first checked) gets the flag written but is NOT prompted: shipping the feature must
    not re-prompt the whole fleet."""
    import os

    d = os.path.dirname(tools.mod.FIRST_RUN_FLAG)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(os.path.join(d, "settings.xml"), "w") as f:
        f.write("<settings/>")
    assert tools.mod.arm_first_run_tuneup() is False
    assert tools.mod.buffer_prompt_pending() is False
    assert os.path.exists(tools.mod.FIRST_RUN_FLAG)
    # And it stays quiet on every later boot.
    assert tools.mod.arm_first_run_tuneup() is False
    assert tools.mod.buffer_prompt_pending() is False


def test_first_run_never_clears_a_pending_restore_marker(tools):
    """If a restore already armed the tune-up (marker present when the first-run check
    happens, e.g. first boot after restoring onto a fresh box), the first-run check must
    leave it pending."""
    tools.mod.mark_buffer_prompt_pending()
    tools.mod.arm_first_run_tuneup()
    assert tools.mod.buffer_prompt_pending() is True


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
