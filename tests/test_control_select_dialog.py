"""control.selectDialog -> xbmcgui.Dialog().select, the one hop nothing else tests.

WHY THIS FILE EXISTS
--------------------
Every other test in this suite stubs `control.selectDialog` wholesale, so a test
about what the Backup/Restore menu offered proves one fake called another fake.
The real one-line hop in control.py was covered by nothing, and it carries traps
a file-content test reads straight past:

Kodi's C++ signature is select(heading, list, autoclose, preselect, useDetails)
and this wrapper's is (list, heading) - reversed. A swap yields a dialog headed
with a Python list repr, and any third positional argument becomes `autoclose`
in milliseconds, i.e. a dialog that shuts itself before it can be read. Neither
mistake is visible to a test that stubs selectDialog, which every other test in
this suite does.

These tests drive the REAL control module against the shared dialog fake, which
records (args, kwargs) per call.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parents[1] / "script.ezmaintenanceplusplus"


@pytest.fixture
def control_mod(monkeypatch, fake_kodi):
    """Import the real control.py under the fake Kodi modules."""
    for pkg in ("resources", "resources.lib", "resources.lib.modules"):
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            m.__path__ = []
            monkeypatch.setitem(sys.modules, pkg, m)

    b2f = types.ModuleType("resources.lib.modules.backtothefuture")
    b2f.PY2 = False
    b2f.unicode = str
    monkeypatch.setitem(sys.modules, "resources.lib.modules.backtothefuture", b2f)

    ui = types.ModuleType("resources.lib.modules.ui")
    ui.HEADING = "EZ Maintenance++"
    monkeypatch.setitem(sys.modules, "resources.lib.modules.ui", ui)
    setattr(sys.modules["resources.lib.modules"], "ui", ui)

    sys.modules.pop("ezm_control_under_test", None)
    spec = importlib.util.spec_from_file_location(
        "ezm_control_under_test",
        ADDON_ROOT / "resources" / "lib" / "modules" / "control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ezm_control_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _calls(fake_kodi):
    return fake_kodi.xbmcgui.Dialog.select_calls


def test_the_heading_is_still_first_and_the_list_second(control_mod, fake_kodi):
    """Kodi takes (heading, list) in that order; control.selectDialog takes them
    the other way round. Swapping them yields a dialog headed with a Python list
    repr, which no fake would notice on its own."""
    rows = ["Backup", "Restore"]
    control_mod.selectDialog(rows, heading="Backup/Restore")

    args, kwargs = _calls(fake_kodi)[-1]
    assert args[0] == "Backup/Restore"
    assert args[1] == rows
    # Kodi's third positional is `autoclose`, in MILLISECONDS. Anything passed
    # through there shuts the dialog before it can be read, and no menu-level
    # test would see it because they all stub selectDialog.
    assert len(args) == 2, "a third positional argument is autoclose: %r" % (args,)
    assert not kwargs, kwargs
