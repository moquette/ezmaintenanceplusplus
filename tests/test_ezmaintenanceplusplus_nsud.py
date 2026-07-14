"""Coverage for script.ezmaintenanceplusplus's nsud.py (Apple TV restore durability).

nsud re-writes restored userdata *.xml THROUGH xbmcvfs so tvOS vectors them into
NSUserDefaults. The load-bearing correctness rule is the SINGLE write per file: Kodi's
tvOS CTVOSFile::Write REPLACES the whole NSUserDefaults key on every call, so a chunked
write would leave only the last chunk (a truncated XML fragment). The fake xbmcvfs.File
below models that replace-per-write semantics, so a regression to chunking fails these
tests exactly the way a real Apple TV would corrupt the settings.

nsud imports only os/json (real) + xbmc/xbmcvfs (faked here), so it is exercised as the
real module in isolation, no heavy add-on import chain needed.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ADDON_MODULES = (
    Path(__file__).parent.parent
    / "script.ezmaintenanceplusplus"
    / "resources"
    / "lib"
    / "modules"
)


class _FakeFile:
    """Models tvOS CTVOSFile: each write() REPLACES the whole stored value for the path,
    and read() serves that stored value back (as NSUserDefaults does)."""

    def __init__(self, store, writes, state, path, mode):
        self._store = store
        self._writes = writes
        self._state = state
        self._path = path
        self._mode = mode

    def write(self, data):
        self._writes.append((self._path, bytes(data)))  # record every write call
        if self._state.get("fail_writes") or self._path in self._state.get(
            "fail_paths", set()
        ):
            return False
        self._store[self._path] = bytes(
            data
        )  # REPLACE (not append) — the tvOS semantics
        return True

    def readBytes(self):
        # Read-back path. `evict_on_readback` models the tvOS store silently dropping a key
        # despite write()==True (the ~500 KB budget) — read returns empty though write said OK.
        if self._path in self._state.get("evict_on_readback", set()):
            return bytearray(b"")
        return bytearray(self._store.get(self._path, b""))

    def close(self):
        pass


@pytest.fixture
def nsud(monkeypatch):
    """Import the real nsud.py with faked xbmc/xbmcvfs; expose recorders."""
    store: dict[str, bytes] = {}  # special path -> final bytes in "NSUserDefaults"
    writes: list[tuple[str, bytes]] = []  # every (path, bytes) write call
    events: list[str] = []  # ordered trace: enable:.. / sleep / write:<path>
    state = {"fail_writes": False}

    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 3
    xbmc.LOGERROR = 1
    xbmc.sleep = lambda ms: events.append("sleep")
    xbmc.log = lambda *a, **k: None

    def _execute_jsonrpc(s):
        import json

        req = json.loads(s)
        if req.get("method") == "Addons.SetAddonEnabled":
            events.append("enable:%s" % req["params"]["enabled"])
        return json.dumps({"result": "OK"})

    xbmc.executeJSONRPC = _execute_jsonrpc

    xbmcvfs = types.ModuleType("xbmcvfs")

    def _make_file(path, mode="r"):
        if "w" in mode:
            events.append(
                "write-open:%s" % path
            )  # only writes matter to ordering traces
        return _FakeFile(store, writes, state, path, mode)

    # File() records the OPEN in events on construction so ordering vs enable/sleep is
    # captured even though the write itself happens on .write().
    xbmcvfs.File = _make_file

    monkeypatch.setitem(sys.modules, "xbmc", xbmc)
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)
    monkeypatch.syspath_prepend(str(ADDON_MODULES))
    monkeypatch.delitem(sys.modules, "nsud", raising=False)
    mod = importlib.import_module("nsud")

    return types.SimpleNamespace(
        mod=mod, store=store, writes=writes, events=events, state=state
    )


def _write(base: Path, rel: str, content: bytes = b"<x/>") -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


# --------------------------------------------------------------------------- #
# The core invariant: ONE write per file, full content (the anti-chunking guard).
# --------------------------------------------------------------------------- #
def test_single_write_per_file_full_content(nsud, tmp_path):
    big = b"<settings>" + b"x" * 100_000 + b"</settings>"
    _write(tmp_path, "guisettings.xml", big)

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    special = "special://home/userdata/guisettings.xml"
    per_file = [w for w in nsud.writes if w[0] == special]
    assert len(per_file) == 1, "must be exactly ONE write() per file — never chunk"
    assert nsud.store[special] == big, "the whole file must land, not a tail fragment"


# --------------------------------------------------------------------------- #
# Exclusions: the add-on's own settings (secret + boot-crash) only. IPTV is NOT special-cased.
# --------------------------------------------------------------------------- #
def test_excludes_own_settings_secret(nsud, tmp_path):
    _write(
        tmp_path,
        "addon_data/script.ezmaintenanceplusplus/settings.xml",
        b'<settings><setting id="dropbox_refresh_token">SECRET</setting></settings>',
    )
    _write(tmp_path, "guisettings.xml")

    written, skipped, failed = nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert (
        "special://home/userdata/addon_data/script.ezmaintenanceplusplus/settings.xml"
        not in nsud.store
    )
    assert not any(b"SECRET" in v for v in nsud.store.values())
    assert skipped >= 1 and written >= 1


def test_general_walk_does_not_special_case_pvr(nsud, tmp_path):
    # IPTV handling is gone: the generic durability re-write treats a pvr.iptvsimple xml
    # like any other userdata xml (same-bytes rewrite so a restore sticks on tvOS). It does
    # NOT enable, disable, stage, or otherwise MANAGE the IPTV client - it only rewrites a
    # file that is already on disk. No special-casing either way.
    _write(tmp_path, "addon_data/pvr.iptvsimple/instance-settings-1.xml")
    _write(tmp_path, "RssFeeds.xml")

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert "special://home/userdata/RssFeeds.xml" in nsud.store
    assert (
        "special://home/userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml"
        in nsud.store
    ), "no special-casing: a present pvr xml is rewritten like any other, never managed"


def test_generic_exclude_dir_prefixes_opt_out(nsud, tmp_path):
    # The generic opt-out still works for a caller that passes it, but its DEFAULT is empty
    # and it names no add-on (no pvr special-casing baked in).
    _write(tmp_path, "addon_data/foo/settings.xml")
    _write(tmp_path, "RssFeeds.xml")

    nsud.mod.rewrite_userdata_xml(
        str(tmp_path), exclude_dir_prefixes=("addon_data/foo/",)
    )

    assert "special://home/userdata/RssFeeds.xml" in nsud.store
    assert not any("addon_data/foo" in p for p in nsud.store)


def test_no_iptv_or_pvr_management_api(nsud):
    # By construction: the IPTV/pvr enable-disable-stage-probe machinery is GONE from nsud.
    for gone in (
        "stage_iptv_disabled",
        "set_pvr_enabled",
        "pvr_is_enabled",
        "iptv_probe_targets",
        "iptv_share_reachable",
        "_set_pvr_enabled",
    ):
        assert not hasattr(nsud.mod, gone), "nsud must not expose %s" % gone


def test_non_xml_files_skipped(nsud, tmp_path):
    _write(tmp_path, "Database/MyVideos.db", b"sqlite")
    _write(tmp_path, "Thumbnails/a.jpg", b"jpeg")
    _write(tmp_path, "keyboard.xml")

    written, _skipped, _failed = nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert list(nsud.store) == ["special://home/userdata/keyboard.xml"]
    assert written == 1


def test_write_failure_leaves_source_and_counts_failed(nsud, tmp_path):
    _write(tmp_path, "guisettings.xml", b"<settings/>")
    nsud.state["fail_writes"] = True

    written, _skipped, failed = nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert written == 0 and failed == 1
    # POSIX source is untouched (no data loss — worst case is the pre-existing shadow).
    assert (tmp_path / "guisettings.xml").read_bytes() == b"<settings/>"


# --------------------------------------------------------------------------- #
# tvOS duplicate-entry fix: after a CONFIRMED vector into NSUserDefaults, and ONLY on tvOS,
# drop the redundant POSIX copy so File Manager stops listing every userdata file twice.
# The gate is a hard safety boundary — on any other platform special://home/userdata IS the
# POSIX file, so dropping it would delete what was just written.
# --------------------------------------------------------------------------- #
def _enable_tvos(monkeypatch):
    """Make the faked xbmc report Apple TV, as nsud._is_tvos() checks."""
    monkeypatch.setattr(
        sys.modules["xbmc"],
        "getCondVisibility",
        lambda cond: "TVOS" in cond,
        raising=False,
    )


def test_tvos_drops_posix_after_confirmed_vector(nsud, tmp_path, monkeypatch):
    _enable_tvos(monkeypatch)
    _write(tmp_path, "guisettings.xml", b"<settings/>")
    _write(tmp_path, "RssFeeds.xml", b"<rss/>")

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    # Vectored into NSUserDefaults (the durable store)...
    assert nsud.store["special://home/userdata/guisettings.xml"] == b"<settings/>"
    # ...and the now-redundant POSIX copies are gone (no duplicate File Manager entry).
    assert not (tmp_path / "guisettings.xml").exists()
    assert not (tmp_path / "RssFeeds.xml").exists()


def test_non_tvos_never_drops_posix(nsud, tmp_path):
    # The default fake xbmc has no getCondVisibility -> _is_tvos() False -> POSIX kept.
    # This is the CATASTROPHE guard: on Fire TV/desktop the special:// path IS the disk file.
    _write(tmp_path, "guisettings.xml", b"<settings/>")

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert (tmp_path / "guisettings.xml").read_bytes() == b"<settings/>"


def test_tvos_keeps_posix_when_vector_fails(nsud, tmp_path, monkeypatch):
    # Ordered write-then-delete: never drop a file whose bytes are not confirmed in the store.
    _enable_tvos(monkeypatch)
    nsud.state["fail_writes"] = True
    _write(tmp_path, "guisettings.xml", b"<settings/>")

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert (tmp_path / "guisettings.xml").read_bytes() == b"<settings/>"


def test_tvos_does_not_drop_excluded_file(nsud, tmp_path, monkeypatch):
    # An excluded file is never vectored, so it must never be dropped either.
    _enable_tvos(monkeypatch)
    own = "addon_data/script.ezmaintenanceplusplus/settings.xml"
    _write(tmp_path, own, b"<settings/>")

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert (tmp_path / own).exists()


def test_drop_posix_flag_disables_tvos_drop(nsud, tmp_path, monkeypatch):
    _enable_tvos(monkeypatch)
    _write(tmp_path, "guisettings.xml", b"<settings/>")

    nsud.mod.rewrite_userdata_xml(str(tmp_path), drop_posix_on_tvos=False)

    assert (tmp_path / "guisettings.xml").exists()


def test_non_tvos_getcondvis_false_never_drops(nsud, tmp_path, monkeypatch):
    # Real Fire TV / Android / desktop: the condition EXISTS and returns False (NOT an
    # exception). This exercises the actual production guard on the dangerous platforms,
    # where special://home/userdata IS the POSIX file and a drop would destroy userdata.
    monkeypatch.setattr(
        sys.modules["xbmc"], "getCondVisibility", lambda cond: False, raising=False
    )
    _write(tmp_path, "guisettings.xml", b"<settings/>")

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert (tmp_path / "guisettings.xml").read_bytes() == b"<settings/>"


def test_is_tvos_queries_exact_condition_string(nsud, monkeypatch):
    # Pin the condition string so a typo (which real Kodi would answer False = fix silently
    # no-ops) is caught here instead of shipping.
    seen = []
    monkeypatch.setattr(
        sys.modules["xbmc"],
        "getCondVisibility",
        lambda cond: seen.append(cond) or True,
        raising=False,
    )
    assert nsud.mod._is_tvos() is True
    assert seen == ["System.Platform.TVOS"]


def test_tvos_keeps_posix_when_readback_mismatch(nsud, tmp_path, monkeypatch):
    # write() reports success, but the durable store does NOT hold the bytes on read-back
    # (models the tvOS storage budget silently evicting/truncating a key). The POSIX copy
    # must be kept — deleting it would lose the only good copy.
    _enable_tvos(monkeypatch)
    _write(tmp_path, "guisettings.xml", b"<settings/>")
    nsud.state["evict_on_readback"] = {"special://home/userdata/guisettings.xml"}

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert (tmp_path / "guisettings.xml").read_bytes() == b"<settings/>"


def test_mixed_success_and_failure_drops_only_confirmed(nsud, tmp_path, monkeypatch):
    # In one call: one file vectors cleanly (drop), another's write fails (keep).
    _enable_tvos(monkeypatch)
    _write(tmp_path, "guisettings.xml", b"<a/>")
    _write(tmp_path, "sources.xml", b"<b/>")
    nsud.state["fail_paths"] = {"special://home/userdata/sources.xml"}

    nsud.mod.rewrite_userdata_xml(str(tmp_path))

    assert not (tmp_path / "guisettings.xml").exists()  # confirmed vector -> dropped
    assert (tmp_path / "sources.xml").read_bytes() == b"<b/>"  # failed write -> kept


# --------------------------------------------------------------------------- #
# Wiring: the generic re-write runs AFTER apply_guisettings/UpdateLocalAddons and BEFORE the
# restart prompt. NO IPTV staging / auto-enable intent is wired anywhere (all removed).
# --------------------------------------------------------------------------- #
def test_wiz_calls_nsud_after_updatelocaladdons_before_restart():
    wiz_src = (ADDON_MODULES / "wiz.py").read_text(encoding="utf-8")
    i_apply = wiz_src.index("apply_guisettings(")
    i_update = wiz_src.index('executebuiltin("UpdateLocalAddons")')
    i_rewrite = wiz_src.index("nsud.rewrite_userdata_xml(")
    i_marker = wiz_src.index("mark_buffer_prompt_pending()")
    assert i_apply < i_update < i_rewrite < i_marker


def test_wiz_restore_has_no_iptv_or_delete_behavior():
    # By construction, the whole IPTV subsystem AND the boot-delete sweep are gone from wiz.
    wiz_src = (ADDON_MODULES / "wiz.py").read_text(encoding="utf-8")
    for gone in (
        "stage_iptv_disabled",
        "mark_iptv_autoenable_pending",
        "set_pvr_enabled",
        "pvr_is_enabled",
        "def sweep_home_root_pollution",
        "_USERDATA_STRAY_NAMES",
    ):
        assert gone not in wiz_src, "wiz.py must no longer contain %r" % gone
