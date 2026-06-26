"""
Adversarial QA harness for EZ Maintenance++ Dropbox backup feature.

We import the REAL add-on module `dropbox_remote.py` under fully-faked Kodi
modules + a fake `requests`, exactly the way Kodi exposes them at runtime, so we
exercise the shipped REST/chunk/token/pagination logic directly. No source is
modified; everything here is test-only scaffolding.
"""

import importlib
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"


# --------------------------------------------------------------------------- #
# Fake Kodi modules
# --------------------------------------------------------------------------- #
class _FakeAddon:
    """One shared settings dict across all Addon() instances (Kodi semantics)."""

    _settings = {}

    def __init__(self, id=None):
        self.id = id

    def getSetting(self, key):
        return _FakeAddon._settings.get(key, "")

    def setSetting(self, key, value):
        _FakeAddon._settings[key] = value

    def getAddonInfo(self, key):
        return {"id": "script.ezmaintenanceplusplus", "name": "EZ Maintenance++"}.get(
            key, ""
        )

    def openSettings(self):
        return None


class _FakeDialog:
    last_ok = None
    inputs = []  # queue of strings to return from input()

    def ok(self, *a, **k):
        _FakeDialog.last_ok = a
        return True

    def input(self, *a, **k):
        if _FakeDialog.inputs:
            return _FakeDialog.inputs.pop(0)
        return ""

    def select(self, *a, **k):
        return -1

    def notification(self, *a, **k):
        return None

    def yesno(self, *a, **k):
        return True


class _FakeDialogProgress:
    def create(self, *a, **k):
        return None

    def update(self, *a, **k):
        return None

    def close(self, *a, **k):
        return None

    def iscanceled(self):
        return False


# captured log lines so redaction tests can assert on them
LOG_LINES = []


def _make_xbmc():
    m = types.ModuleType("xbmc")
    m.LOGINFO = 1
    m.LOGWARNING = 2
    m.LOGERROR = 3
    m.LOGDEBUG = 0

    def _log(msg, level=1):
        LOG_LINES.append((level, msg))

    m.log = _log
    m.translatePath = lambda p: p  # PY2 path; unused here
    m.executebuiltin = lambda *a, **k: None
    m.sleep = lambda *a, **k: None
    return m


def _make_xbmcaddon():
    m = types.ModuleType("xbmcaddon")
    m.Addon = _FakeAddon
    return m


def _make_xbmcgui():
    m = types.ModuleType("xbmcgui")
    m.Dialog = _FakeDialog
    m.DialogProgress = _FakeDialogProgress
    m.INPUT_ALPHANUM = 0
    m.NOTIFICATION_INFO = "info"
    m.NOTIFICATION_WARNING = "warning"
    m.NOTIFICATION_ERROR = "error"
    return m


class _FakeStat:
    def __init__(self, size):
        self._size = size

    def st_size(self):
        return self._size


class _FakeFile:
    """xbmcvfs.File context manager returning bytes via readBytes()."""

    _payloads = {}  # path -> bytes

    def __init__(self, path, mode="r"):
        self.path = path
        self._data = _FakeFile._payloads.get(path, b"")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def readBytes(self):
        return self._data


def _make_xbmcvfs():
    m = types.ModuleType("xbmcvfs")
    # translatePath maps special://temp/<x> to a real local temp path the test sets
    m._temp_map = {}

    def _translate(p):
        return m._temp_map.get(p, p)

    m.translatePath = _translate
    m.Stat = _FakeStat
    m.File = _FakeFile

    def _listdir(path):
        return m._listdir_result.get(path, ([], []))

    m._listdir_result = {}
    m.listdir = _listdir
    m._deleted = []
    m.delete = lambda p: m._deleted.append(p)
    m.copy = lambda a, b: True
    m.exists = lambda p: True
    m.mkdir = lambda p: True
    m.rmdir = lambda p: True
    return m


# --------------------------------------------------------------------------- #
# Fake requests
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, content=b""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self._content = content

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1):
        data = self._content
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]


class FakeRequests:
    """Records every POST and returns scripted responses."""

    def __init__(self):
        self.calls = []  # list of dict(url, headers, data, json, stream, timeout)
        self.responder = None  # callable(call_index, call_dict) -> FakeResponse

    def post(self, url, headers=None, data=None, stream=False, timeout=None, json=None):
        call = {
            "url": url,
            "headers": dict(headers or {}),
            "data": data,
            "json": json,
            "stream": stream,
            "timeout": timeout,
        }
        idx = len(self.calls)
        self.calls.append(call)
        if self.responder is None:
            return FakeResponse(200, {})
        return self.responder(idx, call)


@pytest.fixture
def fake_kodi(monkeypatch):
    """Install fake kodi + requests modules; return a namespace handle."""
    LOG_LINES.clear()
    _FakeAddon._settings = {}
    _FakeDialog.last_ok = None
    _FakeDialog.inputs = []
    _FakeFile._payloads = {}

    xbmc = _make_xbmc()
    xbmcaddon = _make_xbmcaddon()
    xbmcgui = _make_xbmcgui()
    xbmcvfs = _make_xbmcvfs()
    fake_requests = FakeRequests()

    mods = {
        "xbmc": xbmc,
        "xbmcaddon": xbmcaddon,
        "xbmcgui": xbmcgui,
        "xbmcvfs": xbmcvfs,
        "requests": fake_requests,
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Make `from resources.lib.modules._appauth import ...` resolvable but EMPTY
    # so dropbox_remote falls back to the settings-based key/secret (we don't want
    # the real baked secret in the test process). We register a stub package tree.
    for pkg in ("resources", "resources.lib", "resources.lib.modules"):
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            m.__path__ = []
            monkeypatch.setitem(sys.modules, pkg, m)
    # _appauth absent -> dropbox_remote's try/except import falls through to settings

    # Import the real module fresh
    sys.modules.pop("dropbox_remote", None)
    spec = importlib.util.spec_from_file_location(
        "dropbox_remote",
        ADDON_ROOT / "resources" / "lib" / "modules" / "dropbox_remote.py",
    )
    dbx = importlib.util.module_from_spec(spec)
    sys.modules["dropbox_remote"] = dbx
    spec.loader.exec_module(dbx)

    ns = types.SimpleNamespace(
        dbx=dbx,
        requests=fake_requests,
        xbmc=xbmc,
        xbmcvfs=xbmcvfs,
        xbmcgui=xbmcgui,
        addon=_FakeAddon,
        FakeResponse=FakeResponse,
        log_lines=LOG_LINES,
        FakeFile=_FakeFile,
    )
    # reset module bearer cache between tests
    dbx._cache["bearer"] = None
    dbx._cache["exp"] = 0
    return ns
