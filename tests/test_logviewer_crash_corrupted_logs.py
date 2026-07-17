"""Log viewer / pastebin uploader vs crash-corrupted logs.

Root-caused on atv2 (2026-07-17): after a Kodi crash the log tail contains
invalid UTF-8 (heap garbage written during the crash). Both the viewer
(TextViewer.text_view) and the uploader (logviewer.logView select 1) used a
STRICT `.decode("UTF-8")`, and every failure was swallowed by logView's bare
`except: pass` - so exactly when the log matters most (right after a crash),
both features silently did nothing.

These tests import the REAL logviewer.py and TextViewer.py under fully faked
Kodi modules plus stubbed siblings (control/backtothefuture/pastebin), the
same pattern as test_menu_tool_actions.py, and pin the fixed contract:

  * crash-corrupted bytes decode with U+FFFD replacement markers (the markers
    themselves locate the crash point) instead of raising,
  * a failure inside logView is logged at LOGWARNING and closes the busy
    dialog instead of vanishing,
  * cancelling either select dialog exits cleanly (no [-1] indexing, no
    warning noise),
  * an empty log-file list shows a clear "No log files found" dialog instead
    of dying on IndexError.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"
MODULES = ADDON_ROOT / "resources" / "lib" / "modules"

# Real crash-tail shape: valid log lines, then raw heap garbage that is not
# valid UTF-8 (0xC3 0x28 is an invalid 2-byte sequence; 0xFF is an invalid
# start byte, the exact failure class seen at byte 230127 of atv2's
# kodi.old.log).
CORRUPTED_LOG = (
    b"2026-07-17 05:00:00.000 T:1 ERROR: something broke\n"
    b"2026-07-17 05:00:01.000 T:1 info: last good line\n"
    b"\xc3\x28\xff\xfe\x00heap garbage"
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
def _make_env(monkeypatch, logdir):
    """Install fake xbmc/xbmcaddon/xbmcgui/xbmcvfs + stub sibling modules.

    Returns a namespace of the scriptable fakes; logdir is what
    special://logpath translates to.
    """
    ns = types.SimpleNamespace()

    # ---- xbmc ---- #
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGDEBUG = 0
    xbmc.LOGINFO = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGERROR = 3
    xbmc.log_lines = []
    xbmc.log = lambda msg, level=1: xbmc.log_lines.append((level, msg))
    xbmc.executed = []
    xbmc.executebuiltin = lambda cmd: xbmc.executed.append(cmd)
    monkeypatch.setitem(sys.modules, "xbmc", xbmc)

    # ---- xbmcaddon ---- #
    xbmcaddon = types.ModuleType("xbmcaddon")

    class _Addon:
        def __init__(self, *a, **k):
            pass

        def getAddonInfo(self, key):
            return {
                "id": "script.ezmaintenanceplusplus",
                "name": "EZ Maintenance++",
                "path": "/fake/addon/path",
            }.get(key, "")

    xbmcaddon.Addon = _Addon
    monkeypatch.setitem(sys.modules, "xbmcaddon", xbmcaddon)

    # ---- xbmcgui ---- #
    xbmcgui = types.ModuleType("xbmcgui")

    class _Dialog:
        ok_calls = []

        def ok(self, *a, **k):
            _Dialog.ok_calls.append(a)
            return True

    _Dialog.ok_calls = []

    class _DialogProgress:
        def create(self, *a, **k):
            return None

        def update(self, *a, **k):
            return None

        def close(self, *a, **k):
            return None

    class _WindowXML:
        """Fake base: doModal records but never runs the Kodi UI loop."""

        modal_count = 0

        def __init__(self, *a, **k):
            pass

        def doModal(self):
            _WindowXML.modal_count += 1

    _WindowXML.modal_count = 0

    xbmcgui.Dialog = _Dialog
    xbmcgui.DialogProgress = _DialogProgress
    xbmcgui.WindowXML = _WindowXML
    monkeypatch.setitem(sys.modules, "xbmcgui", xbmcgui)

    # ---- xbmcvfs ---- #
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: str(logdir) if p == "special://logpath" else p
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

    # ---- control stub: selectDialog pops scripted results ---- #
    control = types.ModuleType("resources.lib.modules.control")
    control.select_results = []  # queue; empty queue means cancel (-1)
    control.select_calls = []

    def _select(options, *a, **k):
        control.select_calls.append(list(options))
        if control.select_results:
            return control.select_results.pop(0)
        return -1

    control.selectDialog = _select
    _submodule("control", control)

    # ---- backtothefuture stub ---- #
    b2f = types.ModuleType("resources.lib.modules.backtothefuture")
    b2f.PY2 = False
    b2f.unicode = str
    _submodule("backtothefuture", b2f)

    # ---- pastebin stub ---- #
    pastebin = types.ModuleType("resources.lib.modules.pastebin")
    pastebin.paste_texts = []
    pastebin.paste_result = "https://pastebin.com/fake123"
    pastebin.paste_raises = None

    class _Api:
        def paste(self, text):
            if pastebin.paste_raises is not None:
                raise pastebin.paste_raises
            pastebin.paste_texts.append(text)
            return pastebin.paste_result

    pastebin.api = _Api
    _submodule("pastebin", pastebin)

    ns.xbmc = xbmc
    ns.xbmcgui = xbmcgui
    ns.Dialog = _Dialog
    ns.WindowXML = _WindowXML
    ns.control = control
    ns.pastebin = pastebin
    ns.submodule = _submodule
    return ns


def _import_module(name):
    """Import a real add-on module fresh under whatever fakes are installed."""
    modname = "resources.lib.modules.%s" % name
    sys.modules.pop(modname, None)
    spec = importlib.util.spec_from_file_location(modname, MODULES / ("%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    setattr(sys.modules["resources.lib.modules"], name, mod)
    return mod


@pytest.fixture
def env(monkeypatch, tmp_path):
    logdir = tmp_path / "logs"
    logdir.mkdir()
    ns = _make_env(monkeypatch, logdir)
    ns.logdir = logdir
    return ns


def _warnings(env):
    return [msg for level, msg in env.xbmc.log_lines if level == env.xbmc.LOGWARNING]


# --------------------------------------------------------------------------- #
# logView upload branch: crash-corrupted bytes must still upload
# --------------------------------------------------------------------------- #
def test_upload_decodes_crash_corrupted_log_with_replacement(env):
    (env.logdir / "kodi.old.log").write_bytes(CORRUPTED_LOG)
    logviewer = _import_module("logviewer")
    env.control.select_results = [1, 0]  # Upload, then the only listed log

    logviewer.logView()

    assert len(env.pastebin.paste_texts) == 1
    uploaded = env.pastebin.paste_texts[0]
    # The valid prefix survives verbatim; the garbage tail becomes U+FFFD
    # markers (which locate the crash point in the paste).
    assert "last good line" in uploaded
    assert "�" in uploaded
    # Success dialog shown, nothing logged as a failure.
    assert any("Log Uploaded" in str(a) for a in env.Dialog.ok_calls)
    assert _warnings(env) == []


def test_upload_clean_log_unchanged(env):
    (env.logdir / "kodi.log").write_bytes(b"all good\n")
    logviewer = _import_module("logviewer")
    env.control.select_results = [1, 0]

    logviewer.logView()

    assert env.pastebin.paste_texts == ["all good\n"]
    assert "�" not in env.pastebin.paste_texts[0]
    assert _warnings(env) == []


# --------------------------------------------------------------------------- #
# logView failure path: loud in the log, busy dialog closed
# --------------------------------------------------------------------------- #
def test_upload_failure_is_logged_and_closes_busy_dialog(env):
    (env.logdir / "kodi.log").write_bytes(b"fine\n")
    logviewer = _import_module("logviewer")
    env.control.select_results = [1, 0]
    env.pastebin.paste_raises = RuntimeError("pastebin exploded")

    logviewer.logView()  # must not raise

    warnings = _warnings(env)
    assert len(warnings) == 1
    assert "logView failed" in warnings[0]
    assert "pastebin exploded" in warnings[0]
    # The non-cancellable busy dialog opened for the upload must not be left
    # covering the screen.
    assert "Dialog.Close(busydialognocancel)" in env.xbmc.executed


# --------------------------------------------------------------------------- #
# logView cancel paths: clean exit, no [-1] indexing, no warning noise
# --------------------------------------------------------------------------- #
def test_cancel_at_mode_select_exits_cleanly(env):
    (env.logdir / "kodi.log").write_bytes(b"fine\n")
    logviewer = _import_module("logviewer")
    env.control.select_results = [-1]

    logviewer.logView()

    assert env.control.select_calls == [["View Log", "Upload Log to Pastebin"]]
    assert env.pastebin.paste_texts == []
    assert env.Dialog.ok_calls == []
    assert _warnings(env) == []


def test_cancel_at_log_select_exits_cleanly(env):
    # Two logs so [-1] would silently pick the LAST one under the old
    # `logPaths[selectLog]`-before-cancel-check ordering.
    (env.logdir / "kodi.log").write_bytes(b"current\n")
    (env.logdir / "kodi.old.log").write_bytes(b"old\n")
    logviewer = _import_module("logviewer")
    env.control.select_results = [1, -1]  # Upload, then cancel the picker

    logviewer.logView()

    assert env.pastebin.paste_texts == []
    assert env.Dialog.ok_calls == []
    assert _warnings(env) == []


def test_empty_log_list_shows_clear_dialog(env):
    # No log files at all: the old code raised IndexError inside the bare
    # except and the user saw nothing.
    logviewer = _import_module("logviewer")
    env.control.select_results = [0]

    logviewer.logView()

    # Only the mode select ran; the (empty) log picker was never shown.
    assert len(env.control.select_calls) == 1
    assert len(env.Dialog.ok_calls) == 1
    message = " ".join(str(part) for part in env.Dialog.ok_calls[0])
    assert "No log files found" in message
    assert str(env.logdir) in message
    assert _warnings(env) == []


# --------------------------------------------------------------------------- #
# logView view branch routes to TextViewer with the selected path
# --------------------------------------------------------------------------- #
def test_view_branch_passes_selected_path(env):
    (env.logdir / "kodi.log").write_bytes(b"current\n")
    (env.logdir / "kodi.old.log").write_bytes(b"old\n")
    logviewer = _import_module("logviewer")

    viewer_stub = types.ModuleType("resources.lib.modules.TextViewer")
    viewer_stub.viewed = []
    viewer_stub.text_view = lambda loc="", data="": viewer_stub.viewed.append(loc)
    env.submodule("TextViewer", viewer_stub)

    env.control.select_results = [0, 1]  # View, then the second log

    logviewer.logView()

    assert viewer_stub.viewed == [str(env.logdir / "kodi.old.log")]
    assert _warnings(env) == []


# --------------------------------------------------------------------------- #
# TextViewer: crash-corrupted bytes must still display
# --------------------------------------------------------------------------- #
def test_text_view_decodes_crash_corrupted_log(env):
    log_path = env.logdir / "kodi.old.log"
    log_path.write_bytes(CORRUPTED_LOG)
    TextViewer = _import_module("TextViewer")

    TextViewer.text_view(str(log_path))  # must not raise

    # The window was actually shown (the old code died before doModal).
    assert env.WindowXML.modal_count == 1
    assert isinstance(TextViewer.contents, str)
    assert "last good line" in TextViewer.contents
    assert "�" in TextViewer.contents
    # Post-decode colorization still applied.
    assert "[COLOR red]ERROR[/COLOR]:" in TextViewer.contents


def test_text_view_empty_file_notice(env):
    log_path = env.logdir / "kodi.log"
    log_path.write_bytes(b"")
    TextViewer = _import_module("TextViewer")

    TextViewer.text_view(str(log_path))

    assert env.WindowXML.modal_count == 0
    assert any("empty" in str(a) for a in env.Dialog.ok_calls)
