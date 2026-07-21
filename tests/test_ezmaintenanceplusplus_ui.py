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


def test_ask_restart_tvos_says_close_not_restart(ui):
    # Apple TV: System.Platform.TVOS true, Android false. Kodi cannot self-restart
    # on tvOS either (Quit only closes), so it must get the close-and-reopen wording,
    # not the desktop "Restart now?" - the Android-only check used to miss this.
    ui.xbmc.getCondVisibility = lambda cond: cond == "System.Platform.TVOS"
    cap = _capture_yesno(ui)
    assert ui.ask_restart("Cleared 2 recently played channels.") is False
    assert "close" in cap["message"].lower()
    assert "restart now" not in cap["message"].lower()
    assert cap["yes"] == "Close now"


def test_ask_restart_desktop_still_says_restart(ui):
    ui.xbmc.getCondVisibility = lambda cond: False  # desktop
    cap = _capture_yesno(ui)
    assert ui.ask_restart("Restore Complete: 5 items, 3 settings applied.") is False
    assert "restart now" in cap["message"].lower()
    assert cap["yes"] == "Restart"


# --------------------------------------------------------------------------- #
# Progress
#
# ui.py's module docstring claims Progress's guarantees are "enforced by
# test_ui.py". They were not: until now no test in this file (or any other)
# ever CONSTRUCTED a Progress. Every claim below was load-bearing prose only.
#
# The two that matter most are invisible to any functional test of a caller:
#   * the cancel latch. If it regressed to polling iscanceled() every time, a
#     backend that flips the flag back would silently un-cancel a running
#     restore mid-flight, and the copy loop would resume writing.
#   * as_dropbox_callback's `return not cancelled()` inversion. It is described
#     in-source as "the ONLY place that lives". Drop the `not` and every upload
#     aborts on its first chunk; add a second one and cancel stops working. Both
#     directions are silent - the callback's return value is consumed by the
#     dropbox SDK, never by this add-on.
# --------------------------------------------------------------------------- #
class _FakeDialogProgress:
    """A DialogProgress stand-in that RECORDS: every create/update/close, and
    every iscanceled() poll (the count is what the latch test asserts on)."""

    def __init__(self, cancel_sequence=None):
        # A sequence, not a constant: the whole point of the latch is that a
        # backend which answers True then False must not un-cancel the run.
        self._cancel_sequence = list(cancel_sequence or [])
        self.polls = 0
        self.creates = []
        self.updates = []
        self.closes = 0
        self.close_error = None
        self.iscanceled_error = None

    def create(self, heading, message=""):
        self.creates.append((heading, message))

    def update(self, pct, message=""):
        self.updates.append((pct, message))

    def iscanceled(self):
        self.polls += 1
        if self.iscanceled_error is not None:
            raise self.iscanceled_error
        if not self._cancel_sequence:
            return False
        return self._cancel_sequence.pop(0)

    def close(self):
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


def _progress(ui, monkeypatch, cancel_sequence=None, message="Backing up"):
    """A real ui.Progress driven by a recording fake dialog."""
    dp = _FakeDialogProgress(cancel_sequence)
    monkeypatch.setattr(ui.xbmcgui, "DialogProgress", lambda: dp)
    return ui.Progress(message), dp


# -- the cancel latch -------------------------------------------------------- #
def test_cancelled_latches_and_stops_polling_the_backend(ui, monkeypatch):
    """Once cancelled, cancelled() must answer True from memory and never poll
    iscanceled() again - the docstring's "some backends flip it back" claim.
    The poll COUNT is the assertion: a re-poll is what would let a backend
    un-cancel an in-flight restore."""
    p, dp = _progress(ui, monkeypatch, cancel_sequence=[True, False, False])
    assert p.cancelled() is True
    assert dp.polls == 1
    # The fake would answer False on polls 2 and 3. The latch must not ask.
    assert p.cancelled() is True
    assert p.cancelled() is True
    assert dp.polls == 1, "iscanceled() was re-polled after the cancel latched"


def test_cancelled_keeps_polling_until_the_user_actually_cancels(ui, monkeypatch):
    """The latch must not fire early: before a cancel, every call is a real poll,
    otherwise a cancel pressed mid-copy would never be seen."""
    p, dp = _progress(ui, monkeypatch, cancel_sequence=[False, False, True])
    assert p.cancelled() is False
    assert p.cancelled() is False
    assert dp.polls == 2
    assert p.cancelled() is True
    assert dp.polls == 3
    assert p.cancelled() is True
    assert dp.polls == 3  # latched from here on


def test_cancelled_treats_a_raising_backend_as_not_cancelled(ui, monkeypatch):
    """A dialog that throws must not be read as a cancel (that would abort a
    healthy backup) and must not propagate (ui.py is a single point of failure
    for every path in the add-on)."""
    p, dp = _progress(ui, monkeypatch)
    dp.iscanceled_error = RuntimeError("dialog is gone")
    assert p.cancelled() is False
    assert p.cancelled() is False
    # The poll COUNT is what proves "nothing latched": a swallowed error must
    # leave the latch untouched, so every call is still a real poll. Without
    # this, a Progress that latched on the exception would pass identically.
    assert dp.polls == 2


# -- as_dropbox_callback: the inversion -------------------------------------- #
def test_dropbox_callback_returns_true_while_running(ui, monkeypatch):
    """dropbox_remote's callback contract: return TRUE to continue. An inverted
    `not` here would abort every upload on its very first chunk."""
    p, _dp = _progress(ui, monkeypatch)
    cb = p.as_dropbox_callback()
    assert cb(0, 1000) is True
    assert cb(500, 1000) is True
    assert cb(1000, 1000) is True


def test_dropbox_callback_returns_false_once_cancelled(ui, monkeypatch):
    """...and FALSE to abort. A missing `not` here would make cancel a no-op:
    the user presses cancel, the dialog closes, and the upload keeps running."""
    p, _dp = _progress(ui, monkeypatch, cancel_sequence=[False, True])
    cb = p.as_dropbox_callback()
    assert cb(100, 1000) is True
    assert cb(200, 1000) is False
    assert cb(300, 1000) is False  # stays aborted (the latch)


def test_dropbox_callback_reports_progress_as_bytes(ui, monkeypatch):
    """The callback is also the progress report - it must render the MB body,
    not just answer the continue/abort question."""
    p, dp = _progress(ui, monkeypatch, message="Uploading to Dropbox")
    dp.updates.clear()
    p.as_dropbox_callback()(5 * 1024 * 1024, 10 * 1024 * 1024)
    pct, body = dp.updates[-1]
    assert pct == 50
    assert "5.0 MB / 10.0 MB" in body
    assert "Uploading to Dropbox" in body


# -- _pct: the single divide-by-zero guard for the whole add-on -------------- #
def test_pct_normal_range(ui):
    assert ui._pct(0, 100) == 0
    assert ui._pct(50, 100) == 50
    assert ui._pct(100, 100) == 100


def test_pct_indeterminate_totals_pin_at_zero_and_never_raise(ui):
    """An empty folder (total 0) is the exact shape that raised
    ZeroDivisionError before this guard existed. None and a negative total are
    the same "we do not know the size" case."""
    assert ui._pct(0, 0) == 0
    assert ui._pct(500, 0) == 0
    assert ui._pct(500, None) == 0
    assert ui._pct(500, -1) == 0


def test_pct_survives_non_numeric_inputs(ui):
    """A caller handing in a string/None `done` (a stat that failed) must degrade
    to 0, not crash a backup at the progress-reporting line."""
    assert ui._pct(None, 100) == 0
    assert ui._pct("nope", 100) == 0


def test_pct_clamps_out_of_range_values(ui):
    """Kodi's update() takes 0..100; a done>total overshoot (a size that grew
    mid-copy) must clamp rather than pass 140 to the dialog."""
    assert ui._pct(140, 100) == 100
    assert ui._pct(-40, 100) == 0


# -- _fmt_bytes: the indeterminate branch ------------------------------------ #
def test_fmt_bytes_shows_both_sides_when_the_total_is_known(ui):
    assert ui._fmt_bytes(1024 * 1024, 2 * 1024 * 1024) == "1.0 MB / 2.0 MB"


def test_fmt_bytes_shows_only_progress_when_the_total_is_unknown(ui):
    """total 0/None/negative = indeterminate: show what has been done, never
    "5.0 MB / 0.0 MB", which reads as a broken copy."""
    for total in (0, None, -1):
        assert ui._fmt_bytes(3 * 1024 * 1024, total) == "3.0 MB"


def test_fmt_bytes_treats_a_missing_done_as_zero(ui):
    assert ui._fmt_bytes(None, None) == "0.0 MB"


def test_bytes_reports_an_indeterminate_total_without_raising(ui, monkeypatch):
    """The guard end to end through the real Progress: the empty-folder case
    must render a live 0% bar, not raise."""
    p, dp = _progress(ui, monkeypatch, message="Compressing")
    dp.updates.clear()
    p.bytes(0, 0)
    pct, body = dp.updates[-1]
    assert pct == 0
    assert "0.0 MB" in body
    assert "/" not in body


def test_items_reports_an_indeterminate_total_without_raising(ui, monkeypatch):
    p, dp = _progress(ui, monkeypatch, message="Extracting")
    dp.updates.clear()
    p.items(7, 0)
    pct, body = dp.updates[-1]
    assert pct == 0
    assert "7" in body
    assert "/" not in body


def test_items_shows_both_sides_when_the_total_is_known(ui, monkeypatch):
    p, dp = _progress(ui, monkeypatch, message="Extracting")
    dp.updates.clear()
    p.items(3, 12)
    pct, body = dp.updates[-1]
    assert pct == 25
    assert "3 / 12" in body


# -- construction + lifecycle ------------------------------------------------ #
def test_progress_seeds_the_bar_at_zero_on_create(ui, monkeypatch):
    """ "Seed at 0 so total==0 paths still show a live, honest bar instead of a
    phantom 100% flash before the first real update"." """
    p, dp = _progress(ui, monkeypatch, message="Backing up")
    assert dp.creates == [("EZ Maintenance++", "Backing up")]
    assert dp.updates[0] == (0, "Backing up")
    p.close()


def test_progress_accepts_an_explicit_heading(ui, monkeypatch):
    dp = _FakeDialogProgress()
    monkeypatch.setattr(ui.xbmcgui, "DialogProgress", lambda: dp)
    ui.Progress("Working", heading="Something Else")
    assert dp.creates[0][0] == "Something Else"


def test_message_replaces_the_base_line(ui, monkeypatch):
    """The restore path moves a live dialog from "Downloading" to "Restoring"."""
    p, dp = _progress(ui, monkeypatch, message="Downloading")
    p.message("Restoring")
    assert dp.updates[-1] == (0, "Restoring")
    p.bytes(1024 * 1024, 2 * 1024 * 1024)
    assert "Restoring" in dp.updates[-1][1]
    assert "Downloading" not in dp.updates[-1][1]


def test_render_swallows_a_failing_update(ui, monkeypatch):
    """A dialog torn down by Kodi under a running job must not take the job with
    it - reporting progress is never worth failing a backup over.

    "Did not raise" is NOT sufficient evidence here: a bytes()/items()/message()
    that did nothing at all would also not raise. So the failing update RECORDS
    each attempt before throwing, and the assertions are on those attempts -
    the swallow is only proven if the render was genuinely tried and its
    computed pct/body reached the dialog on the way to failing."""
    p, dp = _progress(ui, monkeypatch, message="Backing up")
    attempts = []

    def _boom(pct, message=""):
        attempts.append((pct, message))
        raise RuntimeError("dialog is gone")

    dp.update = _boom

    p.bytes(1024 * 1024, 4 * 1024 * 1024)  # must not raise
    assert len(attempts) == 1, "bytes() never even attempted to render"
    assert attempts[-1][0] == 25
    assert "1.0 MB / 4.0 MB" in attempts[-1][1]

    p.items(3, 12)
    assert len(attempts) == 2, "items() never even attempted to render"
    assert attempts[-1][0] == 25
    assert "3 / 12" in attempts[-1][1]

    p.message("Restoring")
    assert len(attempts) == 3, "message() never even attempted to render"
    assert attempts[-1] == (0, "Restoring")

    # And the object is still usable afterwards - a swallowed render error must
    # not leave Progress in a state where the next call misbehaves.
    assert p.cancelled() is False


def test_close_is_idempotent(ui, monkeypatch):
    """close() runs from both the caller and __exit__ on every `with`. A second
    close must be a no-op, not a second close() on a dialog Kodi already tore
    down."""
    p, dp = _progress(ui, monkeypatch)
    p.close()
    p.close()
    p.close()
    assert dp.closes == 1


def test_close_swallows_a_failing_backend_close(ui, monkeypatch):
    p, dp = _progress(ui, monkeypatch)
    dp.close_error = RuntimeError("already gone")
    p.close()  # must not raise
    assert dp.closes == 1
    p.close()  # and stays latched closed
    assert dp.closes == 1


def test_context_manager_closes_on_a_clean_exit(ui, monkeypatch):
    dp = _FakeDialogProgress()
    monkeypatch.setattr(ui.xbmcgui, "DialogProgress", lambda: dp)
    with ui.Progress("Working") as p:
        assert isinstance(p, ui.Progress)
        p.bytes(1, 2)
    assert dp.closes == 1


def test_context_manager_closes_and_never_masks_the_real_exception(ui, monkeypatch):
    """__exit__ returns False, so the body's exception propagates - a failed
    backup must never be swallowed into a silent success by the progress
    dialog's cleanup."""
    dp = _FakeDialogProgress()
    monkeypatch.setattr(ui.xbmcgui, "DialogProgress", lambda: dp)
    with pytest.raises(ValueError, match="the real failure"):
        with ui.Progress("Working"):
            raise ValueError("the real failure")
    assert dp.closes == 1


def test_context_manager_close_error_does_not_mask_the_body_exception(ui, monkeypatch):
    """The exact case the __exit__ comment names: close() itself failing must not
    replace the body's exception with a cleanup error - the caller would then
    diagnose the wrong failure entirely."""
    dp = _FakeDialogProgress()
    monkeypatch.setattr(ui.xbmcgui, "DialogProgress", lambda: dp)
    p = ui.Progress("Working")

    def _boom():
        raise RuntimeError("close blew up")

    p.close = _boom
    with pytest.raises(ValueError, match="the real failure"):
        with p:
            raise ValueError("the real failure")


def test_context_manager_close_error_does_not_crash_a_clean_exit(ui, monkeypatch):
    """...and on a clean exit there is no exception to mask, so a failing close
    must simply be absorbed.

    "Did not raise" is NOT sufficient evidence: an __exit__ that never called
    close() at all would pass that too, and would be a resource leak (the
    dialog stays on screen over a finished job). The failing close RECORDS its
    call, so this proves __exit__ both CALLED close and SWALLOWED its error -
    two distinct facts, neither provable from the absence of an exception."""
    dp = _FakeDialogProgress()
    monkeypatch.setattr(ui.xbmcgui, "DialogProgress", lambda: dp)
    p = ui.Progress("Working")
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("close blew up")

    p.close = _boom
    with p:
        pass  # must not raise

    assert calls == [1], "__exit__ did not call close() on a clean exit"


# -- two unguarded gaps found while writing this file ------------------------ #
# Both are REPORTED, not fixed: they are behavior changes in a module every path
# in the add-on funnels through, and that is the owner's call, not a test's.
# They are encoded as xfail rather than as assertions on the current behavior so
# that fixing them turns these green instead of breaking the suite.


@pytest.mark.xfail(
    reason=(
        "items() is not guarded the way bytes() is. _pct and _fmt_bytes both "
        "defend against a None/non-numeric `done`, but items() formats its own "
        "body with an inline '%d / %d' and raises TypeError on the same input "
        "bytes() survives. A caller whose item count came back None (a listing "
        "that failed) crashes the whole operation at the progress-reporting "
        "line - reporting progress should never be able to fail a backup. Note "
        "items(1, None) is fine (the `total and total > 0` guard short-circuits); "
        "it is specifically a non-numeric `done` that is unguarded."
    ),
    # strict: this MUST go red the moment the bug is fixed, so the marker gets
    # removed with the fix instead of quietly outliving it. A non-strict xfail
    # here is inert in both directions - it cannot fail while the bug persists
    # (by design) and it cannot fail once it is fixed either, so it would never
    # signal anything to anyone.
    strict=True,
)
def test_items_should_survive_a_non_numeric_done_like_bytes_does(ui, monkeypatch):
    p, _dp = _progress(ui, monkeypatch)
    p.bytes(None, None)  # guarded today
    p.items(None, 5)  # raises TypeError today
    p.items("nope", 0)


@pytest.mark.xfail(
    reason=(
        "Progress.__init__ is the only method in the class that does NOT swallow "
        "a backend error. _render, cancelled and close all guard theirs, per the "
        "__exit__ comment's rule that 'ui.py is now a single point of failure' - "
        "but if xbmcgui.DialogProgress() or its create() raises, the constructor "
        "propagates and takes down the caller's entire operation before it starts. "
        "Related: the `if self._dp is not None` checks in cancelled()/close() are "
        "dead branches today, since __init__ either sets _dp or raises. Setting "
        "_dp = None on a failed create would make those checks live and make a "
        "progress-less run degrade instead of abort."
    ),
    # strict: this MUST go red the moment the bug is fixed, so the marker gets
    # removed with the fix instead of quietly outliving it. A non-strict xfail
    # here is inert in both directions - it cannot fail while the bug persists
    # (by design) and it cannot fail once it is fixed either, so it would never
    # signal anything to anyone.
    strict=True,
)
def test_progress_should_survive_a_dialog_that_cannot_be_created(ui, monkeypatch):
    class _BoomDialogProgress(_FakeDialogProgress):
        def create(self, heading, message=""):
            raise RuntimeError("no dialog available")

    monkeypatch.setattr(ui.xbmcgui, "DialogProgress", _BoomDialogProgress)
    p = ui.Progress("Backing up")  # raises today
    p.bytes(1, 2)
    assert p.cancelled() is False
    p.close()


def test_progress_drives_a_real_copy_cancel(ui, monkeypatch):
    """The integration the latch exists for: a REAL Progress (not the
    SimpleNamespace stub the other copy tests use) cancelling a real
    _copy_once. Proves the two halves fit - copy calls cancelled(), gets the
    latched answer, cleans its partial, and returns COPY_CANCELLED."""
    store = {"nfs://src": b"x" * 1000}
    _install_fake_vfs(monkeypatch, ui, store=store)
    p, dp = _progress(ui, monkeypatch, cancel_sequence=[True])
    with p:
        assert ui._copy_once("nfs://src", "nfs://dst", progress=p) == ui.COPY_CANCELLED
    assert "nfs://dst" not in store
    assert "nfs://dst.ezmpart" not in store
    assert dp.polls == 1


def test_ask_terminate_always_exits_and_offers_no_way_out(ui, monkeypatch):
    """Fresh Start's completion prompt must be a NOTICE, never a choice.

    It used to be a yesno with 'Shut down' / 'Later', and 'Later' left Kodi RUNNING on
    a freshly wiped tree - with every userdata/Database file deleted out from under the
    connections Kodi still holds open. The first write to any of them fails
    SQLITE_READONLY_DBMOVED and then storms SQLITE_MISUSE until the process aborts,
    which is exactly how the office Fire TV died on 2026-07-21 from a single unlinked
    database. The wipe also drops every cached texture, so the next artwork draw IS
    such a write. A Back/ESC dismissal was falsy too, so the most dangerous branch was
    also the accidental one.
    """
    exits = []
    monkeypatch.setattr(ui, "terminate", lambda: exits.append(True))
    used = {}

    class _D:
        def ok(self, heading, message):
            used["ok"] = message
            return True

        def yesno(self, *a, **k):  # pragma: no cover - must never be reached
            raise AssertionError(
                "ask_terminate must not ask - 'Later' left Kodi alive on a wiped tree"
            )

    monkeypatch.setattr(ui.xbmcgui, "Dialog", _D)
    ui.ask_terminate("Clean slate ready.")
    assert exits == [True]
    assert "close" in used["ok"].lower()


def test_ask_terminate_exits_even_if_the_dialog_blows_up(ui, monkeypatch):
    """A dialog failure must not become the 'Later' branch by another name.

    Post-wipe the skin's dialog XML is the likeliest thing to be missing, so this is
    the realistic failure - and leaving Kodi alive is precisely the unsurvivable state.
    """
    exits = []
    monkeypatch.setattr(ui, "terminate", lambda: exits.append(True))

    class _D:
        def ok(self, *a, **k):
            raise RuntimeError("no dialog XML after the wipe")

    monkeypatch.setattr(ui.xbmcgui, "Dialog", _D)
    ui.ask_terminate("Clean slate ready.")
    assert exits == [True]
