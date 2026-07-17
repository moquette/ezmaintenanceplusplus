"""service.py one-shot stale NSUserDefaults key migration (tvOS only).

The 2026.07.08.2 - 2026.07.13.x "vector everything" releases pushed EVERY restored
userdata xml into NSUserDefaults. Boxes that ran them still hold keys for files
current nsud policy leaves as plain POSIX, and on tvOS a key SHADOWS the disk file,
so a freshly written or restored file can be silently invisible to Kodi forever.
service.py now runs nsud.purge_stale_keys(control.USERDATA) exactly once per add-on
version on tvOS, gated by a marker FILE in this add-on's own addon_data (the
tools.BUFFER_PROMPT_MARKER pattern - setSetting is unreliable around restores).

Pinned here:
* runs-once semantics: one purge per version, marker persisted, no re-run on the
  same version even across a service restart (fresh module import);
* a version bump re-arms exactly one more run;
* the tvOS gate: never runs on Fire TV / desktop, and an unknown version skips
  rather than risk running every boot;
* the hasattr guard: an nsud build without purge_stale_keys is a clean no-op that
  leaves the marker UNSET, so the purge still happens once a capable nsud ships;
* failure containment: a raising purge (or a garbage return shape) logs LOUDLY at
  LOGERROR, never raises, and leaves the marker unset so the next boot retries;
* the __main__ wiring: the migration is called at boot, before the first-run /
  post-restore steps (those may read files a stale key would shadow).

Same fixture approach as the other service tests: fake just enough of
xbmc*/resources.* for the real service.py to import, then call the real function.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"
SERVICE_PY = ADDON_ROOT / "service.py"

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGNOTICE = 0, 1, 2, 3, 4


class _Env:
    """Mutable knobs the fakes read at CALL time, so a test can flip platform or
    version between calls without reloading the module."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.tvos = True
        self.version = "2026.7.16.1"
        self.logs = []  # (level, msg)
        self.purge_calls = []  # userdata_root passed to purge_stale_keys
        self.userdata = str(tmp_path / "userdata")

    # ---- assertions helpers ----
    def log_lines(self, level):
        return [m for lv, m in self.logs if lv == level]

    def marker(self):
        return (
            self.tmp
            / "userdata/addon_data/script.ezmaintenanceplusplus/.ezm_stale_key_purge"
        )


def _nsud_stub(env, result=(2, 3, 4, 0), exc=None, with_fn=True):
    """A stand-in for resources.lib.modules.nsud. The REAL nsud may or may not have
    purge_stale_keys yet (it lands in a parallel change); stubbing keeps these tests
    deterministic either way and lets us script results/failures."""
    m = types.ModuleType("resources.lib.modules.nsud")
    if with_fn:

        def purge_stale_keys(userdata_root):
            env.purge_calls.append(userdata_root)
            if exc is not None:
                raise exc
            return result

        m.purge_stale_keys = purge_stale_keys
    return m


def _load_service(monkeypatch, env, nsud_mod):
    """Install the fakes and import the real service.py fresh. Returns the module."""
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGDEBUG = LOGDEBUG
    xbmc.LOGINFO = LOGINFO
    xbmc.LOGWARNING = LOGWARNING
    xbmc.LOGERROR = LOGERROR
    xbmc.LOGNOTICE = LOGNOTICE
    xbmc.log = lambda msg, level=LOGDEBUG: env.logs.append((level, msg))
    xbmc.translatePath = lambda p: p.replace("special://home/", str(env.tmp) + "/")
    xbmc.getCondVisibility = lambda cond: bool(env.tvos) if "TVOS" in cond else True
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
            if key == "version":
                return env.version
            return {"id": "script.ezmaintenanceplusplus"}.get(key, "")

    xbmcaddon.Addon = _FakeAddon

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.Dialog = lambda *a, **k: types.SimpleNamespace(
        yesno=lambda *a, **k: 0,
        select=lambda *a, **k: -1,
        ok=lambda *a, **k: None,
        notification=lambda *a, **k: None,
    )

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = xbmc.translatePath
    xbmcvfs.exists = lambda p: Path(p).exists()

    # Stub the whole resources.* tree: service.py's module-level imports
    # (backtothefuture, maintenance) plus the lazy control/nsud it pulls inside
    # _maybe_purge_stale_nsud_keys. No real add-on module is imported, so these
    # tests cannot be perturbed by concurrent changes to nsud/wiz/onetap.
    pkgs = {}
    for name in ("resources", "resources.lib", "resources.lib.modules"):
        m = types.ModuleType(name)
        m.__path__ = []
        pkgs[name] = m

    b2f = types.ModuleType("resources.lib.modules.backtothefuture")
    b2f.PY2 = False
    b2f.unicode = str

    maintenance = types.ModuleType("resources.lib.modules.maintenance")
    maintenance.logMaintenance = lambda *a, **k: None
    maintenance.determineNextMaintenance = lambda *a, **k: None
    maintenance.getNextMaintenance = lambda *a, **k: 0
    maintenance.clearCache = lambda *a, **k: None
    maintenance.purgePackages = lambda *a, **k: None
    maintenance.deleteThumbnails = lambda *a, **k: None

    control = types.ModuleType("resources.lib.modules.control")
    control.USERDATA = env.userdata

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
            "resources.lib.modules.nsud": nsud_mod,
        }
    )
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    # Bind submodules as attributes so `from resources.lib.modules import x` is
    # unambiguous regardless of import-machinery version quirks.
    pkgs["resources"].lib = pkgs["resources.lib"]
    pkgs["resources.lib"].modules = pkgs["resources.lib.modules"]
    for attr in ("backtothefuture", "maintenance", "control", "nsud"):
        setattr(
            pkgs["resources.lib.modules"], attr, mods["resources.lib.modules." + attr]
        )

    monkeypatch.delitem(sys.modules, "ezm_service_stale_key_under_test", raising=False)
    spec = importlib.util.spec_from_file_location(
        "ezm_service_stale_key_under_test", SERVICE_PY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = _Env(tmp_path)
    e.load = lambda nsud_mod: _load_service(monkeypatch, e, nsud_mod)
    return e


# ------------------------------------------------------------- runs-once core


def test_purge_runs_once_logs_counts_and_writes_marker(env):
    svc = env.load(_nsud_stub(env, result=(2, 3, 4, 0)))

    svc._maybe_purge_stale_nsud_keys()
    svc._maybe_purge_stale_nsud_keys()  # same version: must NOT run again

    assert env.purge_calls == [env.userdata]
    assert env.marker().read_text() == env.version
    info = [m for m in env.log_lines(LOGINFO) if "stale NSUserDefaults key purge" in m]
    assert len(info) == 1
    assert "2 materialized, 3 purged, 4 kept, 0 failed" in info[0]
    assert env.version in info[0]


def test_marker_survives_a_service_restart(env):
    svc = env.load(_nsud_stub(env))
    svc._maybe_purge_stale_nsud_keys()
    assert len(env.purge_calls) == 1

    # "Reboot": a brand-new import of service.py against the same disk state.
    svc2 = env.load(_nsud_stub(env))
    svc2._maybe_purge_stale_nsud_keys()
    assert len(env.purge_calls) == 1, "marker on disk must gate a fresh service too"


def test_version_bump_rearms_exactly_one_more_run(env):
    svc = env.load(_nsud_stub(env))
    svc._maybe_purge_stale_nsud_keys()
    assert len(env.purge_calls) == 1

    env.version = "2026.7.17.1"
    svc._maybe_purge_stale_nsud_keys()
    svc._maybe_purge_stale_nsud_keys()
    assert len(env.purge_calls) == 2
    assert env.marker().read_text() == "2026.7.17.1"


# ---------------------------------------------------------------- tvOS gating


def test_never_runs_on_non_tvos(env):
    env.tvos = False
    svc = env.load(_nsud_stub(env))
    svc._maybe_purge_stale_nsud_keys()
    assert env.purge_calls == []
    assert not env.marker().exists(), "non-tvOS must not burn the marker"


def test_platform_probe_failure_counts_as_non_tvos(env):
    svc = env.load(_nsud_stub(env))

    def _boom(cond):
        raise RuntimeError("no infolabels yet")

    sys.modules["xbmc"].getCondVisibility = _boom
    svc.xbmc.getCondVisibility = _boom
    svc._maybe_purge_stale_nsud_keys()
    assert env.purge_calls == []
    assert not env.marker().exists()


def test_unknown_version_skips_instead_of_running_every_boot(env):
    env.version = ""
    svc = env.load(_nsud_stub(env))
    svc._maybe_purge_stale_nsud_keys()
    assert env.purge_calls == []
    assert not env.marker().exists()


# -------------------------------------------------------------- hasattr guard


def test_nsud_without_purge_fn_is_a_clean_noop_that_keeps_the_marker_unset(env):
    old_nsud = _nsud_stub(env, with_fn=False)
    svc = env.load(old_nsud)

    svc._maybe_purge_stale_nsud_keys()  # must not raise, must not log an error
    assert env.purge_calls == []
    assert not env.marker().exists()
    assert env.log_lines(LOGERROR) == []

    # Once a capable nsud ships (same version), the purge still happens exactly once.
    def purge_stale_keys(userdata_root):
        env.purge_calls.append(userdata_root)
        return (1, 1, 0, 0)

    old_nsud.purge_stale_keys = purge_stale_keys
    svc._maybe_purge_stale_nsud_keys()
    assert env.purge_calls == [env.userdata]
    assert env.marker().read_text() == env.version


# --------------------------------------------------------- failure containment


def test_raising_purge_logs_loudly_never_raises_and_retries_next_boot(env):
    nsud = _nsud_stub(env, exc=RuntimeError("nsuserdefaults exploded"))
    svc = env.load(nsud)

    svc._maybe_purge_stale_nsud_keys()  # must not raise

    errors = [m for m in env.log_lines(LOGERROR) if "purge FAILED" in m]
    assert errors, "a failed purge must be loud in the log"
    assert "RuntimeError" in errors[0] and "nsuserdefaults exploded" in errors[0]
    assert not env.marker().exists(), "a failed purge must not burn the marker"

    # Next boot: same version, failure gone -> the purge runs and completes.
    svc2 = env.load(_nsud_stub(env))
    svc2._maybe_purge_stale_nsud_keys()
    assert len(env.purge_calls) == 2  # the raising call + the successful retry
    assert env.marker().read_text() == env.version


def test_garbage_return_shape_is_contained(env):
    nsud = types.ModuleType("resources.lib.modules.nsud")
    nsud.purge_stale_keys = lambda root: None  # not a 4-tuple
    svc = env.load(nsud)

    svc._maybe_purge_stale_nsud_keys()  # must not raise
    assert [m for m in env.log_lines(LOGERROR) if "purge FAILED" in m]
    assert not env.marker().exists()


# --------------------------------------------------------------- boot wiring


def test_mainline_calls_the_migration_before_first_run_and_restore_steps():
    """The __main__ block cannot be executed under pytest, so pin the wiring at
    source level: the migration is called at boot, and BEFORE the first-run /
    post-restore steps (they may read files a stale key would shadow)."""
    src = SERVICE_PY.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in src
    main_block = src.split('if __name__ == "__main__":', 1)[1]
    assert "_maybe_purge_stale_nsud_keys()" in main_block
    assert main_block.index("_maybe_purge_stale_nsud_keys()") < main_block.index(
        "_maybe_arm_first_run()"
    )
    assert main_block.index("_maybe_arm_first_run()") < main_block.index(
        "_maybe_prompt_after_restore(monitor)"
    )


# ------------------------------------------------- PVR pause crash-recovery


def _tools_stub(pending, enable_result="OK"):
    """A stand-in resources.lib.modules.tools recording marker clears, plus the
    result the boot JSON-RPC re-enable should return."""
    import types as _t

    m = _t.ModuleType("resources.lib.modules.tools")
    state = {"pending": pending, "cleared": False}
    m.pvr_pause_pending = lambda: state["pending"]
    m.clear_pvr_pause_marker = lambda: state.__setitem__("cleared", True)
    m._state = state
    return m


def _load_with_tools(monkeypatch, env, tools_mod, enable_result="OK"):
    mod = env.load(_nsud_stub(env))
    # Inject the tools stub and a scriptable executeJSONRPC into the loaded module.
    monkeypatch.setitem(sys.modules, "resources.lib.modules.tools", tools_mod)
    setattr(
        sys.modules["resources.lib.modules"], "tools", tools_mod
    )
    mod.xbmc.executeJSONRPC = lambda payload: '{"result": %s}' % (
        '"%s"' % enable_result if enable_result is not None else "null"
    )
    return mod


def test_resume_paused_pvr_reenables_and_clears_when_pending(monkeypatch, tmp_path):
    env = _Env(tmp_path)
    env.load = lambda nsud_mod: _load_service(monkeypatch, env, nsud_mod)
    tools_mod = _tools_stub(pending=True, enable_result="OK")
    mod = _load_with_tools(monkeypatch, env, tools_mod, enable_result="OK")
    mod._maybe_resume_paused_pvr()
    assert tools_mod._state["cleared"] is True
    assert any("re-enabled pvr.iptvsimple" in m for _l, m in env.logs)


def test_resume_paused_pvr_noop_when_not_pending(monkeypatch, tmp_path):
    env = _Env(tmp_path)
    env.load = lambda nsud_mod: _load_service(monkeypatch, env, nsud_mod)
    tools_mod = _tools_stub(pending=False)
    mod = _load_with_tools(monkeypatch, env, tools_mod)
    mod._maybe_resume_paused_pvr()
    assert tools_mod._state["cleared"] is False


def test_resume_paused_pvr_keeps_marker_when_reenable_fails(monkeypatch, tmp_path):
    env = _Env(tmp_path)
    env.load = lambda nsud_mod: _load_service(monkeypatch, env, nsud_mod)
    tools_mod = _tools_stub(pending=True, enable_result="Error")
    mod = _load_with_tools(monkeypatch, env, tools_mod, enable_result="Error")
    mod._maybe_resume_paused_pvr()
    # marker NOT cleared so the next boot retries
    assert tools_mod._state["cleared"] is False


def test_mainline_calls_pvr_recovery_at_boot():
    src = SERVICE_PY.read_text(encoding="utf-8")
    main_block = src.split('if __name__ == "__main__":', 1)[1]
    assert "_maybe_resume_paused_pvr()" in main_block
