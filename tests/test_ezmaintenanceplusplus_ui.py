"""Coverage for script.ezmaintenanceplusplus's ui.py copy fallback/local-read fix.

Two real device logs on the same tvOS Apple TV both showed a 142 MB backup
copy failing with "size mismatch (0 != total)": copied=0, total=<the correct
size>, actual=0, on every retry. The first log proved this was a read-side
failure, not a destination-write-timing race. A second log - taken AFTER
shipping a fix that fell back to the opaque xbmcvfs.copy() on a broken chunked
read - showed the SAME failure: xbmcvfs.copy() ALSO could not read the source.
That is decisive: reading this local, just-built temp zip fails through EVERY
Kodi VFS mechanism (File.readBytes() and the native xbmcvfs.copy()), even
though xbmcvfs.Stat() on it always correctly reports the real size. This
add-on's own CreateZip() writes that file with plain zipfile/open(), and
wiz.py's own staged-zip validation already reads it back with plain
os.path.getsize()/zipfile.is_zipfile() - so plain Python I/O is proven to work
for this exact class of path on this exact device; only Kodi's VFS read of it
is broken. The fix: a local source path (no "://") is now read with plain
Python open(), never xbmcvfs, for both the primary chunked copy and its
fallback retry.

This file builds a minimal fake xbmc/xbmcaddon/xbmcgui/xbmcvfs (ui.py's own
docstring: "Imports ONLY xbmc / xbmcaddon / xbmcgui / xbmcvfs, so it is fully
unit-testable off-device") to exercise the real _copy_once/copy_with_progress
code, not a reimplementation of it. Fake VFS paths use an "nfs://" prefix so
`_open_reader` routes them through the fake xbmcvfs.File instead of a real
local `open()` - only the dedicated local-read test uses a real file (via
tmp_path), since that is the one behavior that must NOT go through the fake.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
ADDON_ROOT = REPO_ROOT / "script.ezmaintenanceplusplus"


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    sleeps = []
    logs = []
    fake_xbmc = types.SimpleNamespace(
        log=lambda msg, level=None: logs.append(msg),
        sleep=lambda ms: sleeps.append(ms),
        getCondVisibility=lambda cond: False,  # default: not-Android (desktop)
        LOGERROR=1,
        LOGWARNING=2,
        LOGINFO=3,
        LOGDEBUG=4,
        LOGFATAL=0,
        LOGNONE=5,
    )
    fake_xbmcaddon = types.SimpleNamespace(
        Addon=lambda: types.SimpleNamespace(
            getAddonInfo=lambda key: "EZ Maintenance++" if key == "name" else ""
        )
    )
    input_queue = []
    notifications = []

    fake_xbmcgui = types.SimpleNamespace(
        Dialog=lambda: types.SimpleNamespace(
            yesno=lambda *a, **k: False,
            ok=lambda *a, **k: None,
            input=lambda prompt, default="", type=0: input_queue.pop(0),
            notification=lambda heading, message, *a, **k: notifications.append(
                message
            ),
        ),
        DialogProgress=lambda: types.SimpleNamespace(
            create=lambda *a, **k: None,
            update=lambda *a, **k: None,
            close=lambda: None,
            iscanceled=lambda: False,
        ),
        INPUT_ALPHANUM=0,
        INPUT_NUMERIC=1,
    )

    monkeypatch.setitem(sys.modules, "xbmc", fake_xbmc)
    monkeypatch.setitem(sys.modules, "xbmcaddon", fake_xbmcaddon)
    monkeypatch.setitem(sys.modules, "xbmcgui", fake_xbmcgui)
    # A bare placeholder so `import xbmcvfs` succeeds; each test then replaces
    # it (both in sys.modules and on the imported ui module directly) with a
    # File/Stat/etc. fake tailored to what it's exercising.
    monkeypatch.setitem(sys.modules, "xbmcvfs", types.SimpleNamespace())

    mod = importlib.import_module("resources.lib.modules.ui")
    mod._TEST_SLEEPS = sleeps
    mod._TEST_LOGS = logs
    mod._TEST_INPUT_QUEUE = input_queue
    mod._TEST_NOTIFICATIONS = notifications
    return mod


class _FakeFile:
    """A minimal xbmcvfs.File stand-in backed by an in-memory byte buffer.

    `broken_read_paths` simulates the live tvOS bug: File(path, "r").readBytes()
    returns empty on every call for a path, even though the store (and
    therefore Stat()) holds the real, correctly-sized data. `raise_read_paths`
    simulates a genuinely raised read/stream exception on EVERY attempt (a
    distinct code path from an empty-but-non-raising read).
    `raise_read_once_paths` raises only on the first File() constructed for
    that path (a one-time transient error, e.g. a stale NFS connection) - a
    later attempt (the fallback's fresh File()) reads normally, proving
    recovery. `write_fails_paths` simulates a refused write (share full or
    dropped) - a definitive failure with no retry.
    """

    def __init__(
        self,
        data_store,
        path,
        mode,
        broken_read_paths=frozenset(),
        raise_read_paths=frozenset(),
        raise_read_once_paths=None,
        write_fails_paths=frozenset(),
    ):
        self._store = data_store
        self._path = path
        self._mode = mode
        self._pos = 0
        self._broken = path in broken_read_paths
        self._raises = path in raise_read_paths
        self._write_fails = path in write_fails_paths
        self._raise_once_counts = raise_read_once_paths
        self._raises_this_open = False
        if self._raise_once_counts is not None and path in self._raise_once_counts:
            n = self._raise_once_counts[path]
            if n > 0:
                self._raise_once_counts[path] = n - 1
                self._raises_this_open = True

    def readBytes(self, n):
        if self._raises or self._raises_this_open:
            raise OSError("simulated read error on %s" % self._path)
        if self._broken:
            return b""
        data = self._store.get(self._path, b"")
        chunk = data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def write(self, chunk):
        if self._write_fails:
            return False
        self._store[self._path] = self._store.get(self._path, b"") + bytes(chunk)
        return True

    def close(self):
        pass


def _install_fake_vfs(
    monkeypatch,
    ui_mod,
    *,
    store,
    settled_sizes_by_path=None,
    broken_read_paths=None,
    raise_read_paths=None,
    raise_read_once_paths=None,
    write_fails_paths=None,
):
    """A fake xbmcvfs where Stat().st_size() can return a caller-controlled,
    per-call sequence of values (to simulate a settle race or a permanently
    unreliable stat), independent of what was ACTUALLY written to the
    in-memory store."""
    settled_sizes_by_path = settled_sizes_by_path or {}
    broken_read_paths = broken_read_paths or frozenset()
    raise_read_paths = raise_read_paths or frozenset()
    raise_read_once_paths = dict(raise_read_once_paths or {})
    write_fails_paths = write_fails_paths or frozenset()
    call_counts = {}

    class _FakeStat:
        def __init__(self, path):
            self._path = path

        def st_size(self):
            if self._path in settled_sizes_by_path:
                seq = settled_sizes_by_path[self._path]
                i = call_counts.get(self._path, 0)
                call_counts[self._path] = i + 1
                return seq[min(i, len(seq) - 1)]
            return len(store.get(self._path, b""))

    fake_xbmcvfs = types.SimpleNamespace(
        File=lambda path, mode="r": _FakeFile(
            store,
            path,
            mode,
            broken_read_paths=broken_read_paths,
            raise_read_paths=raise_read_paths,
            raise_read_once_paths=raise_read_once_paths,
            write_fails_paths=write_fails_paths,
        ),
        Stat=_FakeStat,
        exists=lambda p: p in store,
        delete=lambda p: store.pop(p, None) or True,
        rename=lambda a, b: (
            (store.__setitem__(b, store.pop(a)), True)[1] if a in store else False
        ),
        copy=lambda a, b: (store.__setitem__(b, store.get(a, b"")), True)[1],
    )
    monkeypatch.setitem(sys.modules, "xbmcvfs", fake_xbmcvfs)
    monkeypatch.setattr(ui_mod, "xbmcvfs", fake_xbmcvfs)
    return call_counts


def test_copy_once_settles_after_transient_zero_size(ui, monkeypatch):
    """The live-observed shape (the FIRST real device log, before the read-side
    root cause was found): the write completes fully (no refused write, no
    exception), but the FIRST size check reads stale 0 - it must settle to the
    correct size on a later poll instead of declaring a hard failure."""
    store = {"nfs://src": b"x" * 1000}
    # First Stat call after the write reads stale 0; the second reads correct.
    _install_fake_vfs(
        monkeypatch,
        ui,
        store=store,
        settled_sizes_by_path={"nfs://dst.ezmpart": [0, 1000]},
    )
    result = ui.copy_with_progress("nfs://src", "nfs://dst")
    assert result == ui.COPY_OK
    assert store["nfs://dst"] == b"x" * 1000
    # Settled via the cheap poll, NOT the expensive whole-copy retry.
    assert len(ui._TEST_SLEEPS) == 1
    assert ui._TEST_SLEEPS[0] == ui.SIZE_SETTLE_DELAY_MS


def test_copy_once_falls_back_after_settle_attempts_exhausted(ui, monkeypatch):
    """A destination size that never settles must not spin forever, and must
    not just raise either - it must fall back to a second, clean copy attempt
    and succeed, since the destination store DOES hold the right bytes (only
    the Stat() reads in this scenario are wrong, not the underlying data)."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(
        monkeypatch, ui, store=store, settled_sizes_by_path={"nfs://dst.ezmpart": [0]}
    )
    result = ui._copy_once("nfs://src", "nfs://dst")
    assert result == ui.COPY_OK
    assert len(ui._TEST_SLEEPS) == ui.SIZE_SETTLE_ATTEMPTS - 1
    assert store["nfs://dst"] == b"x" * 1000  # shipped via the fallback retry
    assert "nfs://dst.ezmpart" not in store  # partial cleaned up


def test_copy_once_reads_a_local_source_without_going_through_vfs(
    ui, monkeypatch, tmp_path
):
    """The actual, live-confirmed root cause and its fix: reading a just-built
    LOCAL temp zip via Kodi's VFS (File.readBytes() OR the opaque
    xbmcvfs.copy()) came back completely empty on a real Apple TV on every
    attempt, despite xbmcvfs.Stat() correctly reporting its size - confirmed
    twice, once for each recovery mechanism tried. A local source (no "://")
    must now be read with plain Python open(), never xbmcvfs, so this uses a
    REAL file (not the fake store) for src. To prove the VFS read is never
    even attempted, xbmcvfs.File is deliberately broken for this exact path
    too - if the code regressed to using it, this test would fail with a size
    mismatch instead of succeeding on the first try."""
    data = b"x" * 1000
    src = tmp_path / "kodi_backup_202607041122.zip"
    src.write_bytes(data)
    src_path = str(src)
    # Stat() still goes through xbmcvfs (the real Stat() has always correctly
    # reported the size in every live log) - only the store copy for the size
    # check; the real read bypasses this store entirely via plain open().
    store = {src_path: data}
    _install_fake_vfs(
        monkeypatch, ui, store=store, broken_read_paths=frozenset({src_path})
    )
    result = ui.copy_with_progress(src_path, "nfs://dst")
    assert result == ui.COPY_OK
    assert store["nfs://dst"] == data
    # Succeeded on the very first attempt - no settle poll, no fallback needed.
    assert ui._TEST_SLEEPS == []


def test_copy_with_progress_raises_when_both_attempts_fail(ui, monkeypatch):
    """A source whose VFS read is broken on BOTH the primary chunked attempt
    and the fallback's retry (a remote share that genuinely can't be read)
    must still raise VfsCopyError, not silently report success. Exercises
    copy_with_progress (not just _copy_once) so the outer COPY_RETRY_ATTEMPTS
    exhaustion path is proven too, not just one attempt."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(
        monkeypatch, ui, store=store, broken_read_paths=frozenset({"nfs://src"})
    )
    with pytest.raises(ui.VfsCopyError):
        ui.copy_with_progress("nfs://src", "nfs://dst")
    assert "nfs://dst" not in store


def test_fallback_copy_rejects_a_destination_that_never_settles(ui, monkeypatch):
    """A successful fallback copy is not proof the bytes actually landed - the
    same destination-stat unreliability the primary chunked path settles for
    could affect the fallback's own destination check too. A destination whose
    Stat() never matches must still raise VfsCopyError and clean up, not let
    backup() rotate out a good backup for a file whose true size is unknown."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(
        monkeypatch,
        ui,
        store=store,
        settled_sizes_by_path={"nfs://dst.ezmpart": [0], "nfs://dst": [0]},
    )
    with pytest.raises(ui.VfsCopyError, match="fallback copy size mismatch"):
        ui._copy_once("nfs://src", "nfs://dst")
    assert "nfs://dst" not in store  # the unverified file was cleaned up


def test_copy_once_no_settle_needed_is_fast(ui, monkeypatch):
    """The common case (size correct immediately) must not sleep at all."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(monkeypatch, ui, store=store)
    result = ui.copy_with_progress("nfs://src", "nfs://dst")
    assert result == ui.COPY_OK
    assert ui._TEST_SLEEPS == []


def test_copy_once_size_mismatch_logs_diagnostic_with_copied_count(ui, monkeypatch):
    """A never-settling mismatch must log `copied` (bytes actually read from
    src and written to tmp) alongside total/actual before falling back - the
    one fact that distinguished a read-side failure (copied==0) from a
    destination-stat failure (copied==total but actual!=total) during the live
    investigation, before a second real device log settled it decisively."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(
        monkeypatch, ui, store=store, settled_sizes_by_path={"nfs://dst.ezmpart": [0]}
    )
    result = ui._copy_once("nfs://src", "nfs://dst")
    assert result == ui.COPY_OK
    assert len(ui._TEST_LOGS) == 1
    msg = ui._TEST_LOGS[0]
    assert "copied=1000" in msg  # the full 1000 bytes WERE read+written here
    assert "total=1000" in msg
    assert "actual=0" in msg


def test_copy_once_cancel_returns_cancelled_without_fallback(ui, monkeypatch):
    """A user cancel mid-copy must return COPY_CANCELLED and clean up the
    partial - never fall back (that would defeat the cancel), never raise."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(monkeypatch, ui, store=store)
    progress = types.SimpleNamespace(cancelled=lambda: True, bytes=lambda *a, **k: None)
    result = ui._copy_once("nfs://src", "nfs://dst", progress=progress)
    assert result == ui.COPY_CANCELLED
    assert "nfs://dst" not in store
    assert "nfs://dst.ezmpart" not in store


def test_copy_once_write_refused_raises_without_fallback(ui, monkeypatch):
    """A refused write (share full/dropped) is a DEFINITIVE failure - it must
    raise immediately and clean up, never attempt the fallback (retrying a
    refused write against the same destination is pointless, per the
    _copy_once docstring's "do NOT fall back" rule)."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(
        monkeypatch,
        ui,
        store=store,
        write_fails_paths=frozenset({"nfs://dst.ezmpart"}),
    )
    with pytest.raises(ui.VfsCopyError, match="write refused"):
        ui._copy_once("nfs://src", "nfs://dst")
    assert "nfs://dst" not in store
    assert "nfs://dst.ezmpart" not in store


def test_copy_once_raised_read_exception_falls_back_and_succeeds(ui, monkeypatch):
    """A genuinely raised read/stream exception (as opposed to an empty,
    non-raising read) is a distinct code path from the size-mismatch branch -
    it skips straight to _fallback_copy via the generic exception handler, per
    the _copy_once docstring's "transient failure (a raised read/stream
    error)" guarantee. Here the primary attempt's read raises ONCE (a
    one-time transient error, e.g. the documented stale-NFS-connection case),
    but the fallback's own fresh File() open reads normally and must recover -
    proving this is a genuine retry path, not just a relabeled failure."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(
        monkeypatch,
        ui,
        store=store,
        raise_read_once_paths={"nfs://src": 1},
    )
    result = ui._copy_once("nfs://src", "nfs://dst")
    assert result == ui.COPY_OK
    assert store["nfs://dst"] == b"x" * 1000


def test_copy_with_progress_raised_read_exception_raises_after_fallback_fails(
    ui, monkeypatch
):
    """When the source's read raises an exception (not just returns empty) on
    EVERY attempt - both the primary chunked copy and the fallback's own
    _stream_copy retry - the whole thing must still raise VfsCopyError, not
    silently report success. This is the exception-raising counterpart to
    test_copy_with_progress_raises_when_both_attempts_fail (which uses an
    empty, non-raising read); the two exercise different code paths in
    _copy_once (the generic `except Exception:` handler vs. the size-mismatch
    branch) that both route to the same _fallback_copy recovery attempt."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(
        monkeypatch, ui, store=store, raise_read_paths=frozenset({"nfs://src"})
    )
    with pytest.raises(ui.VfsCopyError):
        ui.copy_with_progress("nfs://src", "nfs://dst")
    assert "nfs://dst" not in store


def test_fallback_copy_skips_size_check_when_total_unknown(ui, monkeypatch):
    """If the source's own size couldn't be determined (_vfs_size returned -1,
    e.g. a Stat() failure), the fallback has nothing to verify against and
    must still report success rather than block on an unknowable check."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(monkeypatch, ui, store=store)
    result = ui._fallback_copy("nfs://src", "nfs://dst", total=-1)
    assert result == ui.COPY_OK
    assert store["nfs://dst"] == b"x" * 1000


# --------------------------------------------------------------------------- #
# ask_int - the bounded-integer input helper (boundary behavior verified in
# isolation).
# --------------------------------------------------------------------------- #
def test_ask_int_accepts_inclusive_minimum(ui):
    ui._TEST_INPUT_QUEUE.append("25")
    assert ui.ask_int("Max Total Files Size (MB)", 200, 25, 500) == 25


def test_ask_int_accepts_inclusive_maximum(ui):
    ui._TEST_INPUT_QUEUE.append("500")
    assert ui.ask_int("Max Total Files Size (MB)", 200, 25, 500) == 500


def test_ask_int_rejects_just_below_minimum(ui):
    ui._TEST_INPUT_QUEUE.append("24")
    assert ui.ask_int("Max Total Files Size (MB)", 200, 25, 500) is None
    assert ui._TEST_NOTIFICATIONS == ["Enter a whole number from 25 to 500"]


def test_ask_int_rejects_just_above_maximum(ui):
    ui._TEST_INPUT_QUEUE.append("501")
    assert ui.ask_int("Max Total Files Size (MB)", 200, 25, 500) is None
    assert ui._TEST_NOTIFICATIONS == ["Enter a whole number from 25 to 500"]


def test_ask_int_rejects_non_numeric_input(ui):
    ui._TEST_INPUT_QUEUE.append("not a number")
    assert ui.ask_int("Max Total Files Size (MB)", 200, 25, 500) is None
    assert ui._TEST_NOTIFICATIONS == ["Enter a whole number from 25 to 500"]


def test_ask_int_cancel_returns_none_without_notifying(ui):
    # Kodi's numeric input dialog returns "" on cancel - silent, no rejection
    # toast (a cancel is not a mistake the user needs explained to them).
    ui._TEST_INPUT_QUEUE.append("")
    assert ui.ask_int("Max Total Files Size (MB)", 200, 25, 500) is None
    assert ui._TEST_NOTIFICATIONS == []


# --------------------------------------------------------------------------- #
# ask_restart - HONEST per platform. On Fire TV / Android Kodi cannot restart
# itself (RestartApp is desktop-only; restart() only Quits), so the prompt must
# say "close and reopen", not "restart".
# --------------------------------------------------------------------------- #
def _capture_yesno(ui):
    captured = {}

    def _yesno(heading, message, yeslabel="", nolabel="", **k):
        captured["heading"] = heading
        captured["message"] = message
        captured["yes"] = yeslabel
        return False  # user declines, so restart() is never called

    ui.xbmcgui.Dialog = lambda: types.SimpleNamespace(yesno=_yesno)
    return captured


def test_ask_restart_android_says_close_not_restart(ui):
    ui.xbmc.getCondVisibility = lambda cond: True  # Fire TV / Android
    cap = _capture_yesno(ui)
    assert ui.ask_restart("Restore Complete: 5 items, 3 settings applied.") is False
    assert "close" in cap["message"].lower()
    assert "restart now" not in cap["message"].lower()
    assert cap["yes"] == "Close now"
    assert "Restore Complete: 5 items" in cap["message"]  # status line preserved


def test_ask_restart_desktop_still_says_restart(ui):
    ui.xbmc.getCondVisibility = lambda cond: False  # desktop
    cap = _capture_yesno(ui)
    assert ui.ask_restart("Restore Complete: 5 items, 3 settings applied.") is False
    assert "restart now" in cap["message"].lower()
    assert cap["yes"] == "Restart"
