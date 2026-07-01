# -*- coding: utf-8 -*-
"""
One-Tap Restore: pin a SPECIFIC backup (a "golden snapshot") to a slot, then restore
it in one tap.

This module is the SAFE foundation (stage 1): the pin data model, the source picker
(VFS / Dropbox), and the read-only "verify this pin" check. The destructive apply path
(verify -> confirm -> wipe -> restore) is stage 2 and routes through the proven
wiz.restore(), so every tvOS fix comes for free.

A pin lives in a few add-on settings, per slot N (1..SLOTS):
    pinN_name : display name (user-editable text setting)
    pinN_kind : "vfs" | "dropbox" | ""   ("" = unset)
    pinN_src  : the VFS path, or the Dropbox file name
    pinN_type : "full" | "userdata" | "unknown"
    pinN_meta : human display string, e.g. "full . 130 MB"

Kept deliberately self-contained (xbmcaddon/xbmcgui/xbmcvfs only, like dropbox_remote)
so the model and the verify logic are fully unit-testable off-device.
"""

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

SLOTS = 6
ADDON = "EZ Maintenance++"
FIELDS = ("name", "kind", "src", "type", "meta")


def _log(msg):
    try:
        xbmc.log("EZMpp OneTap: %s" % msg, xbmc.LOGINFO)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Pin storage (settings-backed)
# --------------------------------------------------------------------------- #
def _get(slot, field, default=""):
    try:
        val = xbmcaddon.Addon().getSetting("pin%d_%s" % (slot, field))
        return (val or default).strip()
    except Exception:
        return default


def _set(slot, field, value):
    try:
        xbmcaddon.Addon().setSetting("pin%d_%s" % (slot, field), value or "")
    except Exception:
        pass


def get_pin(slot):
    """Return the pin dict for a slot. kind == '' means the slot is empty."""
    return {
        "slot": slot,
        "name": _get(slot, "name"),
        "kind": _get(slot, "kind"),
        "src": _get(slot, "src"),
        "type": _get(slot, "type", "unknown") or "unknown",
        "meta": _get(slot, "meta"),
    }


def is_set(pin):
    return bool(pin.get("kind")) and bool(pin.get("src"))


def save_pin(slot, name, kind, src, ptype, meta):
    _set(slot, "name", name)
    _set(slot, "kind", kind)
    _set(slot, "src", src)
    _set(slot, "type", ptype)
    _set(slot, "meta", meta)


def clear_pin(slot):
    for f in FIELDS:
        _set(slot, f, "")


def all_pins():
    return [get_pin(n) for n in range(1, SLOTS + 1)]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def infer_type(filename):
    """Best-effort backup type from the file name (informational)."""
    n = (filename or "").lower()
    if "userdata" in n:
        return "userdata"
    if "full" in n or "backup" in n:
        return "full"
    return "unknown"


def fmt_size(nbytes):
    try:
        nbytes = float(int(nbytes))
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return (
                ("%d %s" % (nbytes, unit))
                if unit == "B"
                else ("%.0f %s" % (nbytes, unit))
            )
        nbytes /= 1024.0
    return "%d B" % nbytes


def basename(path):
    return (path or "").replace("\\", "/").rstrip("/").split("/")[-1]


def label_for(pin):
    """One-line label for a pin row: 'Name  -  meta' (or 'Empty slot N')."""
    if not is_set(pin):
        return "Pin %d: (empty)" % pin["slot"]
    name = pin["name"] or basename(pin["src"]) or "Pin %d" % pin["slot"]
    bits = [
        b
        for b in (pin.get("meta"), "Dropbox" if pin["kind"] == "dropbox" else None)
        if b
    ]
    return "%s  -  %s" % (name, "  ".join(bits)) if bits else name


# --------------------------------------------------------------------------- #
# Pick a backup into a slot (configuration; safe)
# --------------------------------------------------------------------------- #
def pick(slot):
    """Choose a source + a specific backup zip and pin it to the slot."""
    idx = xbmcgui.Dialog().select(
        "Pin a backup (slot %d)" % slot,
        ["Network / local file (browse)", "Dropbox backup"],
    )
    if idx == 0:
        _pick_vfs(slot)
    elif idx == 1:
        _pick_dropbox(slot)


def _pick_vfs(slot):
    path = xbmcgui.Dialog().browse(
        1, "Pick a backup .zip", "files", ".zip", False, False, ""
    )
    if not path or not path.lower().endswith(".zip"):
        return
    size = 0
    try:
        size = xbmcvfs.Stat(path).st_size()
    except Exception:
        pass
    name = basename(path)
    ptype = infer_type(name)
    save_pin(
        slot,
        "%s  (%s)" % (name, fmt_size(size)),
        "vfs",
        path,
        ptype,
        "%s . %s" % (ptype, fmt_size(size)),
    )
    xbmcgui.Dialog().notification(ADDON, "Pinned: %s" % name)
    _log("pinned vfs slot %d: %s" % (slot, path))


def _pick_dropbox(slot):
    from resources.lib.modules import dropbox_remote

    if not _signed_in():
        xbmcgui.Dialog().ok(
            ADDON, "Sign in to Dropbox first (Settings -> Backup/Restore)."
        )
        return
    try:
        names = dropbox_remote.list_backups()
    except Exception:
        xbmcgui.Dialog().ok(ADDON, "Could not list Dropbox backups.")
        return
    if not names:
        xbmcgui.Dialog().ok(ADDON, "No backups found in Dropbox.")
        return
    idx = xbmcgui.Dialog().select("Pick a Dropbox backup", list(names))
    if idx == -1:
        return
    chosen = names[idx]
    ptype = infer_type(chosen)
    save_pin(
        slot, "%s  (Dropbox)" % chosen, "dropbox", chosen, ptype, "%s . Dropbox" % ptype
    )
    xbmcgui.Dialog().notification(ADDON, "Pinned: %s" % chosen)
    _log("pinned dropbox slot %d: %s" % (slot, chosen))


def _keep_or(slot, fallback):
    """Keep a user-set name, else default to the file name."""
    existing = _get(slot, "name")
    if existing and existing.lower() not in ("", "name", "pin %d" % slot):
        return existing
    return fallback


def _signed_in():
    return bool(_setting_global("dropbox_refresh_token"))


def _setting_global(key):
    try:
        return (xbmcaddon.Addon().getSetting(key) or "").strip()
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Verify a pin (READ-ONLY; never wipes, never downloads the whole file)
# --------------------------------------------------------------------------- #
def verify(slot, silent=False):
    """True if the pinned backup still exists and looks like a valid zip."""
    ok, msg = verify_pin(get_pin(slot))
    if not silent:
        xbmcgui.Dialog().ok(ADDON, msg)
    return ok


def verify_pin(pin):
    if not is_set(pin):
        return False, "Slot %d is empty - pin a backup first." % pin["slot"]
    if pin["kind"] == "vfs":
        return _verify_vfs(pin["src"])
    if pin["kind"] == "dropbox":
        return _verify_dropbox(pin["src"])
    return False, "Unknown pin source."


def _verify_vfs(path):
    if not xbmcvfs.exists(path):
        return False, "Backup not found:\n%s" % path
    size = 0
    try:
        size = xbmcvfs.Stat(path).st_size()
    except Exception:
        pass
    if size <= 0:
        return False, "Backup is empty (0 bytes):\n%s" % path
    # Cheap zip sniff: the local-file-header signature is 'PK\x03\x04'. Avoids reading
    # the whole file; the apply path does the full is_zipfile() check before any wipe.
    try:
        with xbmcvfs.File(path) as f:
            head = bytes(f.readBytes(4))
    except Exception:
        return False, "Backup could not be read:\n%s" % path
    if head[:2] != b"PK":
        return False, "That file is not a zip:\n%s" % path
    return True, "Valid backup:\n%s\n%s" % (basename(path), fmt_size(size))


def _verify_dropbox(name):
    from resources.lib.modules import dropbox_remote

    if not _signed_in():
        return False, "Sign in to Dropbox to verify this pin."
    try:
        names = dropbox_remote.list_backups()
    except Exception:
        return False, "Could not reach Dropbox to verify."
    if name in names:
        return True, "Valid (present in Dropbox):\n%s" % name
    return False, "Not in Dropbox anymore:\n%s" % name


# --------------------------------------------------------------------------- #
# Apply a pin: verify -> fetch + FULLY validate -> confirm -> WIPE -> restore.
#
# Safety invariant: the box is NEVER wiped until the snapshot has been fetched to
# local disk AND confirmed a valid zip. A missing/corrupt/unreachable pin can never
# strand a wiped box. The wipe preserves this add-on, its runtime deps, and the staged
# snapshot (special://temp), so restore has everything it needs afterward.
# --------------------------------------------------------------------------- #
_ADDON_ID = "script.ezmaintenanceplusplus"


def _wipe_excludes():
    keep = {
        "temp",  # the staged, already-validated snapshot lives here - must survive
        "backupdir",
        "backup.zip",
        "script.module.requests",
        "script.module.urllib3",
        "script.module.chardet",
        "script.module.idna",
        "script.module.certifi",
    }
    try:
        keep.add(xbmcaddon.Addon().getAddonInfo("id") or _ADDON_ID)
    except Exception:
        keep.add(_ADDON_ID)
    return keep


def _wipe(home, excludes, keep_files=None):
    """Remove everything under `home` except any entry named in `excludes` (matched at any
    depth - protects addons/<this add-on> and temp/) and any absolute path in `keep_files`
    (e.g. Kodi's add-on state DB, so the surviving add-on stays ENABLED). Returns files
    removed."""
    import os

    keep_files = keep_files or set()
    removed = 0
    for root, dirs, files in os.walk(home, topdown=True):
        dirs[:] = [d for d in dirs if d not in excludes]
        for fname in files:
            path = os.path.join(root, fname)
            if path in keep_files:
                continue
            try:
                os.remove(path)
                removed += 1
            except Exception:
                pass
    for root, dirs, _files in os.walk(home, topdown=False):
        for dname in dirs:
            if dname in excludes:
                continue
            try:
                os.rmdir(os.path.join(root, dname))  # only removes if now empty
            except Exception:
                pass
    return removed


def keep_addon_db():
    """Absolute paths of Kodi's add-on state database (Addons*.db). Preserving it through a
    wipe keeps EZ Maintenance++ ENABLED on the restart instead of coming back disabled
    (which is what made it look 'gone' after a wipe)."""
    import glob
    import os

    try:
        db_dir = xbmcvfs.translatePath("special://home/userdata/Database")
        return set(glob.glob(os.path.join(db_dir, "Addons*.db")))
    except Exception:
        return set()


def _stage(pin):
    """Fetch the pinned snapshot into special://temp (preserved by the wipe) and return
    its local path, or None. Runs BEFORE any wipe."""
    import os

    if pin["kind"] == "dropbox":
        from resources.lib.modules import dropbox_remote

        return xbmcvfs.translatePath(dropbox_remote.download(pin["src"]))
    dest_special = "special://temp/" + basename(pin["src"])
    dest_local = xbmcvfs.translatePath(dest_special)
    try:
        os.remove(dest_local)
    except Exception:
        pass
    return dest_local if xbmcvfs.copy(pin["src"], dest_special) else None


def _cleanup(path):
    import os

    try:
        os.remove(path)
    except Exception:
        pass


def apply(slot):
    """One-tap restore of a pinned backup: wipe-then-restore, but only after the snapshot
    is fetched and confirmed a valid zip locally (never wipe on a bad pin)."""
    import os
    import zipfile

    pin = get_pin(slot)
    ok, msg = verify_pin(pin)
    if not ok:
        xbmcgui.Dialog().ok(ADDON, msg + "\n\nNothing was changed.")
        return

    label = pin["name"] or basename(pin["src"])
    if not xbmcgui.Dialog().yesno(
        ADDON,
        "Restore this backup?\n%s\n\nThis WIPES this Kodi, then restores it. Continue?"
        % label,
        yeslabel="Wipe + Restore",
        nolabel="Cancel",
    ):
        return

    # Fetch + FULLY validate BEFORE touching the box (the safety invariant).
    dp = xbmcgui.DialogProgress()
    try:
        dp.create(ADDON, "Fetching the backup...\nPlease wait")
    except Exception:
        pass
    try:
        local = _stage(pin)
    except Exception:
        local = None
    try:
        dp.close()
    except Exception:
        pass

    good = False
    if local:
        try:
            good = os.path.getsize(local) > 0 and zipfile.is_zipfile(local)
        except Exception:
            good = False
    if not good:
        if local:
            _cleanup(local)
        xbmcgui.Dialog().ok(
            ADDON, "Could not fetch a valid backup. Nothing was changed."
        )
        return

    # ---- point of no return: wipe (add-on + deps + temp survive), then restore ----
    from resources.lib.modules import wiz

    _wipe(xbmcvfs.translatePath("special://home/"), _wipe_excludes())
    try:
        wiz.restore(local, confirm=False)
    finally:
        _cleanup(local)


# --------------------------------------------------------------------------- #
# The One-Tap Restore MENU - the user-facing entry, opened from the add-on's main
# menu (NOT settings). Lists pinned backups; tap one to restore it.
# --------------------------------------------------------------------------- #
def menu():
    pins = all_pins()
    labels = [
        label_for(p) if is_set(p) else "Slot %d  -  (empty)" % p["slot"] for p in pins
    ]
    labels.append("[ + ]  Pin or change a backup...")
    idx = xbmcgui.Dialog().select("One-Tap Restore - pick a backup to restore", labels)
    if idx == -1:
        return
    if idx == len(pins):
        menu_pick()
        return
    chosen = pins[idx]
    if is_set(chosen):
        _pin_actions(chosen["slot"])  # Restore / Rename / Verify / Change / Remove
    else:
        pick(chosen["slot"])  # empty slot tapped -> pin one


def menu_pick():
    opts = []
    for n in range(1, SLOTS + 1):
        p = get_pin(n)
        opts.append("Slot %d  -  %s" % (n, p["name"] if is_set(p) else "empty"))
    idx = xbmcgui.Dialog().select("Pin a backup to which slot?", opts)
    if idx != -1:
        pick(idx + 1)


def _pin_actions(slot):
    """Tapping a pinned backup: choose what to do with it."""
    pin = get_pin(slot)
    name = pin["name"] or basename(pin["src"])
    idx = xbmcgui.Dialog().select(
        name,
        ["Restore now", "Rename", "Verify", "Change backup (re-pick)", "Remove pin"],
    )
    if idx == 0:
        apply(slot)
    elif idx == 1:
        rename(slot)
    elif idx == 2:
        verify(slot)
    elif idx == 3:
        pick(slot)
    elif idx == 4:
        remove(slot)


def rename(slot):
    pin = get_pin(slot)
    if not is_set(pin):
        return
    current = pin["name"] or basename(pin["src"])
    new = xbmcgui.Dialog().input("Rename this backup", current)
    if new and new.strip():
        _set(slot, "name", new.strip())
        xbmcgui.Dialog().notification(ADDON, "Renamed to: %s" % new.strip())


def remove(slot):
    if not is_set(get_pin(slot)):
        return
    if xbmcgui.Dialog().yesno(
        ADDON, "Remove this pin?\nThe backup file itself is NOT deleted."
    ):
        clear_pin(slot)
        xbmcgui.Dialog().notification(ADDON, "Pin removed")
