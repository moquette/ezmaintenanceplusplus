"""
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
import json
import os
import re
import zipfile
from resources.lib.modules import control, maintenance, tools, ui
from datetime import datetime
from resources.lib.modules.backtothefuture import unicode, PY2

if PY2:
    from io import open as open

    translatePath = xbmc.translatePath
else:
    translatePath = xbmcvfs.translatePath
    unicode = str

dialog = xbmcgui.Dialog()
addonInfo = xbmcaddon.Addon().getAddonInfo

AddonTitle = "EZ Maintenance++"
AddonID = "script.ezmaintenanceplusplus"


# VfsCopyError now lives in ui.py (one definition for the whole add-on); alias it here so
# `wiz.VfsCopyError` and `except VfsCopyError` keep working. A FAILED ship raises this so
# backup() reports an error and SKIPS rotation (a cancel instead returns canceled=True),
# and the prior good backup is never pruned behind a ship that never landed.
VfsCopyError = ui.VfsCopyError


class BackupCaptureError(Exception):
    """A tvOS NSUserDefaults capture failure. On Apple TV the owner's settings live
    ONLY in NSUserDefaults, so a failed capture means the backup is MISSING exactly
    the data the owner cares about: the backup must FAIL loudly (error dialog, log,
    no success report, no rotation) rather than land silently incomplete."""


# The self-describing metadata member every backup zip carries (read back by
# restore()'s manifest verification and by default.py's analyze_backup_zip).
MANIFEST_NAME = "backup_manifest.json"

# EZM's own settings.xml (userdata-relative, forward-slash) - the ONE addon_data
# file a backup must never embed: it carries the source box's download/restore
# paths and the dropbox_refresh_token. Matched by suffix so a per-profile copy is
# covered too. Kept in lockstep with nsub._SECRET_TAIL (the tvOS capture path).
_SECRET_TAIL = "addon_data/%s/settings.xml" % AddonID


def _is_secret_arc(arc):
    """True iff `arc` (a home- OR userdata-anchored zip arcname) is EZM's own
    settings.xml, in the top-level or any per-profile userdata."""
    a = (arc or "").replace("\\", "/").lstrip("/")
    return a.endswith(_SECRET_TAIL)


def _source_os():
    """'tvos' | 'android' | 'other', via Kodi's own platform conditions (the same
    detection nsud._is_tvos uses). Defaults to 'other' on any error."""
    try:
        if xbmc.getCondVisibility("System.Platform.TVOS"):
            return "tvos"
        if xbmc.getCondVisibility("System.Platform.Android"):
            return "android"
    except Exception:
        pass
    return "other"


class ZipResult(object):
    """CreateZip's truthful outcome: whether the user canceled, how many entries
    landed in the zip, and exactly which paths could NOT be captured."""

    def __init__(self, canceled=False, entries=0, failed=None):
        self.canceled = bool(canceled)
        self.entries = int(entries)
        self.failed = list(failed or [])


def _as_zip_result(res):
    """Normalize a CreateZip return. Tolerates a bare canceled bool (older callers
    and test stubs) by wrapping it with no failure information."""
    if isinstance(res, ZipResult):
        return res
    return ZipResult(canceled=bool(res))


class ExtractResult(object):
    """ExtractWithProgress's truthful outcome: what actually landed on disk, what
    was intentionally skipped, and exactly which members failed to extract.
    extracted == -1 means 'unknown' (a bare-bool return was normalized)."""

    def __init__(self, canceled=False, extracted=0, skipped=0, total=0, failed=None):
        self.canceled = bool(canceled)
        self.extracted = extracted
        self.skipped = skipped
        self.total = total
        self.failed = list(failed or [])


def _as_extract_result(res):
    """Normalize an extract return. Tolerates a bare canceled bool (older callers
    and test stubs), which carries no per-member counts (extracted == -1)."""
    if isinstance(res, ExtractResult):
        return res
    return ExtractResult(canceled=bool(res), extracted=-1)


def _failed_lines(failed, limit=10):
    """Human-readable bullet lines for a failure list, truncated for a dialog."""
    lines = [" - %s" % f for f in failed[:limit]]
    if len(failed) > limit:
        lines.append(" - ... and %d more (see the log)" % (len(failed) - limit))
    return lines


def _write_manifest(zip_file, entries, failed):
    """Embed MANIFEST_NAME into the (still open) backup zip: when it was made, on
    which OS, how many entries it holds, and EXACTLY which paths it could not
    capture. A backup that is missing something must say so - restore() verifies
    the archive against this and the owner tools display it."""
    manifest = {
        "created": datetime.now().replace(microsecond=0).isoformat(),
        "source_os": _source_os(),
        "entries": int(entries),
        "failed": [unicode(f) for f in failed],
    }
    zip_file.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))


def _read_manifest(zip_path):
    """The embedded MANIFEST_NAME as a dict, or None. Older backups have no
    manifest - tolerated; an unreadable/foreign-shaped manifest is treated as
    absent rather than failing the restore."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            if MANIFEST_NAME not in z.namelist():
                return None
            data = z.read(MANIFEST_NAME)
        manifest = json.loads(data.decode("utf-8"))
        return manifest if isinstance(manifest, dict) else None
    except Exception:
        return None


def _manifest_problems(manifest, namelist):
    """Verify the archive against its own manifest. Returns human-readable problem
    strings ([] when everything checks out): a member-count mismatch means the
    archive does not hold what the backup said it wrote, and a non-empty 'failed'
    list means the BACKUP itself already knew it was missing those paths."""
    problems = []
    member_names = [n for n in namelist if n != MANIFEST_NAME]
    members = len(member_names)
    raw = manifest.get("entries", -1)
    if isinstance(raw, (list, tuple)):
        # A name-list manifest: strictly better diagnostics - report exactly
        # which promised entries the archive does not hold.
        missing = sorted(set(unicode(e) for e in raw) - set(member_names))
        if missing:
            problems.append(
                "manifest mismatch: %d entrie(s) promised by the manifest are "
                "missing from the archive: %s" % (len(missing), ", ".join(missing[:5]))
            )
    else:
        try:
            expected = int(raw)
        except (TypeError, ValueError):
            expected = -1
        if expected >= 0 and expected != members:
            problems.append(
                "manifest mismatch: the archive holds %d entries but its manifest "
                "recorded %d" % (members, expected)
            )
    failed = manifest.get("failed")
    if isinstance(failed, (list, tuple)) and failed:
        shown = ", ".join(unicode(f) for f in list(failed)[:5])
        if len(failed) > 5:
            shown += ", and %d more" % (len(failed) - 5)
        problems.append(
            "the backup itself could not capture %d item(s): %s" % (len(failed), shown)
        )
    return problems


# Kodi's own network-browse dialog (used to pick download.path/restore.path -
# both "type=folder" settings, browse-only, no manual text entry) bakes an
# explicit port into the nfs:// URL it hands back, e.g.
# nfs://192.168.7.2:2049/export/path. That explicit-port form breaks Kodi's
# own NFS client write path - proven live, independently, on two different
# boxes (a VfsCopyError / 0-byte copy every time) - while the port-free form
# (nfs://host/export/path) works. Since the destination setting can only ever
# be set via that same browse dialog, this can recur on every future box;
# strip the port defensively wherever the setting is read rather than relying
# on a one-off manual edit each time.
_NFS_PORT_RE = re.compile(r"^(nfs://[^/]+?):\d+(/|$)")


def _strip_nfs_port(path):
    """Strip an explicit port from an nfs:// VFS path; anything else (smb://,
    a local path, empty/None) passes through unchanged."""
    if not path:
        return path
    return _NFS_PORT_RE.sub(r"\1\2", path)


# Backup filenames carry a trailing _YYYYMMDDHHMM stamp before ".zip".
_STAMP_RE = re.compile(r"_(\d{12})\.zip$", re.IGNORECASE)


def _name_stamp(name):
    """Parse the trailing _YYYYMMDDHHMM stamp from a backup filename.

    Returns the 12-digit string (lexically == chronologically sortable) or ""
    when the file has no tool stamp (e.g. a user-renamed backup).
    """
    m = _STAMP_RE.search(name or "")
    return m.group(1) if m else ""


# A "keep" token anywhere in a name marks a protected backup even if it still
# carries a stamp (rescues a user who renamed but left the date on).
_KEEP_TOKEN_RE = re.compile(r"keep", re.IGNORECASE)


def _is_rolling(name):
    """True only for a tool-created ROLLING backup that keep-N may prune.

    A rolling backup matches the tool's own naming contract: a trailing
    _YYYYMMDDHHMM.zip stamp (always appended at backup time) AND no explicit
    'keep' token. Every other file - a user rename, a manual copy, a foreign zip -
    is PROTECTED: rotation never counts it toward keep-N and never deletes it.
    Renaming is exactly how users protect a golden backup, so this fails SAFE:
    an unrecognized name is always protected, never a rotation victim.
    """
    if not _name_stamp(name):
        return False
    if _KEEP_TOKEN_RE.search(name or ""):
        return False
    return True


def get_Kodi_Version():
    try:
        KODIV = float(xbmc.getInfoLabel("System.BuildVersion")[:4])
    except:
        KODIV = 0
    return KODIV


def FIX_SPECIAL():

    HOME = translatePath("special://home")
    dp = xbmcgui.DialogProgress()
    dp.create(AddonTitle, "Renaming paths...")
    url = translatePath("special://userdata")
    for root, dirs, files in os.walk(url):
        for file in files:
            if file.endswith(".xml"):
                if PY2:
                    dp.update(0, "Fixing", "[COLOR dodgerblue]" + file + "[/COLOR]")
                else:
                    dp.update(
                        0, "Fixing" + "\n" + "[COLOR dodgerblue]" + file + "[/COLOR]"
                    )
                try:
                    a = open((os.path.join(root, file)), "r", encoding="utf-8").read()
                    b = a.replace(HOME, "special://home/")
                    f = open((os.path.join(root, file)), mode="w", encoding="utf-8")
                    f.write(unicode(b))
                    f.close()
                except:
                    try:
                        a = open((os.path.join(root, file)), "r").read()
                        b = a.replace(HOME, "special://home/")
                        f = open((os.path.join(root, file)), mode="w")
                        f.write(unicode(b))
                        f.close()
                    except:
                        pass


def _destination():
    # 0 = Local, 1 = Network (SMB/NFS), 2 = Dropbox. Default 0.
    try:
        return int(control.setting("destination") or "0")
    except:
        return 0


# BACKUP ZIP
def backup(mode="full"):
    KODIV = get_Kodi_Version()
    dest = _destination()

    if mode == "full":
        defaultName = "kodi_backup"
        BACKUPDATA = control.HOME
        getSetting = xbmcaddon.Addon().getSetting
        if getSetting("BackupFixSpecialHome") == "true":
            FIX_SPECIAL()
    elif mode == "userdata":
        defaultName = "kodi_settings"
        BACKUPDATA = control.USERDATA
    else:
        return

    if not os.path.exists(BACKUPDATA):
        return

    if dest == 2:
        _backup_dropbox(mode, defaultName, BACKUPDATA)
        return

    # Local / Network (SMB/NFS): path-based CreateZip (VFS copy seam handles nfs://, smb://).
    backupdir = _strip_nfs_port(control.setting("download.path"))
    if backupdir == "" or backupdir == None:
        control.infoDialog("Please Setup a Path for Downloads first")
        control.openSettings()
        return

    name = tools._get_keyboard(
        default=defaultName, heading="Name your Backup", cancel="-"
    )
    if name == "-":
        return
    today = datetime.now().strftime("%Y%m%d%H%M")
    today = re.sub("[^0-9]", "", str(today))
    zipDATE = "_%s.zip" % today
    name = re.sub(" ", "_", name) + zipDATE
    # Confirm the final filename (with the auto timestamp) BEFORE committing to the
    # zip build - parity with restore's confirm. The name also decides rotation
    # protection (see _rotate_vfs), so a fat-fingered name is worth catching here.
    if not ui.confirm(
        "Create this backup?\n\n%s" % name,
        heading=AddonTitle,
        yeslabel="Back up",
        nolabel="Cancel",
    ):
        return
    backup_zip = translatePath(os.path.join(backupdir, name))
    exclude_database = [".pyo", ".log"]

    try:
        maintenance.clearCache(mode="silent")
        maintenance.deleteThumbnails(mode="silent")
        maintenance.purgePackages(mode="silent")
    except:
        pass

    # Only special://home/temp AT THE WALK ROOT (transient + self-referential);
    # a nested dir that merely happens to be named "temp" is real content.
    exclude_dirs = ["temp"]
    try:
        result = _as_zip_result(
            CreateZip(
                BACKUPDATA,
                backup_zip,
                "Creating Backup",
                "Backing up files",
                exclude_dirs,
                exclude_database,
                prune_home_root=(mode == "full"),
            )
        )
    except VfsCopyError as e:
        # The ship to the share/path FAILED. Never report success and never
        # rotate - the prior good backup stays untouched.
        xbmc.log(
            "%s : backup copy failed: %s" % (AddonTitle, type(e).__name__),
            level=xbmc.LOGERROR,
        )
        dialog.ok(
            AddonTitle,
            "Backup failed: could not write to the Backup Location. "
            "Your previous backup was not touched.",
        )
        return
    except BackupCaptureError as e:
        # tvOS settings capture FAILED: the zip would silently miss the owner's
        # settings. Fail loudly - no success dialog, no rotation, no partial zip.
        xbmc.log("%s : backup failed: %s" % (AddonTitle, e), level=xbmc.LOGERROR)
        try:
            xbmcvfs.delete(backup_zip)  # VFS-safe: handles nfs:// and local
        except Exception:
            pass
        dialog.ok(
            AddonTitle,
            "Backup FAILED: this device's settings could not be captured (%s). "
            "No backup was created. Your previous backup was not touched." % e,
        )
        return
    if result.canceled:
        try:
            xbmcvfs.delete(backup_zip)  # VFS-safe: handles nfs:// and local
        except:
            pass
        dialog.ok(AddonTitle, "Backup canceled")
    else:
        # Only rotate AFTER a confirmed-landed backup - and NEVER when this backup is
        # itself incomplete: rotating on a swiss-cheese zip can delete the last COMPLETE
        # backup in favor of one that is missing files (with backup.keep=1 that loss is
        # total). An incomplete backup is kept, reported honestly, and rotation waits
        # for the next clean run.
        if not result.failed:
            _rotate_vfs(backupdir, protect={name})
        _report_backup_done(result)


def _backup_dropbox(mode, defaultName, BACKUPDATA):
    from resources.lib.modules import dropbox_remote

    if not control.setting("dropbox_refresh_token").strip():
        control.infoDialog("Sign in to Dropbox first (Settings -> Backup/Restore)")
        return

    name = tools._get_keyboard(
        default=defaultName, heading="Name your Backup", cancel="-"
    )
    if name == "-":
        return
    today = datetime.now().strftime("%Y%m%d%H%M")
    today = re.sub("[^0-9]", "", str(today))
    name = re.sub(" ", "_", name) + ("_%s.zip" % today)
    # Confirm the final filename before committing (parity with the local/network
    # path and with restore's confirm).
    if not ui.confirm(
        "Create this Dropbox backup?\n\n%s" % name,
        heading=AddonTitle,
        yeslabel="Back up",
        nolabel="Cancel",
    ):
        return

    try:
        maintenance.clearCache(mode="silent")
        maintenance.deleteThumbnails(mode="silent")
        maintenance.purgePackages(mode="silent")
    except:
        pass

    # Keep the box awake for the whole backup - zip build + upload can run for several
    # minutes, and an idle/screensaver suspension was stalling the upload. Released in
    # the finally below. (On tvOS the OS screensaver can still appear, but the upload's
    # per-chunk resume rides out a brief stall instead of restarting.)
    xbmc.executebuiltin("InhibitIdleShutdown(true)")

    # Build the zip LOCALLY in special://temp (no "://" so CreateZip skips its VFS copy
    # branch), then ship it to Dropbox. The prior remote backup stays untouched unless
    # this upload confirms, so a failed run never destroys the last good backup.
    staged = "special://temp/" + name
    # Only special://home/temp AT THE WALK ROOT (transient + self-referential);
    # a nested dir that merely happens to be named "temp" is real content.
    exclude_dirs = ["temp"]
    exclude_database = [".pyo", ".log"]
    try:
        try:
            result = _as_zip_result(
                CreateZip(
                    BACKUPDATA,
                    translatePath(staged),
                    "Creating Backup",
                    "Backing up files",
                    exclude_dirs,
                    exclude_database,
                    prune_home_root=(mode == "full"),
                )
            )
        except BackupCaptureError as e:
            # tvOS settings capture FAILED: the zip would silently miss the owner's
            # settings. Fail loudly - nothing is uploaded, nothing is rotated.
            xbmc.log("%s : backup failed: %s" % (AddonTitle, e), level=xbmc.LOGERROR)
            dialog.ok(
                AddonTitle,
                "Backup FAILED: this device's settings could not be captured (%s). "
                "Nothing was uploaded. Your previous backup was not touched." % e,
            )
            return
        if result.canceled:
            dialog.ok(AddonTitle, "Backup canceled")
            return
        try:
            # One uniform upload gauge. The cancel is handled by the adapter
            # (as_dropbox_callback returns not cancelled()), so a canceled upload raises
            # DropboxCanceled and never touches the last good backup. The context manager
            # closes the dialog even if upload raises.
            with ui.Progress("Uploading to Dropbox...", heading=AddonTitle) as sp:
                dropbox_remote.upload(staged, name, progress=sp.as_dropbox_callback())
        except dropbox_remote.DropboxCanceled:
            dialog.ok(
                AddonTitle, "Backup canceled. Your previous backup was not touched."
            )
            return
        except Exception as e:
            xbmc.log(
                "%s : Dropbox upload failed: %s" % (AddonTitle, type(e).__name__),
                level=xbmc.LOGERROR,
            )
            dialog.ok(
                AddonTitle,
                "Dropbox upload failed. Your previous backup was not touched.",
            )
            return
        # Confirmed landed -> safe to rotate the Dropbox folder, but ONLY when this
        # backup is complete: an incomplete backup never rotates out the last good one.
        if not result.failed:
            _rotate_dropbox(dropbox_remote, protect={name})
        _report_backup_done(result, suffix=" (Dropbox)")
    finally:
        xbmc.executebuiltin("InhibitIdleShutdown(false)")
        try:
            os.remove(translatePath(staged))
        except:
            pass


def _report_backup_done(result, suffix=""):
    """Honest completion dialog: a backup that could not capture something says
    EXACTLY what is missing before claiming success; a clean backup says so plainly."""
    if result.failed:
        dialog.ok(
            AddonTitle,
            "Backup complete%s, but %d item(s) could NOT be captured:\n%s"
            % (suffix, len(result.failed), "\n".join(_failed_lines(result.failed))),
        )
    else:
        dialog.ok(AddonTitle, "Backup complete" + suffix)


def _keep_n():
    try:
        return int(control.setting("backup.keep") or "0")
    except:
        return 0


def _rotate_vfs(backupdir, protect=None):
    # Keep-N for Local/Network: prune ONLY tool-created ROLLING backups (a trailing
    # _YYYYMMDDHHMM.zip stamp). A user-renamed / protected backup is never counted
    # toward N and never deleted - renaming is how users keep a golden backup.
    n = _keep_n()
    if n <= 0:
        return
    protect = protect or set()
    try:
        _dirs, files = xbmcvfs.listdir(backupdir)
        # Only rolling (stamped) files are prune candidates, so every entry HAS a
        # stamp and the oldest-first sort is fully defined.
        rolling = sorted(
            [
                f
                for f in files
                if f.endswith(".zip") and _is_rolling(f) and f not in protect
            ],
            key=lambda f: (_name_stamp(f), f),
        )
        for old in rolling[: max(0, len(rolling) - n)]:
            try:
                xbmcvfs.delete(translatePath(os.path.join(backupdir, old)))
            except Exception:
                pass
    except Exception as e:
        xbmc.log(
            "%s : backup rotation skipped: %s" % (AddonTitle, type(e).__name__),
            level=xbmc.LOGWARNING,
        )


def _rotate_dropbox(dropbox_remote, protect=None):
    # Keep-N for Dropbox: prune ONLY tool-created ROLLING backups; a user-renamed /
    # protected backup is never counted toward N and never deleted.
    n = _keep_n()
    if n <= 0:
        return
    protect = protect or set()
    try:
        names = dropbox_remote.list_backups()  # newest-first
        rolling = [nm for nm in names if _is_rolling(nm) and nm not in protect]
        for old in rolling[n:]:
            try:
                dropbox_remote.delete(old)
            except Exception:
                pass
    except Exception as e:
        xbmc.log(
            "%s : Dropbox rotation skipped: %s" % (AddonTitle, type(e).__name__),
            level=xbmc.LOGWARNING,
        )


def restoreFolder():
    if _destination() == 2:
        _restore_dropbox()
        return

    names = []
    links = []
    zipFolder = _strip_nfs_port(control.setting("restore.path"))
    if zipFolder == "" or zipFolder == None:
        control.infoDialog("Please Setup a Zip Files Location first")
        control.openSettings()
        return
    try:
        _dirs, _files = xbmcvfs.listdir(
            zipFolder
        )  # VFS list works on nfs:// / smb:// too
    except:
        _files = []
    for zipFile in _files:
        if zipFile.endswith(".zip"):
            url = translatePath(os.path.join(zipFolder, zipFile))
            names.append(zipFile)
            links.append(url)
    select = control.selectDialog(names)
    if select != -1:
        # How to restore: a self-describing MENU instead of a yes/no, so the choice is
        # unambiguous. Each line is a full instruction (no "Yes/No" to map onto Wipe/Merge
        # buttons), the safe "add on top" is FIRST and highlighted, and Back/Cancel aborts
        # instead of silently merging. The wipe itself is still deferred to restore(), which
        # ONLY wipes after the chosen zip is staged and validated (a bad zip never wipes).
        how = control.selectDialog(
            [
                "Keep what's on this device and add the backup on top",
                "Erase this device first, then restore a clean copy",
                "Cancel - don't restore anything",
            ],
            heading="Restore backup: how should I do it?",
        )
        # Corroborating hint from the file name (defense-in-depth; the zip's own layout is
        # the authoritative anchor). Maps a "kodi_settings"/userdata name -> userdata anchor.
        # Lazy import breaks the onetap<->wiz cycle (onetap imports wiz inside apply()).
        hint = None
        try:
            from resources.lib.modules import onetap

            hint = {"full": "home", "userdata": "userdata"}.get(
                onetap.infer_type(names[select])
            )
        except Exception:
            pass
        if how == 0:
            restore(links[select], wipe=False, anchor_hint=hint)  # merge
        elif how == 1:
            restore(links[select], wipe=True, anchor_hint=hint)  # clean clone
        # how == 2 (Cancel) or -1 (Back): do nothing.


def _restore_dropbox():
    from resources.lib.modules import dropbox_remote

    if not control.setting("dropbox_refresh_token").strip():
        control.infoDialog("Sign in to Dropbox first (Settings -> Backup/Restore)")
        return
    try:
        names = dropbox_remote.list_backups()
    except Exception as e:
        xbmc.log(
            "%s : Dropbox list failed: %s" % (AddonTitle, type(e).__name__),
            level=xbmc.LOGERROR,
        )
        dialog.ok(AddonTitle, "Could not list Dropbox backups.")
        return
    if not names:
        dialog.ok(AddonTitle, "No backups found in Dropbox.")
        return
    select = control.selectDialog(names)
    if select == -1:
        return
    chosen = names[select]
    local = None
    try:
        # One uniform download gauge. The adapter enables cancel (download honors a
        # False return by dropping the partial and raising DropboxCanceled), where the
        # old hand-rolled callback returned None and the cancel was a silent no-op.
        with ui.Progress("Downloading from Dropbox...", heading=AddonTitle) as dp2:
            special = dropbox_remote.download(
                chosen, progress=dp2.as_dropbox_callback()
            )
        local = translatePath(special)
    except dropbox_remote.DropboxCanceled:
        # User canceled the download - the partial was already removed; nothing changed.
        return
    except Exception as e:
        xbmc.log(
            "%s : Dropbox download failed: %s" % (AddonTitle, type(e).__name__),
            level=xbmc.LOGERROR,
        )
        dialog.ok(AddonTitle, "Could not download that backup from Dropbox.")
        return
    # Hand the LOCAL staged path to restore(): it has no "://", so restore() extracts it
    # directly (the unchanged extractor). restore() removes the temp on the remote branch
    # only, so clean it up here after the user confirms (or declines) the restore.
    try:
        restore(local)
    finally:
        try:
            os.remove(local)
        except:
            pass


def restore(zipFile, confirm=True, post_wipe=False, wipe=False, anchor_hint=None):
    """Extract a backup zip over special://home and offer a restart.

    post_wipe=True is the One-Tap path: the box has ALREADY been wiped and the snapshot
    ALREADY fully validated by the caller before the wipe. In that mode the extract is a
    single UNINTERRUPTIBLE unit (no cancel) and we NEVER early-return - a wiped box must
    always be driven to the restart prompt, never left silent on a partial/empty extract.

    wipe=True is the normal-restore "clean clone" path (chosen in restoreFolder()): this
    function does the wipe ITSELF, but ONLY under the mandatory safe ordering - the zip is
    staged locally AND validated (size>0 + a real zip) FIRST; only THEN is the box wiped;
    then the extract runs as the same UNINTERRUPTIBLE unit as post_wipe. If the zip is
    missing/short/corrupt the function ABORTS with the box UNTOUCHED (it never wipes on a
    bad zip). The wipe reuses the PROVEN One-Tap wipe (onetap._wipe / _wipe_excludes),
    which preserves this add-on, its runtime deps, and special://temp (the staged zip).
    """
    if confirm and not post_wipe:
        if not ui.confirm(
            "This will overwrite all your current settings ... Are you sure?",
            heading=AddonTitle,
            yeslabel="Yes",
            nolabel="No",
        ):
            return

    # Stage a remote (VFS) zip locally first; a plain local path extracts directly. The
    # staging copy now has a gauge + cancel (it used to be a silent xbmcvfs.copy).
    local = zipFile
    staged = False
    wipe_failed_n = 0
    if "://" in zipFile:
        local = translatePath(os.path.join("special://temp", os.path.basename(zipFile)))
        try:
            with ui.Progress("Preparing the backup...", heading=AddonTitle) as sp:
                if (
                    ui.copy_with_progress(zipFile, local, progress=sp)
                    == ui.COPY_CANCELLED
                ):
                    try:
                        os.remove(local)
                    except OSError:
                        pass
                    return
            staged = True
        except Exception:
            # Staging failed; the validation below reports the missing/short zip.
            pass

    # Validate BEFORE extracting so we never report success on a missing / empty / bad
    # zip. After a wipe the snapshot was already validated by the caller BEFORE the box was
    # touched, so post_wipe does NOT early-return here (that would strand a wiped box).
    # NOTE: wipe=True still validates here (it is NOT yet post_wipe), because on the
    # clean-clone path THIS is the validation that MUST pass before we are allowed to wipe.
    try:
        size = os.path.getsize(local)
    except OSError:
        size = 0
    if not post_wipe and (size == 0 or not zipfile.is_zipfile(local)):
        if staged:
            try:
                os.remove(local)
            except OSError:
                pass
        dialog.ok(
            AddonTitle,
            "Restore failed: the backup is missing or not a valid zip (%d bytes). "
            "Nothing was changed." % size,
        )
        return

    # Clean-clone path: the zip is now staged + validated, so it is finally safe to wipe.
    # Ensure the validated zip lives in special://temp (which the wipe preserves) so the
    # source survives the wipe, wipe with the PROVEN One-Tap wipe, then flip to post_wipe
    # semantics (uninterruptible extract that always reaches the restart prompt).
    if wipe and not post_wipe:
        try:
            temp_dir = translatePath("special://temp")
            os.makedirs(temp_dir, exist_ok=True)
            if os.path.dirname(os.path.abspath(local)) != os.path.abspath(temp_dir):
                import shutil

                staged_local = os.path.join(temp_dir, os.path.basename(local))
                if os.path.abspath(staged_local) != os.path.abspath(local):
                    shutil.copyfile(local, staged_local)
                local = staged_local
                staged = True
        except Exception:
            # Could not park the validated zip somewhere the wipe preserves; ABORT
            # rather than risk wiping the box out from under its own restore source.
            if staged:
                try:
                    os.remove(local)
                except OSError:
                    pass
            dialog.ok(
                AddonTitle,
                "Restore failed: could not stage the backup for a clean-clone restore. "
                "Nothing was changed.",
            )
            return
        # Lazy import breaks the onetap<->wiz cycle: onetap imports wiz lazily (inside
        # apply()), so importing onetap here at call time - not at module top - is the
        # lowest-risk way to reuse its proven wipe without an import loop.
        from resources.lib.modules import onetap

        # Show the SAME progress bar the extract uses so the wipe is not a dead screen for
        # ~90s. Counts only (note is static) - a per-file name would re-trigger the text
        # renderer crash ExtractWithProgress was fixed to avoid. Not cancelable: a cancel
        # mid-wipe would strand a half-wiped box, so we never check p.cancelled() here.
        with ui.Progress("Wiping the device clean...", heading="Restoring") as wp:
            _wres = onetap._wipe(
                translatePath("special://home/"),
                onetap._wipe_excludes(),
                progress=lambda done, total: wp.items(
                    done, total, note="Removing old files"
                ),
            )
        # The wipe's failure count is NEVER discarded: leftover files - and on tvOS
        # leftover NSUserDefaults keys - shadow or pollute the restore. It lands in
        # the final report via wipe_failed_n below.
        try:
            wipe_failed_n = int(_wres[2])
        except (TypeError, IndexError, ValueError):
            wipe_failed_n = 0
        # From here on this IS a post-wipe restore: uninterruptible, never early-returns.
        post_wipe = True

    def _rlog(m):
        xbmc.log("%s : %s" % (AddonTitle, m), xbmc.LOGINFO)

    # A backup zip is either HOME-anchored (full: members under userdata/ + addons/) or
    # USERDATA-anchored (a "kodi_settings" backup: bare userdata contents, NO userdata/
    # prefix). Extract to the MATCHING root - extracting a userdata-anchored zip to HOME is
    # exactly the bug that scattered settings into the home root and bricked the box.
    try:
        _names = zipfile.ZipFile(local).namelist()
    except Exception:
        _names = []
    items = len(_names)
    manifest = _read_manifest(local)  # None on older, pre-manifest backups
    anchor = _archive_anchor(_names, hint=anchor_hint)
    extract_root = control.HOME if anchor == "home" else control.USERDATA
    _rlog(
        "restore: anchor=%s -> extract_root=%s (%d members, manifest=%s)"
        % (anchor, extract_root, items, "yes" if manifest is not None else "no")
    )

    # Restore-scoped PVR pause. pvr.iptvsimple reads instance-settings-*.xml only at
    # client start and FLUSHES its stale in-memory copy over them at teardown
    # (hardware-proven: docs/playbooks/kodi-settings-clobber.md) - so extracting IPTV
    # config under a LIVE client is undone at the next clean shutdown. When (and only
    # when) the archive carries pvr.iptvsimple config and the client is enabled, it is
    # disabled here and ALWAYS re-enabled after the durability rewrite (both the cancel
    # path and the completion path re-enable; a re-enable failure is reported loudly).
    # This is the single sanctioned exception to "restore never toggles add-ons": a
    # bounded, restore-scoped pause - never boot-time automation, never install/stage.
    early_problems = []
    iptv_prefixes = _iptv_profile_prefixes(_names, anchor)
    pvr_paused = False
    if iptv_prefixes:
        if _pvr_enabled():
            pvr_paused = _pvr_set_enabled(False)
            if pvr_paused:
                # Record the outstanding pause BEFORE the extract, so if this
                # restore is interrupted (crash, power loss) the boot service
                # re-enables the client instead of leaving it disabled forever.
                try:
                    from resources.lib.modules import tools

                    tools.mark_pvr_paused()
                except Exception:
                    pass
                _rlog("restore: paused %s for the IPTV restore window" % _PVR_ADDON_ID)
            else:
                early_problems.append(
                    "could not pause the IPTV client; its shutdown may overwrite "
                    "the restored IPTV settings"
                )

    # Skip the temp/ self-reference, recomputed against the ACTUAL extract root. On a
    # userdata anchor relpath(home/temp, home/userdata) -> '../temp' -> stays None (a
    # userdata zip has no home temp tree), which is correct.
    skip_prefix = None
    try:
        rel = os.path.relpath(translatePath("special://temp"), extract_root)
        if not rel.startswith(".."):
            skip_prefix = rel.replace("\\", "/").rstrip("/") + "/"
    except Exception:
        pass

    # Post-wipe the extract is uninterruptible: a cancel here would strand the wiped box,
    # so cancelable is off and we ALWAYS fall through to the restart prompt below.
    result = ExtractResult(canceled=False, extracted=-1)
    try:
        with ui.Progress("Extracting the backup...", heading="Restoring") as p:
            result = _as_extract_result(
                ExtractWithProgress(
                    local,
                    extract_root,
                    p,
                    skip_prefix=skip_prefix,
                    cancelable=not post_wipe,
                    skip_member=_extract_skip(anchor, skip_prefix),
                )
            )
    finally:
        if staged:
            try:
                os.remove(local)
            except OSError:
                pass

    if result.canceled and not post_wipe:
        # The user canceled a (non-wipe) restore. The stray sweep runs only AFTER a
        # completed extract, so the box's own IPTV config was NOT touched; the only
        # residue is whatever members the (now-stopped) extract already overwrote.
        if pvr_paused:
            if _pvr_set_enabled(True):
                _clear_pvr_pause_marker()
            else:
                dialog.ok(
                    AddonTitle,
                    "The IPTV client could not be re-enabled automatically. "
                    "It will be re-enabled on the next restart.",
                )
        dialog.ok(
            AddonTitle,
            "Restore Canceled. Files extracted before the cancel remain on disk; "
            "your IPTV configuration was not swept.",
        )
        return

    # IPTV duplicate-instance STRAY sweep (AFTER the extract, deliberately): the
    # archive's own instance files were just written by the extract; this removes only
    # instance-settings-*.xml the archive does NOT carry (both layers on tvOS), so the
    # restored instance set exactly equals the archive and numbering can never
    # accumulate (the 2026-07-08 brick guard). Running it post-extract means a cancel
    # can never destroy config the box already had. No-op without IPTV in the archive.
    swept, sweep_failed = _sweep_iptv_instances(_names, anchor, log=_rlog)

    # Everything that did not go perfectly, in one honest list for the final report.
    problems = list(early_problems)
    if wipe_failed_n:
        problems.append(
            "the pre-restore wipe could not remove %d item(s); leftovers may "
            "shadow or pollute the restored state" % wipe_failed_n
        )
    # Members the anchor rules refused to map are surfaced, never silently dropped:
    # on a HOME anchor a member outside the allowed top-level dirs is real content
    # from a foreign/legacy layout the restore cannot place - the owner must know it
    # did not land (dropping it silently while saying "Complete" is the old lie).
    _skip_fn = _extract_skip(anchor, skip_prefix)
    unmapped = [
        n
        for n in _names
        if _skip_fn(n)
        and (n or "").lstrip("/").replace("\\", "/") != MANIFEST_NAME
        and not (
            skip_prefix
            and (n or "").lstrip("/").replace("\\", "/").startswith(skip_prefix)
        )
    ]
    if unmapped:
        problems.append(
            "%d member(s) outside the recognized layout were NOT restored: %s"
            % (len(unmapped), ", ".join(unmapped[:5]))
        )
    if manifest is not None:
        problems.extend(_manifest_problems(manifest, _names))
    if sweep_failed:
        problems.append(
            "%d stale IPTV instance file(s) could not be removed: %s"
            % (len(sweep_failed), ", ".join(sweep_failed[:5]))
        )

    # Make the restore actually take effect on every platform - critically on tvOS,
    # where Kodi mirrors guisettings.xml in NSUserDefaults and would otherwise revert a
    # file-only restore. Mirror the official Backup add-on: re-apply settings through
    # the JSON-RPC API and rescan add-ons, then offer the (unified) restart. A failure
    # here is never swallowed silently: it is logged AND lands in the final report.
    applied = 0
    try:
        from resources.lib.modules import _kodisettings

        applied = _kodisettings.apply_guisettings(
            os.path.join(control.USERDATA, "guisettings.xml")
        )
    except Exception as e:
        xbmc.log(
            "%s : apply_guisettings failed: %s: %s" % (AddonTitle, type(e).__name__, e),
            level=xbmc.LOGERROR,
        )
        problems.append("settings re-apply failed (%s)" % type(e).__name__)
    try:
        xbmc.executebuiltin("UpdateLocalAddons")
    except Exception as e:
        xbmc.log(
            "%s : UpdateLocalAddons failed: %s" % (AddonTitle, type(e).__name__),
            level=xbmc.LOGWARNING,
        )

    # tvOS durability: the extract wrote userdata/*.xml with plain POSIX I/O, which on Apple
    # TV BYPASSES Kodi's CTVOSFile VFS - so the restored settings never reach NSUserDefaults
    # (tvOS's only persistent store) and are shadowed by the stale mirror at boot. Re-write
    # each restored userdata/*.xml THROUGH xbmcvfs so tvOS vectors it into NSUserDefaults
    # (durable on the first reopen, no clean shutdown needed). This is a generic settings
    # durability rewrite - it does NOT enable, disable, or stage the IPTV client. Runs AFTER
    # apply_guisettings/UpdateLocalAddons (so nothing re-saves defaults over it) and BEFORE
    # the restart prompt. A failure is logged AND reported, never silently swallowed.
    try:
        from resources.lib.modules import nsud

        # Purge FIRST (before the rewrite): out-of-scope vector-everything-era keys
        # shadow restored files and inflate the NSUserDefaults store; clearing them
        # before vectoring the restored xml keeps the store's peak size down (the
        # 512 KB warn / 1 MB kill budget) and cannot touch in-scope keys the rewrite
        # is about to write. The purge is best-effort HYGIENE of PRE-EXISTING box
        # cruft (old keys this restore did not create) - it must NOT downgrade the
        # restore's own success verdict. A key it cannot clear (undecodable, or a
        # confirm-read that tvOS's async preference flush has not yet reflected) is
        # LOGGED, never added to `problems`; the restore's success is decided by the
        # extract/sweep/rewrite counts, which are the files THIS restore handled.
        if hasattr(nsud, "purge_stale_keys"):
            try:
                pg = nsud.purge_stale_keys(control.USERDATA, log=_rlog)
                try:
                    pg_failed = int(pg[3])
                except (TypeError, IndexError, ValueError):
                    pg_failed = 0
                if pg_failed:
                    xbmc.log(
                        "%s : stale-key purge left %d pre-existing key(s) unresolved "
                        "(logged, does not affect this restore)"
                        % (AddonTitle, pg_failed),
                        level=xbmc.LOGINFO,
                    )
            except Exception as e:
                # purge_stale_keys is documented never to raise; guard anyway, and
                # still do NOT fail the restore over a hygiene step.
                xbmc.log(
                    "%s : purge_stale_keys raised %s: %s (ignored; restore stands)"
                    % (AddonTitle, type(e).__name__, e),
                    level=xbmc.LOGWARNING,
                )

        rw = nsud.rewrite_userdata_xml(control.USERDATA, log=_rlog)
        try:
            rw_failed = int(rw[2])
        except (TypeError, IndexError, ValueError):
            rw_failed = 0
        if rw_failed:
            problems.append("tvOS settings rewrite failed for %d file(s)" % rw_failed)
    except Exception as e:
        xbmc.log(
            "%s : tvOS settings rewrite failed: %s: %s"
            % (AddonTitle, type(e).__name__, e),
            level=xbmc.LOGERROR,
        )
        problems.append("tvOS settings rewrite failed (%s)" % type(e).__name__)

    # ALWAYS resume the paused IPTV client - the pause is restore-scoped by contract.
    # The client starts fresh and reads the restored (and on tvOS, vectored) instance
    # files. A re-enable failure is loud: it lands in the report AND its own dialog.
    if pvr_paused:
        if _pvr_set_enabled(True):
            _clear_pvr_pause_marker()
            _rlog("restore: resumed %s" % _PVR_ADDON_ID)
        else:
            problems.append(
                "the IPTV client could not be re-enabled now - it will be re-enabled "
                "automatically on the next restart"
            )

    # A restore clones the SOURCE box's guisettings, so this box now carries the wrong
    # device name (services.devicename) AND a buffer (filecache.memorysize) sized for the
    # wrong RAM. Drop a persistent marker so the boot service runs the post-restore tune-up
    # on the next boot: offer to rename this device, then to retune the buffer for it. Written
    # HERE - AFTER the extract completes and right before the restart prompt - deliberately:
    # the pre-extract wipe (wipe/One-Tap paths) runs earlier and would remove it, and the
    # extract itself would overwrite it. Covers BOTH the normal restore and One-Tap (onetap
    # calls restore(post_wipe=True), which reaches this same point). The marker keeps its
    # historical name (.ezm_buffer_prompt) - it now gates the whole combined flow. Guarded: a
    # marker-write failure must never break the restore.
    try:
        from resources.lib.modules import tools

        tools.mark_buffer_prompt_pending()
    except Exception:
        pass

    # Truthful completion: "Complete" is only ever claimed when every member the
    # extract attempted actually landed AND nothing else went wrong. Otherwise the
    # report says INCOMPLETE, lists exactly what did not restore, and still drives
    # the (mandatory on post-wipe) restart prompt.
    extracted_n = result.extracted if result.extracted >= 0 else items
    total_n = result.total if result.total else items
    if not result.failed and not problems:
        ui.ask_restart(
            "Restore Complete: %d items restored, %d settings applied."
            % (extracted_n, applied)
        )
        return
    lines = []
    if result.failed:
        lines.append("%d item(s) did NOT restore:" % len(result.failed))
        lines.extend(_failed_lines(result.failed))
    lines.extend(problems)
    dialog.ok(AddonTitle, "Restore INCOMPLETE:\n%s" % "\n".join(lines))
    ui.ask_restart(
        "Restore INCOMPLETE: %d of %d items restored, %d failed, %d settings applied."
        % (extracted_n, total_n, len(result.failed), applied)
    )


def CreateZip(
    folder,
    zip_filename,
    message_header,
    message1,
    exclude_dirs,
    exclude_files,
    prune_home_root=False,
):
    # EZ Maintenance++ : Python's zipfile can only write a LOCAL file. When the backup
    # destination is a Kodi VFS path (nfs://, smb://, ...) build the zip in special://temp
    # and copy the finished file to the destination via xbmcvfs (see the tail of this fn).
    remote = "://" in zip_filename
    target = zip_filename
    if remote:
        zip_filename = translatePath(
            os.path.join("special://temp", os.path.basename(target))
        )
    abs_src = os.path.abspath(folder)
    try:
        os.remove(zip_filename)
    except Exception:
        pass

    canceled = False
    failed = []  # arcnames that could NOT be captured (per-file accounting)
    entries_total = 0

    # ONE uniform gauge (percent + count). The divide-by-zero guard lives INSIDE
    # ui.Progress.items(), so an empty backup folder (0 files) no longer raises, and the
    # context manager guarantees the dialog is closed - the old DialogProgress leaked.
    # os.walk with the default onerror=None SKIPS a whole directory subtree
    # silently when its listing fails (SELinux/FUSE EACCES on sdcardfs, a dir
    # removed mid-walk). That is directory-granularity silent data loss - the
    # collector turns every unlistable directory into a counted, named failure.
    def _walk_error(err):
        try:
            failed.append(
                "%s/ (directory unreadable: %s)"
                % (
                    os.path.relpath(getattr(err, "filename", "?") or "?", abs_src),
                    type(err).__name__,
                )
            )
        except Exception:
            failed.append("directory unreadable (%s)" % type(err).__name__)

    try:
        with ui.Progress(message1, heading=message_header) as p:
            ITEM = []
            for _base, _dirs, files in os.walk(folder, onerror=lambda _e: None):
                ITEM.extend(files)
            n_item = len(ITEM)
            count = 0
            zip_file = zipfile.ZipFile(
                zip_filename, "w", zipfile.ZIP_DEFLATED, allowZip64=True
            )
            written_arcs = (
                set()
            )  # arcnames the POSIX walk captured (for the tvOS augment)
            try:
                for dirpath, dirnames, filenames in os.walk(
                    folder, onerror=_walk_error
                ):
                    if canceled:
                        break
                    at_root = os.path.normpath(dirpath) == abs_src
                    # exclude_dirs (i.e. ["temp"]) means special://home/temp - the
                    # transient, self-referential dir at the WALK ROOT. Prune it there
                    # ONLY: a nested dir that happens to be named "temp"
                    # (addon_data/<id>/temp/...) is real content and must be captured.
                    if at_root:
                        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
                    filenames[:] = [f for f in filenames if f not in exclude_files]
                    # A FULL backup (folder == special://home) must capture ONLY the allowed
                    # home-level dirs. Stray root userdata pollution (loose guisettings.xml or
                    # a sibling addon_data/ left by the old userdata-restore-to-HOME bug) would
                    # otherwise be re-captured and re-scattered on the next restore. Depth-scoped
                    # to the walk ROOT so the real userdata/addon_data/... is untouched. Never
                    # set for userdata/dropbox mode, where those names ARE the content.
                    if prune_home_root and at_root:
                        dirnames[:] = [d for d in dirnames if d in _HOME_ALLOWED_TOP]
                        filenames[:] = []
                    for fname in filenames:
                        if p.cancelled():
                            canceled = True
                            break
                        count += 1
                        p.items(count, n_item, note="[COLOR lime]%s[/COLOR]" % fname)
                        fpath = os.path.normpath(os.path.join(dirpath, fname))
                        # Forward-slash arcnames (the ZIP convention) so the tvOS augment's
                        # idempotency check (nsub compares its own '/'-joined arcs) matches -
                        # os.sep is already '/' on the darwin/tvOS/Android fleet, a no-op there.
                        arc = fpath[len(abs_src) + 1 :].replace(os.sep, "/")
                        # NEVER embed EZM's own settings.xml. It carries the SOURCE box's
                        # download/restore paths AND its dropbox_refresh_token (a secret);
                        # restoring it onto another box hands that box this box's paths and
                        # token. nsub excludes it on the tvOS NSUserDefaults path; the POSIX
                        # walk (Fire TV / on-disk copy) must exclude it too. Matched by suffix
                        # so a per-profile copy is covered. (Caught on real hardware 2026-07-16
                        # by tools/backup_lint.py's secret check.)
                        if _is_secret_arc(arc):
                            continue
                        # PER-FILE error handling: one unreadable file must never
                        # silently drop the rest of its directory (the old
                        # per-directory except swallowed every file after the first
                        # failure). Name it, count it, keep going - the manifest and
                        # the completion dialog report exactly what is missing.
                        try:
                            zip_file.write(fpath, arc)
                            written_arcs.add(arc)
                        except Exception as e:
                            failed.append(arc)
                            xbmc.log(
                                "%s : backup could not capture %s (%s: %s)"
                                % (AddonTitle, fpath, type(e).__name__, e),
                                level=xbmc.LOGERROR,
                            )
                # tvOS completeness: the POSIX walk above cannot see the userdata *.xml
                # that live only in NSUserDefaults (guisettings.xml, profiles.xml, addon
                # settings, ...), so on Apple TV the backup would silently omit exactly
                # the settings the owner cares about. Capture them by reading Kodi's
                # NSUserDefaults plist directly and add any the walk missed. Additive +
                # idempotent - a pure no-op on Fire TV / desktop (no such plist). On
                # tvOS a capture failure FAILS the backup (BackupCaptureError) instead
                # of silently shipping an incomplete zip. See nsub.py (and why xbmcvfs
                # reads cannot be used) + docs/plans/atv-restore-*.
                if not canceled:
                    cap_added, cap_failed = _capture_nsud(
                        zip_file, abs_src, written_arcs
                    )
                    failed.extend(cap_failed)
                    entries_total = len(written_arcs) + cap_added
                    # The backup carries its own truth: what it holds and what it
                    # could NOT capture, so restore (and the owner) can verify it.
                    # A manifest that cannot be written (disk full in temp, a dying
                    # card) FAILS the backup: without its truth layer the zip is a
                    # stamped partial that would later restore as "Complete" - the
                    # exact lie this contract exists to kill.
                    try:
                        _write_manifest(zip_file, entries_total, failed)
                    except Exception as e:
                        raise BackupCaptureError(
                            "backup manifest could not be written (%s)"
                            % type(e).__name__
                        )
            finally:
                zip_file.close()
    except BackupCaptureError:
        # The zip is NOT a valid backup (tvOS settings missing). Remove the partial
        # file and let the caller report the failure - never a success, never rotation.
        try:
            os.remove(zip_filename)
        except OSError:
            pass
        raise

    # If the destination was a VFS path, the zip was built in special://temp - ship the
    # finished file to the share/cloud with a gauge, then drop the staged temp.
    if remote:
        try:
            if not canceled:
                # Chunked, gauged, ATOMIC copy. It raises VfsCopyError on a definitive
                # failure (share offline / no space / permission) so backup() reports the
                # error and SKIPS rotation - a discarded failure used to look like success
                # and prune a good old backup. A cancel mid-ship returns COPY_CANCELLED.
                with ui.Progress(
                    "Copying to the backup location", heading=message_header
                ) as sp:
                    shipped = ui.copy_with_progress(zip_filename, target, progress=sp)
                if shipped == ui.COPY_CANCELLED:
                    canceled = True
        finally:
            try:
                os.remove(zip_filename)
            except Exception:
                pass

    return ZipResult(canceled=canceled, entries=entries_total, failed=failed)


def _capture_nsud(zip_file, abs_src, written_arcs):
    """Run nsub's NSUserDefaults capture (the tvOS backup augment) with honest
    failure semantics. Returns (added, failed_list).

    On tvOS the owner's settings live ONLY in NSUserDefaults, so a capture that
    raises, reports failures, or finds no store at all means the backup is MISSING
    the owner's settings: raise BackupCaptureError and fail the backup loudly.
    Off tvOS no NSUserDefaults store exists and the capture is a true no-op, so
    any hiccup there is logged and ignored."""
    tvos = _source_os() == "tvos"

    def _blog(m):
        xbmc.log("%s : %s" % (AddonTitle, m), xbmc.LOGINFO)

    try:
        from resources.lib.modules import nsub

        res = nsub.capture_nsud_userdata(zip_file, abs_src, written_arcs, log=_blog)
    except BackupCaptureError:
        raise
    except Exception as e:
        if tvos:
            raise BackupCaptureError(
                "NSUserDefaults capture raised %s: %s" % (type(e).__name__, e)
            )
        xbmc.log(
            "%s : NSUserDefaults capture skipped (non-tvOS): %s"
            % (AddonTitle, type(e).__name__),
            level=xbmc.LOGWARNING,
        )
        return (0, [])
    try:
        added, skipped, cap_failed = res
    except (TypeError, ValueError):
        added, skipped, cap_failed = 0, 0, 0
    # Tolerate either shape for the failure field: a count, or a list of paths.
    failed_list = (
        [unicode(f) for f in cap_failed]
        if isinstance(cap_failed, (list, tuple, set))
        else []
    )
    failed_n = len(failed_list) if failed_list else _as_count(cap_failed)
    if tvos:
        if failed_n:
            raise BackupCaptureError(
                "%d NSUserDefaults value(s) could not be captured" % failed_n
            )
        if not (_as_count(added) or _as_count(skipped)):
            # On tvOS the store ALWAYS exists (guisettings.xml at minimum lives
            # there); finding nothing to add or skip means the plist was never
            # read - the backup would silently miss every NSUserDefaults-only
            # setting.
            raise BackupCaptureError("NSUserDefaults store not found or unreadable")
    return (_as_count(added), failed_list)


def _as_count(value):
    """An int from a count-or-list field (len() of a collection); 0 on anything else."""
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return len(value)
        except TypeError:
            return 0


# EXTRACT ZIP
def ExtractZip(
    _in, _out, progress=None, skip_prefix=None, cancelable=True, skip_member=None
):
    """Extract _in over _out. `progress` is a ui.Progress (gauge + cancel); None means a
    silent extract. cancelable=False makes the extract UNINTERRUPTIBLE (the post-wipe
    One-Tap path, where a cancel would strand a wiped box)."""
    if progress is not None:
        return ExtractWithProgress(
            _in,
            _out,
            progress,
            skip_prefix=skip_prefix,
            cancelable=cancelable,
            skip_member=skip_member,
        )
    return ExtractNOProgress(_in, _out)


def ExtractNOProgress(_in, _out):
    try:
        zin = zipfile.ZipFile(_in, "r")
        names = zin.namelist()
        zin.extractall(_out)
        return ExtractResult(extracted=len(names), total=len(names))
    except Exception as e:
        return ExtractResult(
            failed=["archive extract failed (%s: %s)" % (type(e).__name__, e)]
        )


# How often the extract refreshes the progress dialog. See the CRASH FIX note in
# ExtractWithProgress: redrawing it for every one of thousands of files SIGSEGVs Kodi's
# native text renderer on Fire OS 8, so we refresh at most every _UPDATE_EVERY files.
_UPDATE_EVERY = 50


# The only top-level dirs that legitimately live at special://home. A HOME-anchored extract
# member (or a full-backup root entry) whose FIRST path segment is anything else is stray
# userdata pollution: the on-disk residue of the historical "userdata-mode backup extracted
# to HOME" bug, which scattered guisettings.xml/addon_data/ as siblings of userdata/.
_HOME_ALLOWED_TOP = frozenset({"userdata", "addons", "media", "system", "temp"})


def _first_seg(name):
    return (name or "").lstrip("/").replace("\\", "/").split("/", 1)[0]


def _archive_anchor(namelist, hint=None):
    """'home' if any member sits under userdata/ or addons/ (a full/home backup), else
    'userdata' (a settings backup: bare userdata contents, no userdata/ prefix). `hint`
    (from a pin type / filename) breaks a tie ONLY on an empty/degenerate archive."""
    for raw in namelist:
        if _first_seg(raw) in ("userdata", "addons"):
            return "home"
    if not namelist and hint in ("home", "userdata"):
        return hint
    return "userdata"


def _extract_skip(anchor, skip_prefix):
    """Return a predicate(name)->bool: True => do NOT extract this member. Drops the temp/
    self-reference, and on a HOME anchor drops any member whose first segment is not an
    allowed home-level dir (stray userdata pollution - the on-disk residue of the historical
    userdata-restore-to-HOME bug). This only CHOOSES what to extract; it never deletes an
    existing file. Inert on a USERDATA anchor, where addon_data/ and guisettings.xml ARE the
    real content."""

    def _skip(name):
        rel = (name or "").lstrip("/").replace("\\", "/")
        if rel == MANIFEST_NAME:
            return (
                True  # backup metadata (verified separately), never a file to restore
            )
        if skip_prefix and rel.startswith(skip_prefix):
            return True
        if anchor == "home" and _first_seg(rel) not in _HOME_ALLOWED_TOP:
            return True
        return False

    return _skip


# NOTE: the boot-time home-root self-heal sweep (sweep_home_root_pollution) was REMOVED. It
# deleted files at special://home on every boot, and nothing in EZ Maintenance++ may delete
# files at boot. The anchor-aware extract (_extract_skip / _archive_anchor) prevents the
# scatter at its source - a userdata backup extracts UNDER userdata/, never at the home root -
# so there is no pollution to sweep in the first place.


def _order_userdata_first(infos):
    """Defense-in-depth ordering for a restore extract: write userdata/ (irreplaceable
    settings) FIRST, then everything else, then addons/ LAST (add-ons re-download).

    A backup zip lists userdata/ as its LAST ~70 entries, so a mid-extract failure with
    the archive's own order would lose ALL settings while the (recoverable) add-ons were
    already written. Reordering makes settings the first thing on disk. Order within each
    bucket is preserved (stable)."""
    userdata, addons, other = [], [], []
    for it in infos:
        fn = (getattr(it, "filename", "") or "").lstrip("/")
        if fn.startswith("userdata/"):
            userdata.append(it)
        elif fn.startswith("addons/"):
            addons.append(it)
        else:
            other.append(it)
    return userdata + other + addons


# --------------------------------------------------------------------------- #
# Restore-side IPTV duplicate-instance sweep (the 2026-07-08 brick guard).
#
# pvr.iptvsimple numbers its instances via instance-settings-<N>.xml. When a
# restore lays the archive's instance files OVER a target that already has its
# own (differently numbered) ones, the union accumulates duplicate instances -
# the failure that bricked a box on 2026-07-08. The sweep below runs AFTER the
# extract and ONLY when the archive actually carries pvr.iptvsimple addon_data:
# it deletes the TARGET's STRAY instance-settings-*.xml (files the archive does
# not carry; top-level, and per-profile only for profiles the archive carries)
# so the restored IPTV state exactly equals the archive. It deletes BOTH tvOS
# layers (NSUserDefaults key + POSIX file) and VERIFIES the key drop by
# re-reading the plist, because xbmcvfs.delete's boolean lies on tvOS. It never
# installs or configures any add-on; the only toggle anywhere in the restore is
# the bounded PVR pause documented in restore() itself.
# --------------------------------------------------------------------------- #
_IPTV_ADDON_DATA = "addon_data/pvr.iptvsimple/"
_INSTANCE_XML_RE = re.compile(r"^instance-settings-\d+\.xml$", re.IGNORECASE)
_PROFILE_IPTV_RE = re.compile(
    r"^(profiles/[^/]+/)addon_data/pvr\.iptvsimple/", re.IGNORECASE
)
_PVR_ADDON_ID = "pvr.iptvsimple"


def _jsonrpc(method, params):
    """One JSON-RPC call against the live Kodi; the parsed 'result' or None."""
    try:
        resp = json.loads(
            xbmc.executeJSONRPC(
                json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                )
            )
        )
        return resp.get("result")
    except Exception:
        return None


def _pvr_enabled():
    """True iff pvr.iptvsimple is installed AND enabled; False when it is not
    installed, disabled, or the probe fails (failing toward 'no pause needed' is
    safe: the pause exists only to protect a LIVE client's teardown flush)."""
    res = _jsonrpc(
        "Addons.GetAddonDetails", {"addonid": _PVR_ADDON_ID, "properties": ["enabled"]}
    )
    try:
        return bool(res["addon"]["enabled"])
    except (TypeError, KeyError):
        return False


def _pvr_set_enabled(flag):
    """Enable/disable pvr.iptvsimple via JSON-RPC. True iff Kodi acknowledged."""
    res = _jsonrpc(
        "Addons.SetAddonEnabled", {"addonid": _PVR_ADDON_ID, "enabled": bool(flag)}
    )
    return res == "OK"


def _clear_pvr_pause_marker():
    """Clear the crash-recovery marker once the IPTV client is confirmed enabled."""
    try:
        from resources.lib.modules import tools

        tools.clear_pvr_pause_marker()
    except Exception:
        pass


def _userdata_rel(member, anchor):
    """A zip member -> its userdata-relative path (forward-slash), or None when the
    member does not live under userdata (a home-anchored addons/ or media/ entry)."""
    rel = (member or "").lstrip("/").replace("\\", "/")
    if anchor == "home":
        if not rel.startswith("userdata/"):
            return None
        rel = rel[len("userdata/") :]
    return rel or None


def _iptv_profile_prefixes(namelist, anchor):
    """The userdata-relative profile prefixes under which the ARCHIVE carries
    pvr.iptvsimple addon_data: '' for the top-level profile, 'profiles/<name>/' per
    profile. An empty set means the archive has no IPTV config (no sweep)."""
    prefixes = set()
    for member in namelist:
        rel = _userdata_rel(member, anchor)
        if not rel:
            continue
        if rel.lower().startswith(_IPTV_ADDON_DATA):
            prefixes.add("")
            continue
        m = _PROFILE_IPTV_RE.match(rel)
        if m:
            prefixes.add(m.group(1))
    return prefixes


def _sweep_iptv_instances(namelist, anchor, log=None):
    """Adapter: compute the archive's userdata-relative paths and delegate the IPTV
    stray-instance sweep to nsud.sweep_iptv_instances - the two-layer delete plus
    plist read-back verification live with the rest of the tvOS storage machinery
    (inside the hardware gate's fingerprint), not here. Runs AFTER the extract; see
    restore(). Returns (removed, failed_list); never raises."""
    try:
        rels = [r for r in (_userdata_rel(m, anchor) for m in namelist) if r]
        from resources.lib.modules import nsud

        return nsud.sweep_iptv_instances(control.USERDATA, rels, log=log)
    except Exception as e:
        xbmc.log(
            "%s : IPTV instance sweep failed: %s: %s"
            % (AddonTitle, type(e).__name__, e),
            level=xbmc.LOGERROR,
        )
        return (0, ["sweep aborted (%s)" % type(e).__name__])


def ExtractWithProgress(
    _in, _out, progress, skip_prefix=None, cancelable=True, skip_member=None
):
    """Extract _in over _out with the throttled gauge. Returns an ExtractResult
    carrying the TRUTH of what happened: extracted / skipped / total counts, the
    exact members that failed (with their errors), and the canceled flag - so the
    caller can report honestly instead of assuming the archive's member count."""
    count = 0
    extracted = 0
    skipped = 0
    n_files = 0
    canceled = False
    failed = []  # "member (Error: msg)" for every member that did NOT land
    try:
        zin = zipfile.ZipFile(_in, "r")
        # Defense-in-depth: extract userdata/ before addons/ so an interrupted extract
        # keeps the irreplaceable settings and only loses re-downloadable add-ons.
        ordered = _order_userdata_first(zin.infolist())
        # Never restore the transient temp tree: a full backup includes a partial copy of
        # itself at temp/<backup>.zip, and the restore zip is staged in temp - extracting
        # it would overwrite the source mid-read (Truncated file header). Filter it out up
        # front so the count/percent below is over the files we actually write.
        to_extract = [
            it
            for it in ordered
            if not (skip_prefix and it.filename.startswith(skip_prefix))
            and not (skip_member and skip_member(it.filename))
        ]
        skipped = len(ordered) - len(to_extract)
        n_files = len(to_extract)
        for item in to_extract:
            # Post-wipe this is off (cancelable=False): the box is already wiped, so a
            # cancel here would strand it - the extract must run to completion.
            if cancelable and progress.cancelled():
                canceled = True
                break
            count += 1
            # CRASH FIX (Fire OS 8 sticks): the old code called progress.items() with a
            # changing per-file basename for EVERY file. Thousands of rapid filename
            # text-layout redraws SIGSEGV Kodi's native text renderer (CGUIFont::
            # GetTextWidth <- CGUITextLayout::WrapText <- CGUITextBox::UpdateInfo), which
            # killed a restore around file ~5600 of 6130 - and a wipe-then-restore is
            # unsafe with that crash. Refresh the dialog at most every _UPDATE_EVERY files
            # (plus the first and last), with a SHORT static note - never the per-file
            # basename. The bar still advances in visible steps and always reaches 100%.
            if count == 1 or count % _UPDATE_EVERY == 0 or count == n_files:
                progress.items(
                    count,
                    n_files,
                    note="Extracting file %d of %d" % (count, n_files),
                )
            try:
                zin.extract(item, _out)
                extracted += 1
            except Exception as e:
                failed.append("%s (%s: %s)" % (item.filename, type(e).__name__, e))
    except Exception as e:
        failed.append("archive read failed (%s: %s)" % (type(e).__name__, e))
    xbmc.log(
        "%s : extract to %s -> %d ok, %d failed, %d skipped%s"
        % (
            AddonTitle,
            _out,
            extracted,
            len(failed),
            skipped,
            (" | failed: " + "; ".join(failed)) if failed else "",
        ),
        level=xbmc.LOGERROR if failed else xbmc.LOGINFO,
    )
    return ExtractResult(
        canceled=canceled,
        extracted=extracted,
        skipped=skipped,
        total=n_files,
        failed=failed,
    )
