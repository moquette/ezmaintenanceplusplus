"""Unit tests for _kodisettings.apply_guisettings (the tvOS restore fix).

Re-applying settings through JSON-RPC is what makes a restore survive on tvOS (where a
file-only guisettings.xml is reverted from NSUserDefaults on boot). These tests run the
parse + type-coerce + apply logic under a fake xbmc.executeJSONRPC.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"


class FakeXbmc:
    def __init__(self):
        self.set_calls = []
        self.settings = {}  # id -> {"id", "type", "value"}

    def executeJSONRPC(self, req):
        d = json.loads(req)
        method = d["method"]
        if method == "Settings.GetSettings":
            return json.dumps({"result": {"settings": list(self.settings.values())}})
        if method == "Settings.SetSettingValue":
            p = d["params"]
            self.set_calls.append((p["setting"], p["value"]))
            return json.dumps({"result": True})
        return json.dumps({"result": {}})


@pytest.fixture
def kodisettings(monkeypatch):
    fake = FakeXbmc()
    xbmc_mod = types.ModuleType("xbmc")
    xbmc_mod.executeJSONRPC = fake.executeJSONRPC
    monkeypatch.setitem(sys.modules, "xbmc", xbmc_mod)
    sys.modules.pop("_kodisettings", None)
    spec = importlib.util.spec_from_file_location(
        "_kodisettings",
        ADDON_ROOT / "resources" / "lib" / "modules" / "_kodisettings.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_kodisettings"] = mod
    spec.loader.exec_module(mod)
    return types.SimpleNamespace(mod=mod, fake=fake)


def _write_guisettings(tmp_path, entries):
    lines = ['<settings version="2">']
    for sid, text in entries:
        lines.append('  <setting id="%s">%s</setting>' % (sid, text))
    lines.append("</settings>")
    p = tmp_path / "guisettings.xml"
    p.write_text("\n".join(lines))
    return str(p)


def test_apply_coerces_types_and_skips_unchanged(kodisettings, tmp_path):
    ks, fake = kodisettings.mod, kodisettings.fake
    fake.settings = {
        "lookandfeel.skin": {
            "id": "lookandfeel.skin",
            "type": "string",
            "value": "skin.estuary",
        },
        "audiooutput.volumesteps": {
            "id": "audiooutput.volumesteps",
            "type": "integer",
            "value": 90,
        },
        "ear.enable": {"id": "ear.enable", "type": "boolean", "value": False},
        "some.action": {"id": "some.action", "type": "action", "value": ""},
    }
    gs = _write_guisettings(
        tmp_path,
        [
            ("lookandfeel.skin", "skin.estuary.modv2"),  # changed string -> applied
            ("audiooutput.volumesteps", "90"),  # unchanged int -> skipped
            ("ear.enable", "true"),  # changed, coerced to bool -> applied
            ("some.action", "doit"),  # action type -> skipped
            ("unknown.id", "x"),  # not a live setting -> skipped
        ],
    )
    n = ks.apply_guisettings(gs)
    applied = dict(fake.set_calls)
    assert applied.get("lookandfeel.skin") == "skin.estuary.modv2"
    assert applied.get("ear.enable") is True  # coerced str "true" -> bool True
    assert "audiooutput.volumesteps" not in applied  # unchanged -> not re-applied
    assert "some.action" not in applied  # action type skipped
    assert "unknown.id" not in applied  # unknown id skipped
    assert n == 2


def test_apply_missing_file_returns_zero(kodisettings, tmp_path):
    assert kodisettings.mod.apply_guisettings(str(tmp_path / "nope.xml")) == 0
