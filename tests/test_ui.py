"""
Contract tests for ui.py - the uniform dialog / progress / copy / restart library.

These pin the behaviour every backup/restore/compress/extract/upload/download path will
inherit once migrated: an honest gauge with the divide-by-zero guard baked in, a memoised
cancel, an atomic chunked copy that cleans its partial and only falls back on a transient
failure (never on cancel), one heading on every dialog, and one restart mechanism.
"""

import pytest

HEADING = "EZ Maintenance++"
MB = 1024 * 1024


# ============================================================== _pct guard ===
@pytest.mark.parametrize(
    "done,total,expect",
    [
        (0, 0, 0),  # empty -> indeterminate, NOT ZeroDivision
        (50, 0, 0),  # any progress with no total -> 0
        (1, 3, 33),  # normal
        (50, 100, 50),
        (100, 100, 100),
        (150, 100, 100),  # clamp high
        (-5, 100, 0),  # clamp low
        (5, None, 0),  # None total -> 0
    ],
)
def test_pct_guard_and_clamp(ui_mod, done, total, expect):
    assert ui_mod.ui._pct(done, total) == expect


# ============================================================== Progress ===
def test_progress_seeds_zero_with_heading(ui_mod):
    ui = ui_mod.ui
    assert ui.HEADING == HEADING
    ui.Progress("Downloading from Dropbox")
    create = ui_mod.DialogProgress.create_calls[0][0]
    assert create[0] == HEADING
    assert create[1] == "Downloading from Dropbox"
    # first update is a 0% seed (no phantom 100% flash before the first real report)
    first = ui_mod.DialogProgress.update_calls[0][0]
    assert first[0] == 0


def test_progress_bytes_formats_mb_and_percent(ui_mod):
    ui = ui_mod.ui
    p = ui.Progress("Downloading")
    p.bytes(3 * MB // 2, 3 * MB)  # 1.5 MB / 3.0 MB -> 50%
    pct, msg = ui_mod.DialogProgress.update_calls[-1][0]
    assert pct == 50
    assert "1.5 MB / 3.0 MB" in msg
    assert "Downloading" in msg


def test_progress_bytes_zero_total_is_indeterminate(ui_mod):
    ui = ui_mod.ui
    p = ui.Progress("Working")
    p.bytes(0, 0)  # must not raise, must not compute a percent
    pct, msg = ui_mod.DialogProgress.update_calls[-1][0]
    assert pct == 0
    assert "0.0 MB" in msg


def test_progress_items_zero_total_is_indeterminate(ui_mod):
    ui = ui_mod.ui
    p = ui.Progress("Extracting")
    p.items(0, 0)  # empty archive: no ZeroDivision, no percent
    pct, msg = ui_mod.DialogProgress.update_calls[-1][0]
    assert pct == 0
    assert "0" in msg


def test_progress_items_counts(ui_mod):
    ui = ui_mod.ui
    p = ui.Progress("Extracting")
    p.items(3, 12)
    pct, msg = ui_mod.DialogProgress.update_calls[-1][0]
    assert pct == 25
    assert "3 / 12" in msg


def test_cancel_is_memoised_and_idempotent(ui_mod):
    ui = ui_mod.ui
    ui_mod.DialogProgress.cancel_after = 1  # first poll cancels
    p = ui.Progress("x")
    assert p.cancelled() is True
    # even if the backend flips iscanceled() back off, cancelled() stays True and does
    # not poll again
    ui_mod.DialogProgress.cancel_after = None
    ui_mod.DialogProgress._polls = 0
    assert p.cancelled() is True
    assert ui_mod.DialogProgress._polls == 0  # not re-polled after latch


def test_dropbox_callback_true_then_cancel(ui_mod):
    ui = ui_mod.ui
    # not cancelled: callback reports progress and returns True
    p = ui.Progress("Up")
    cb = p.as_dropbox_callback()
    assert cb(10, 100) is True
    assert ui_mod.DialogProgress.update_calls  # progress was reported
    # cancelled: same adapter returns False so upload/download aborts
    ui_mod.DialogProgress.cancel_after = 1
    p2 = ui.Progress("Up")
    cb2 = p2.as_dropbox_callback()
    assert cb2(10, 100) is False


def test_progress_context_closes_on_exception(ui_mod):
    ui = ui_mod.ui
    with pytest.raises(ValueError):
        with ui.Progress("x"):
            raise ValueError("boom")
    # the dialog was still closed (ui.py must never leak a stuck progress dialog)
    assert ui_mod.DialogProgress.close_calls == 1


def test_progress_close_is_idempotent(ui_mod):
    ui = ui_mod.ui
    p = ui.Progress("x")
    p.close()
    p.close()
    assert ui_mod.DialogProgress.close_calls == 1


# ============================================================== copy ===
def _seed(ui_mod, path, data):
    ui_mod.FakeFile._payloads[path] = data


def test_copy_ok_atomic_via_rename_no_fallback(ui_mod):
    ui = ui_mod.ui
    src, dst = "src://a.zip", "dst://a.zip"
    tmp = dst + ui.COPY_TMP_SUFFIX
    _seed(ui_mod, src, b"HELLO" * 1000)  # 5000 bytes, one chunk
    assert ui.copy_with_progress(src, dst) == ui.COPY_OK
    # destination is the full file, written via the sidecar + rename (atomic)
    assert ui_mod.FakeFile._payloads.get(dst) == b"HELLO" * 1000
    assert ui_mod.xbmcvfs._renames == [(tmp, dst)]
    # no partial left, and the opaque fallback was NOT used on the happy path
    assert tmp not in ui_mod.FakeFile._payloads
    assert ui_mod.xbmcvfs._copies == []


def test_copy_reports_progress_and_reaches_100(ui_mod):
    ui = ui_mod.ui
    src, dst = "src://big.zip", "dst://big.zip"
    _seed(ui_mod, src, b"Z" * (3 * MB))  # three 1 MiB chunks
    p = ui.Progress("Copying to network share")
    assert ui.copy_with_progress(src, dst, p) == ui.COPY_OK
    pcts = [c[0][0] for c in ui_mod.DialogProgress.update_calls]
    assert pcts[-1] == 100
    assert pcts == sorted(pcts)  # monotonic


def test_copy_cancel_deletes_partial_and_never_falls_back(ui_mod):
    ui = ui_mod.ui
    src, dst = "src://c.zip", "dst://c.zip"
    tmp = dst + ui.COPY_TMP_SUFFIX
    _seed(ui_mod, src, b"Z" * (3 * MB))
    ui_mod.DialogProgress.cancel_after = 2  # cancel after the first chunk is written
    p = ui.Progress("Copying")
    assert ui.copy_with_progress(src, dst, p) == ui.COPY_CANCELLED
    assert tmp not in ui_mod.FakeFile._payloads  # partial cleaned
    assert dst not in ui_mod.FakeFile._payloads  # destination never created
    assert ui_mod.xbmcvfs._copies == []  # cancel must NEVER fall back


def test_copy_read_error_falls_back_to_vfs_copy(ui_mod):
    ui = ui_mod.ui
    base = ui_mod.FakeFile

    class _Raises(base):
        def readBytes(self, n=None):
            raise OSError("stream reset")

    ui_mod.xbmcvfs.File = _Raises
    src, dst = "src://d.zip", "dst://d.zip"
    tmp = dst + ui.COPY_TMP_SUFFIX
    _seed(ui_mod, src, b"DATA" * 100)
    # transient read failure -> clean partial -> opaque xbmcvfs.copy succeeds
    assert ui.copy_with_progress(src, dst) == ui.COPY_OK
    assert ui_mod.xbmcvfs._copies == [(src, dst)]
    assert tmp not in ui_mod.FakeFile._payloads
    assert ui_mod.FakeFile._payloads.get(dst) == b"DATA" * 100


def test_copy_write_false_raises_and_does_not_fall_back(ui_mod):
    ui = ui_mod.ui
    src, dst = "src://e.zip", "dst://e.zip"
    tmp = dst + ui.COPY_TMP_SUFFIX
    _seed(ui_mod, src, b"PAYLOAD" * 100)
    ui_mod.FakeFile._write_fails.add(tmp)  # write() returns False -> definitive failure
    with pytest.raises(ui.VfsCopyError):
        ui.copy_with_progress(src, dst)
    assert tmp not in ui_mod.FakeFile._payloads
    assert dst not in ui_mod.FakeFile._payloads
    assert ui_mod.xbmcvfs._copies == []  # a refused write must NOT silently fall back


def test_copy_fallback_false_raises_vfs_copy_error(ui_mod):
    ui = ui_mod.ui
    base = ui_mod.FakeFile

    class _Raises(base):
        def readBytes(self, n=None):
            raise OSError("boom")

    ui_mod.xbmcvfs.File = _Raises
    ui_mod.xbmcvfs._copy_result = False  # fallback also fails
    src, dst = "src://f.zip", "dst://f.zip"
    tmp = dst + ui.COPY_TMP_SUFFIX
    _seed(ui_mod, src, b"x" * 50)
    with pytest.raises(ui.VfsCopyError):
        ui.copy_with_progress(src, dst)
    assert ui_mod.xbmcvfs._copies == [(src, dst)]  # fallback was attempted
    assert tmp not in ui_mod.FakeFile._payloads


def test_copy_size_mismatch_fails_and_never_finalizes(ui_mod):
    ui = ui_mod.ui
    src, dst = "src://g.zip", "dst://g.zip"
    tmp = dst + ui.COPY_TMP_SUFFIX
    _seed(ui_mod, src, b"12345")  # 5 real bytes
    ui_mod.FakeStat._overrides[src] = 999  # but Stat claims 999 -> short copy
    with pytest.raises(ui.VfsCopyError):
        ui.copy_with_progress(src, dst)
    assert tmp not in ui_mod.FakeFile._payloads
    assert dst not in ui_mod.FakeFile._payloads  # never renamed into place
    assert ui_mod.xbmcvfs._renames == []


# ============================================================== dialogs ===
def test_confirm_uses_heading(ui_mod):
    ui = ui_mod.ui
    ui_mod.Dialog.yesno_result = True
    assert ui.confirm("Proceed?") is True
    assert ui_mod.Dialog.yesno_calls[-1][0][0] == HEADING


def test_confirm_wipe_labels_and_heading(ui_mod):
    ui = ui_mod.ui
    ui_mod.Dialog.yesno_result = False
    assert ui.confirm_wipe("This erases everything.") is False
    a, k = ui_mod.Dialog.yesno_calls[-1]
    assert a[0] == HEADING
    assert k.get("yeslabel") == "Wipe"


def test_error_and_done_use_heading(ui_mod):
    ui = ui_mod.ui
    ui.error("It broke.")
    assert ui_mod.Dialog.ok_calls[-1][0][0] == HEADING
    ui.done("All good.")
    assert ui_mod.Dialog.ok_calls[-1][0][0] == HEADING


def test_notify_uses_dialog_notification_not_executebuiltin(ui_mod):
    ui = ui_mod.ui
    ui.notify("Backup complete")
    assert ui_mod.Dialog.notification_calls[-1][0][0] == HEADING
    # the whole point: NOT executebuiltin('Notification(Maintenance,...)')
    assert ui_mod.xbmc._executed == []


def test_choose_uses_heading(ui_mod):
    ui = ui_mod.ui
    ui_mod.Dialog.select_result = 2
    assert ui.choose(["a", "b", "c"]) == 2
    assert ui_mod.Dialog.select_calls[-1][0][0] == HEADING


# ============================================================== restart ===
def test_ask_restart_yes_quits(ui_mod):
    ui = ui_mod.ui
    ui_mod.Dialog.yesno_result = True
    assert ui.ask_restart() is True
    assert "Quit" in ui_mod.xbmc._executed


def test_ask_restart_no_does_not_quit(ui_mod):
    ui = ui_mod.ui
    ui_mod.Dialog.yesno_result = False
    assert ui.ask_restart() is False
    assert "Quit" not in ui_mod.xbmc._executed


def test_restart_uses_quit(ui_mod):
    ui = ui_mod.ui
    ui.restart()
    assert ui_mod.xbmc._executed == ["Quit"]
