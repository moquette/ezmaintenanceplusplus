"""
EZ Maintenance++ : one uniform dialog / progress / copy / restart surface.

Every backup / restore / compress / extract / upload / download path in this add-on
used to hand-roll its own DialogProgress, its own cancel check, its own "Please wait"
string, its own restart mechanism, and its own network copy. The paths drifted: some
had a download gauge but no restore gauge, some cancelled cleanly and some bricked the
box, some named the add-on "Maintenance" and some "EZ Maintenance++". This module is the
single place all of that lives so every path behaves identically.

Design rules (enforced by test_ui.py):
  * Imports ONLY xbmc / xbmcaddon / xbmcgui / xbmcvfs, so it is fully unit-testable
    off-device.
  * `Progress` is ONE class with two body formatters (`bytes`, `items`) - never split.
    The divide-by-zero guard lives INSIDE it, so no caller can reintroduce the
    empty-folder ZeroDivisionError. `cancelled()` is memoised + idempotent.
  * `copy_with_progress` is atomic (temp + rename), checks `write()`'s bool return,
    validates size after close, cleans its partial on ANY non-success exit, and falls
    back to a second, unchunked copy attempt (itself size-verified, same settle-poll
    as the chunked path) on a transient failure OR a chunked copy whose size never
    settles, NEVER on cancel.
  * A LOCAL source path (no "://") is read with plain Python `open()`, never
    `xbmcvfs.File`/`xbmcvfs.copy` - live-confirmed on tvOS: reading a just-built
    local temp zip via EITHER `File(src, "r").readBytes()` OR the opaque
    `xbmcvfs.copy()` comes back completely empty on every call, despite
    `xbmcvfs.Stat()` correctly reporting its size, so the bug is not which VFS
    entry point is used - Kodi's VFS read of this sandboxed local file is broken
    full stop. This add-on's own `CreateZip()` writes that same file with plain
    `zipfile`/`open()`, and `wiz.py`'s staged-zip validation already reads it back
    with plain `os.path.getsize()`/`zipfile.is_zipfile()` - so plain Python I/O is
    proven to work for this exact path on this exact device; only Kodi's own VFS
    read of it is broken. A remote source (nfs://, smb://, ...) still goes through
    `xbmcvfs.File`, since plain Python can't read a VFS URL - the bug is specific
    to local sandboxed reads, not remote ones.
  * `copy_with_progress` retries a definitive `VfsCopyError` up to `COPY_RETRY_ATTEMPTS`
    times with a `COPY_RETRY_DELAY_SECS` pause between tries (never on cancel). Kodi's
    NFS/SMB client can leave a connection stale after its own idle-close reaper fires
    (observed: instant `VfsCopyError` on the very next write after "NFS is idle,
    closing the remaining connections"); an immediate in-process retry reuses the same
    broken connection object and fails identically, but a short pause gives Kodi's VFS
    layer room to open a genuinely fresh one, and the next attempt succeeds.
  * One heading (`HEADING`), one restart (`Quit`).

GPL v3 or later (see the other modules).
"""

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

# --------------------------------------------------------------------------- #
# Heading - the ONE name every dialog shows.
# --------------------------------------------------------------------------- #
_FALLBACK_HEADING = "EZ Maintenance++"


def _resolve_heading():
    try:
        name = xbmcaddon.Addon().getAddonInfo("name")
        return name or _FALLBACK_HEADING
    except Exception:
        return _FALLBACK_HEADING


HEADING = _resolve_heading()


# --------------------------------------------------------------------------- #
# copy_with_progress result + error
# --------------------------------------------------------------------------- #
COPY_OK = "ok"
COPY_FAILED = "failed"
COPY_CANCELLED = "cancelled"

# 1 MiB VFS copy unit. Big enough to keep SMB/NFS throughput up, small enough that a
# cancel is responsive and one write finishes promptly. Do not exceed 1 MiB.
COPY_CHUNK = 1024 * 1024
# Sidecar name for the in-progress copy so the final path only ever appears complete.
COPY_TMP_SUFFIX = ".ezmpart"

# A large NFS write can complete (every fdst.write() returns True, close() raises
# nothing) before the server has actually committed the bytes - an immediate stat
# right after close() can still read a stale, pre-write size (observed: a 142 MB
# copy reporting "0 != 142380074" instantly on every attempt, with no write ever
# refused). Poll the size a few times, briefly, before treating it as a genuine
# short write - cheap, and unlike COPY_RETRY_ATTEMPTS this does not re-read and
# re-send the whole file (which hits the exact same race again every time).
SIZE_SETTLE_ATTEMPTS = 4
SIZE_SETTLE_DELAY_MS = 500

# A VfsCopyError can mean the share is genuinely gone, OR that Kodi's own NFS/SMB
# idle-close reaper just tore down the connection a moment ago and the next open
# hit that stale state. 1 initial attempt + 2 retries, paused so Kodi's VFS layer
# has time to establish a fresh connection instead of reusing the broken one.
COPY_RETRY_ATTEMPTS = 3
COPY_RETRY_DELAY_SECS = 5


class VfsCopyError(Exception):
    """A VFS copy failed definitively (write refused, size mismatch, or fallback
    also failed). Callers that rotate backups MUST treat this as a failed ship and
    keep the previous good backup."""


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #
def _pct(done, total):
    """Percent 0..100. total <= 0 is indeterminate and pins at 0 (never a percent,
    never a ZeroDivisionError) - this is the single guard for the whole add-on."""
    try:
        if total is None or total <= 0:
            return 0
        p = int(done * 100 / total)
    except (TypeError, ZeroDivisionError):
        return 0
    return max(0, min(100, p))


def _mb(n):
    return (n or 0) / (1024.0 * 1024.0)


def _fmt_bytes(done, total):
    if total and total > 0:
        return "%.1f MB / %.1f MB" % (_mb(done), _mb(total))
    return "%.1f MB" % _mb(done)


class Progress(object):
    """A DialogProgress wrapper that formats byte / item progress uniformly and gives
    a memoised, idempotent cancel check.

    Use as a context manager so it always closes, even on an exception::

        with ui.Progress("Downloading from Dropbox") as p:
            dbx.download(name, progress=p.as_dropbox_callback())

    or drive it directly (p.bytes(...), p.items(...), p.cancelled(), p.close()).
    """

    def __init__(self, message="", heading=None):
        self._base = message or ""
        self._heading = heading if heading is not None else HEADING
        self._cancelled = False
        self._closed = False
        self._dp = xbmcgui.DialogProgress()
        self._dp.create(self._heading, self._base)
        # Seed at 0 so total==0 paths still show a live, honest bar instead of a
        # phantom 100% flash before the first real update.
        self._dp.update(0, self._base)

    # -- reporting ---------------------------------------------------------- #
    def _render(self, pct, detail, note):
        lines = [ln for ln in (self._base, detail, note) if ln]
        try:
            self._dp.update(pct, "\n".join(lines))
        except Exception:
            pass

    def bytes(self, done, total, note=""):
        """Report byte progress (done / total bytes). total <= 0 is indeterminate."""
        self._render(_pct(done, total), _fmt_bytes(done, total), note)

    def items(self, done, total, note=""):
        """Report item progress (done / total items). total <= 0 is indeterminate."""
        detail = "%d / %d" % (done, total) if total and total > 0 else "%d" % done
        self._render(_pct(done, total), detail, note)

    def message(self, text):
        """Replace the base line (e.g. moving from 'Downloading' to 'Restoring')."""
        self._base = text or ""
        self._render(0, "", "")

    # -- cancel ------------------------------------------------------------- #
    def cancelled(self):
        """True once the user has cancelled. Memoised: once True, always True, and the
        underlying iscanceled() is not polled again (some backends flip it back)."""
        if self._cancelled:
            return True
        try:
            if self._dp is not None and self._dp.iscanceled():
                self._cancelled = True
        except Exception:
            pass
        return self._cancelled

    def as_dropbox_callback(self):
        """Adapter for dropbox_remote.upload/download, whose callback is
        progress(sent, total) and cancels by returning False. This is the ONLY place
        that 'return not cancelled()' lives, so no call site hand-writes it."""

        def _cb(sent, total):
            self.bytes(sent, total)
            return not self.cancelled()

        return _cb

    # -- lifecycle ---------------------------------------------------------- #
    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._dp is not None:
                self._dp.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        # ui.py is now a single point of failure; never let a close error mask the
        # real exception (or crash a clean exit).
        try:
            self.close()
        except Exception:
            pass
        return False


# --------------------------------------------------------------------------- #
# copy_with_progress - the SMB/NFS/local copy every path shares
# --------------------------------------------------------------------------- #
def _vfs_size(path):
    try:
        return int(xbmcvfs.Stat(path).st_size())
    except Exception:
        return -1


def _vfs_delete(path):
    try:
        if xbmcvfs.exists(path):
            xbmcvfs.delete(path)
    except Exception:
        pass


class _LocalReader:
    """Adapts a plain Python file object to the xbmcvfs.File readBytes/close
    interface, so the copy loop can treat local and VFS sources uniformly."""

    def __init__(self, path):
        self._f = open(path, "rb")

    def readBytes(self, n):
        return self._f.read(n)

    def close(self):
        self._f.close()


def _open_reader(path):
    """Open `path` for chunked reading. See the module docstring: a local path
    (no "://") is read with plain Python `open()`, not `xbmcvfs.File`, because
    Kodi's own VFS read of a local sandboxed file has been live-confirmed broken
    on tvOS. A remote path (nfs://, smb://, ...) still has to go through
    xbmcvfs - plain Python cannot open a VFS URL."""
    if "://" in path:
        return xbmcvfs.File(path, "r")
    return _LocalReader(path)


def _stream_copy(src, dst):
    """Byte-for-byte copy src -> dst: `_open_reader` for the read side (so a
    local src bypasses the broken VFS read), `xbmcvfs.File` for the write side
    (dst is always a VFS path - local or remote). No progress/cancel; used only
    as the fallback's single clean retry."""
    fsrc = _open_reader(src)
    try:
        fdst = xbmcvfs.File(dst, "w")
        try:
            while True:
                chunk = fsrc.readBytes(COPY_CHUNK)
                if not chunk:
                    break
                if not fdst.write(chunk):
                    raise VfsCopyError("write refused on %s" % dst)
        finally:
            fdst.close()
    finally:
        fsrc.close()


def _vfs_finalize(tmp, dst):
    """Move the completed sidecar onto the final path. Prefer an atomic rename; fall
    back to copy+delete only if the backend cannot rename across the path."""
    _vfs_delete(dst)
    try:
        if xbmcvfs.rename(tmp, dst):
            return
    except Exception:
        pass
    if not xbmcvfs.copy(tmp, dst):
        _vfs_delete(tmp)
        raise VfsCopyError("could not finalize copy to %s" % dst)
    _vfs_delete(tmp)


def _fallback_copy(src, dst, total=-1):
    """A second, unchunked copy attempt via `_stream_copy` - no progress, no
    cancel, and (for a local src) no dependence on Kodi's own VFS read, which is
    what the chunked path's main loop uses and what live-confirmed to be broken
    on tvOS for a local sandboxed source (see the module docstring). This used
    to be a single opaque `xbmcvfs.copy()` call, which turned out to hit the
    exact same broken VFS read for a local src - a fresh, direct-Python-read
    attempt is what actually recovers it.

    A successful copy is not proof the bytes actually landed - the same
    server-side commit-delay race SIZE_SETTLE_ATTEMPTS exists for could affect
    this attempt too, and backup() (unlike restore/onetap, which both validate
    the zip before acting on it) rotates out the previous good backup with no
    other check. So verify `dst` against `total` (when known) the same way the
    chunked path does before treating this as a real success.
    """
    _vfs_delete(dst)
    try:
        _stream_copy(src, dst)
    except Exception:
        raise VfsCopyError("copy failed for %s -> %s" % (src, dst))
    if total >= 0:
        actual = _vfs_size(dst)
        for _ in range(SIZE_SETTLE_ATTEMPTS - 1):
            if actual == total:
                break
            xbmc.sleep(SIZE_SETTLE_DELAY_MS)
            actual = _vfs_size(dst)
        if actual != total:
            _vfs_delete(dst)
            raise VfsCopyError(
                "fallback copy size mismatch on %s (%s != %s)" % (dst, actual, total)
            )
    return COPY_OK


def copy_with_progress(src, dst, progress=None):
    """Copy `src` -> `dst` over Kodi VFS, retrying a definitive `VfsCopyError` up to
    `COPY_RETRY_ATTEMPTS` times (paused `COPY_RETRY_DELAY_SECS` apart) before giving
    up - never on cancel, which returns COPY_CANCELLED immediately with no retry.
    See `_copy_once` for the per-attempt guarantees.
    """
    attempt = 1
    while True:
        try:
            return _copy_once(src, dst, progress)
        except VfsCopyError as e:
            if attempt >= COPY_RETRY_ATTEMPTS:
                raise
            xbmc.log(
                "EZ Maintenance++ : copy attempt %d/%d failed (%s); retrying in %ds"
                % (attempt, COPY_RETRY_ATTEMPTS, e, COPY_RETRY_DELAY_SECS),
                level=xbmc.LOGWARNING,
            )
            xbmc.sleep(COPY_RETRY_DELAY_SECS * 1000)
            attempt += 1


def _copy_once(src, dst, progress=None):
    """Copy `src` -> `dst` over Kodi VFS in chunks, reporting to `progress` (a Progress)
    and honouring its cancel.

    Returns COPY_OK / COPY_CANCELLED. Raises VfsCopyError on a definitive failure
    (write refused, size mismatch, or `_fallback_copy` also failed).

    Guarantees:
      * Atomic: bytes are written to a sidecar and only renamed onto `dst` after a
        full, size-verified copy, so `dst` is never a truncated file.
      * Cancel: the partial sidecar is deleted and COPY_CANCELLED returned; the
        `_fallback_copy` retry is NEVER used for a cancel (that would defeat it).
      * Transient failure (a raised read/stream error) OR a chunked copy whose size
        never settles: the partial is deleted and `_fallback_copy` is tried once
        against a clean `dst`, itself size-verified against `total` the same way
        the chunked path is; if that also fails (or its size never settles),
        VfsCopyError is raised.
    """
    tmp = dst + COPY_TMP_SUFFIX
    _vfs_delete(tmp)
    total = _vfs_size(src)
    copied = 0
    cancelled = False

    try:
        fsrc = _open_reader(src)
        try:
            fdst = xbmcvfs.File(tmp, "w")
            try:
                while True:
                    if progress is not None and progress.cancelled():
                        cancelled = True
                        break
                    chunk = fsrc.readBytes(COPY_CHUNK)
                    if not chunk:
                        break
                    if not fdst.write(chunk):
                        # A False write is a definitive failure (share full / dropped);
                        # do NOT fall back - fail loud so rotation is skipped.
                        raise VfsCopyError("write refused on %s" % dst)
                    copied += len(chunk)
                    if progress is not None:
                        progress.bytes(copied, total)
            finally:
                fdst.close()
        finally:
            fsrc.close()
    except VfsCopyError:
        _vfs_delete(tmp)
        raise
    except Exception:
        # Transient/stream error: clean the partial, then try _fallback_copy once
        # against a now-clean destination.
        _vfs_delete(tmp)
        return _fallback_copy(src, dst, total)

    if cancelled:
        _vfs_delete(tmp)
        return COPY_CANCELLED

    # Full read completed: verify the sidecar is the expected size before it becomes
    # the real file (a short copy that never raised must still be caught). Poll
    # briefly first - see SIZE_SETTLE_ATTEMPTS - a big NFS write can still be
    # settling server-side the instant close() returns.
    if total >= 0:
        actual = _vfs_size(tmp)
        for _ in range(SIZE_SETTLE_ATTEMPTS - 1):
            if actual == total:
                break
            xbmc.sleep(SIZE_SETTLE_DELAY_MS)
            actual = _vfs_size(tmp)
        if actual != total:
            # Live-confirmed (copied=0, total=142444751/142438765, actual=0 on
            # every attempt across two separate real devices): this is Kodi's
            # own VFS read of a local source coming back empty, not a settling
            # write - retrying the same chunked path can never succeed.
            # _fallback_copy retries with a plain-Python read for a local src
            # (see the module docstring) instead of raising here.
            xbmc.log(
                "%s : chunked copy size mismatch (copied=%s total=%s actual=%s) "
                "on %s - falling back to a direct copy"
                % (HEADING, copied, total, actual, dst),
                level=xbmc.LOGWARNING,
            )
            _vfs_delete(tmp)
            return _fallback_copy(src, dst, total)

    _vfs_finalize(tmp, dst)
    return COPY_OK


# --------------------------------------------------------------------------- #
# Dialog helpers - one heading, one voice
# --------------------------------------------------------------------------- #
def confirm(message, heading=None, yeslabel="", nolabel=""):
    """A yes/no prompt. Returns True on yes."""
    return bool(
        xbmcgui.Dialog().yesno(
            heading if heading is not None else HEADING,
            message,
            yeslabel=yeslabel,
            nolabel=nolabel,
        )
    )


def confirm_wipe(message, heading=None):
    """A destructive yes/no prompt, defaulting the labels so 'this erases things' is
    unmistakable. Returns True only if the user explicitly chose to wipe."""
    return bool(
        xbmcgui.Dialog().yesno(
            heading if heading is not None else HEADING,
            message,
            yeslabel="Wipe",
            nolabel="Cancel",
        )
    )


def error(message, heading=None):
    """A blocking error acknowledgement."""
    xbmcgui.Dialog().ok(heading if heading is not None else HEADING, message)


def done(message, heading=None):
    """A blocking success acknowledgement."""
    xbmcgui.Dialog().ok(heading if heading is not None else HEADING, message)


def notify(message, heading=None, icon=None, time_ms=4000, sound=False):
    """A non-blocking toast. Uses Dialog().notification (NOT the executebuiltin
    'Notification(...)' string), so the heading is always the real add-on name."""
    try:
        default_icon = xbmcgui.NOTIFICATION_INFO
    except Exception:
        default_icon = "info"
    xbmcgui.Dialog().notification(
        heading if heading is not None else HEADING,
        message,
        icon if icon is not None else default_icon,
        time_ms,
        sound,
    )


def choose(options, heading=None):
    """A select list. Returns the chosen index, or -1 if cancelled."""
    return xbmcgui.Dialog().select(heading if heading is not None else HEADING, options)


def ask(prompt, default="", heading=None):
    """A text input. `heading` is accepted for a uniform signature; Kodi's input dialog
    shows the prompt itself. Returns the entered string ('' if cancelled)."""
    try:
        input_type = xbmcgui.INPUT_ALPHANUM
    except Exception:
        input_type = 0
    return xbmcgui.Dialog().input(prompt, default, type=input_type)


def ask_int(prompt, current, minimum, maximum, heading=None):
    """A bounded-integer input, pre-filled with `current`. Returns the entered int,
    or None if the user cancelled or entered something non-numeric/out of range (a
    toast explains why; the caller re-prompts with the same `current` unchanged)."""
    try:
        input_type = xbmcgui.INPUT_NUMERIC
    except Exception:
        input_type = 0
    entered = xbmcgui.Dialog().input(prompt, str(current), type=input_type)
    if entered == "":
        return None
    try:
        value = int(entered)
    except (TypeError, ValueError):
        notify("Enter a whole number from %d to %d" % (minimum, maximum))
        return None
    if value < minimum or value > maximum:
        notify("Enter a whole number from %d to %d" % (minimum, maximum))
        return None
    return value


# --------------------------------------------------------------------------- #
# Restart - the ONE mechanism. This is a QUIT, not a relaunch (LoadProfile lied).
# Quit runs CApplication::Stop() in full, which is what takes a restore live; it
# does NOT bring Kodi back up on any platform. RestartApp is the relaunch builtin
# and it is desktop-only, so this add-on never calls it. See wiz.py:979-981.
# --------------------------------------------------------------------------- #
def restart():
    """Quit Kodi. The clean shutdown is what takes a restore/wipe live.

    This never relaunches. Callers must tell the user to reopen Kodi themselves.
    """
    xbmc.executebuiltin("Quit")


def terminate():
    """Hard-exit the process (os.sync() then os._exit(0)) - the NO-FLUSH exit for
    Fresh Start ONLY.

    Unlike restart()'s Quit, this bypasses CApplication::Stop() and therefore its
    save-skin-settings-on-exit flush. That flush is what re-wrote the wiped custom
    skin's addon_data/<skin>/settings.xml AFTER a Fresh Start wipe (and, on tvOS,
    re-vectored it into NSUserDefaults), re-dirtying the clean slate for a skin whose
    add-on no longer exists. os.sync() first so the wipe result is durable on disk.

    Like restart(), this NEVER relaunches - the caller must tell the user to reopen
    Kodi. Do NOT use this for restore: restore NEEDS the Quit flush to take restored
    settings live.
    """
    import os

    try:
        os.sync()
    except Exception:
        pass
    os._exit(0)


def ask_terminate(status="", heading=None):
    """Fresh Start completion prompt: advise the user Kodi must close, and offer
    'Shut down' (hard-exit now via terminate(), which keeps the clean slate) or 'Later'
    (defer - Kodi stays up, still wiped, so the user can finish first and close it
    themselves).

    This renders only because Fresh Start REQUIRES the stock Estuary skin, which is
    bundled read-only in the APK (outside special://home) and therefore survives the
    wipe. From a wiped custom skin the dialog XML would be gone and nothing could draw.

    `status` is an optional line shown above the prompt (e.g. "Clean slate ready."). On
    tvOS / Fire TV Kodi cannot relaunch itself, so the wording is close-and-reopen.
    Returns True and exits on 'Shut down', False on 'Later'.
    """
    prompt = "Kodi needs to close to finish. Shut down now, then reopen Kodi."
    message = (status + "\n\n" + prompt) if status else prompt
    if xbmcgui.Dialog().yesno(
        heading if heading is not None else HEADING,
        message,
        yeslabel="Shut down",
        nolabel="Later",
    ):
        terminate()
        return True
    return False


def ask_restart(status="", heading=None):
    """Offer to finish the restore/wipe.

    On Fire TV / Android AND Apple TV / tvOS, Kodi CANNOT restart itself -
    `RestartApp` is desktop-only, so `restart()` can only `Quit`. Promising "Restart
    now?" there is misleading (it just closes). So on those appliances we say "close
    now, then reopen Kodi". tvOS is detected the same way nsud does
    (System.Platform.TVOS) - it is NOT Android, so the Android-only check used to
    give it the wrong wording.

    KNOWN WORDING BUG on the `else` branch (desktop, i.e. the macOS bench): it says
    "Restart now?" but `restart()` calls `Quit` there too, and Quit does not relaunch
    on ANY platform - `RestartApp` is the relaunch builtin and this add-on never calls
    it. An earlier version of this docstring asserted "on desktop Quit does relaunch
    cleanly"; that was FALSE, the same false belief that armed defect A in wiz.py.
    The wording is left alone deliberately: no fleet box is desktop, so this only
    affects the bench, and changing user-facing restore vocabulary is owner-gated.

    `status` is an optional line shown above the prompt (e.g. "Restore Complete: ...").
    Returns True and acts (Quit) if the user accepts. Callers on the post-wipe path MUST
    always reach this, even after a partial restore.
    """
    if xbmc.getCondVisibility("System.Platform.Android") or xbmc.getCondVisibility(
        "System.Platform.TVOS"
    ):
        prompt = "Kodi needs to close to finish.\nClose Kodi now, then reopen it."
        yeslabel, nolabel = "Close now", "Later"
    else:
        prompt = "Kodi needs to restart to finish. Restart now?"
        yeslabel, nolabel = "Restart", "Later"
    message = (status + "\n" + prompt) if status else prompt
    if xbmcgui.Dialog().yesno(
        heading if heading is not None else HEADING,
        message,
        yeslabel=yeslabel,
        nolabel=nolabel,
    ):
        restart()
        return True
    return False
