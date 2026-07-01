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
    back to the opaque `xbmcvfs.copy` ONLY on a transient failure, NEVER on cancel.
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


def copy_with_progress(src, dst, progress=None):
    """Copy `src` -> `dst` over Kodi VFS in chunks, reporting to `progress` (a Progress)
    and honouring its cancel.

    Returns COPY_OK / COPY_CANCELLED. Raises VfsCopyError on a definitive failure
    (write refused, size mismatch, or the opaque fallback also failed).

    Guarantees:
      * Atomic: bytes are written to a sidecar and only renamed onto `dst` after a
        full, size-verified copy, so `dst` is never a truncated file.
      * Cancel: the partial sidecar is deleted and COPY_CANCELLED returned; the opaque
        xbmcvfs.copy fallback is NEVER used for a cancel (that would defeat it).
      * Transient failure (a raised read/stream error): the partial is deleted and the
        opaque xbmcvfs.copy is tried once against a clean `dst`; if that also fails,
        VfsCopyError is raised.
    """
    tmp = dst + COPY_TMP_SUFFIX
    _vfs_delete(tmp)
    total = _vfs_size(src)
    copied = 0
    cancelled = False

    try:
        fsrc = xbmcvfs.File(src, "r")
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
        # Transient/stream error: clean the partial, then try the opaque VFS copy once
        # against a now-clean destination.
        _vfs_delete(tmp)
        try:
            if xbmcvfs.copy(src, dst):
                return COPY_OK
        except Exception:
            pass
        raise VfsCopyError("copy failed for %s -> %s" % (src, dst))

    if cancelled:
        _vfs_delete(tmp)
        return COPY_CANCELLED

    # Full read completed: verify the sidecar is the expected size before it becomes
    # the real file (a short copy that never raised must still be caught).
    if total >= 0:
        actual = _vfs_size(tmp)
        if actual != total:
            _vfs_delete(tmp)
            raise VfsCopyError("size mismatch on %s (%s != %s)" % (dst, actual, total))

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


# --------------------------------------------------------------------------- #
# Restart - the ONE mechanism (Quit lets Kodi restart cleanly; LoadProfile lied)
# --------------------------------------------------------------------------- #
def restart():
    """Restart Kodi the only way that actually takes a restore/wipe live."""
    xbmc.executebuiltin("Quit")


def ask_restart(
    message="Kodi needs to restart to finish. Restart now?",
    heading=None,
    yeslabel="Restart",
    nolabel="Later",
):
    """Offer a restart. Returns True (and quits) if the user accepts. Callers on the
    post-wipe path MUST always reach this, even after a partial restore."""
    if xbmcgui.Dialog().yesno(
        heading if heading is not None else HEADING,
        message,
        yeslabel=yeslabel,
        nolabel=nolabel,
    ):
        restart()
        return True
    return False
