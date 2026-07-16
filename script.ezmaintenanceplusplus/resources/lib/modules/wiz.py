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

    exclude_dirs = ["temp"]  # never back up special://home/temp (transient + self-ref)
    try:
        canceled = CreateZip(
            BACKUPDATA,
            backup_zip,
            "Creating Backup",
            "Backing up files",
            exclude_dirs,
            exclude_database,
            prune_home_root=(mode == "full"),
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
    if canceled:
        try:
            xbmcvfs.delete(backup_zip)  # VFS-safe: handles nfs:// and local
        except:
            pass
        dialog.ok(AddonTitle, "Backup canceled")
    else:
        # Only rotate AFTER a confirmed-landed backup; the new zip is sacred until then.
        _rotate_vfs(backupdir, protect={name})
        dialog.ok(AddonTitle, "Backup complete")


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
    exclude_dirs = ["temp"]  # never back up special://home/temp (transient + self-ref)
    exclude_database = [".pyo", ".log"]
    canceled = CreateZip(
        BACKUPDATA,
        translatePath(staged),
        "Creating Backup",
        "Backing up files",
        exclude_dirs,
        exclude_database,
        prune_home_root=(mode == "full"),
    )
    try:
        if canceled:
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
        # Confirmed landed -> safe to rotate the Dropbox folder.
        _rotate_dropbox(dropbox_remote, protect={name})
        dialog.ok(AddonTitle, "Backup complete (Dropbox)")
    finally:
        xbmc.executebuiltin("InhibitIdleShutdown(false)")
        try:
            os.remove(translatePath(staged))
        except:
            pass


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
            onetap._wipe(
                translatePath("special://home/"),
                onetap._wipe_excludes(),
                progress=lambda done, total: wp.items(
                    done, total, note="Removing old files"
                ),
            )
        # From here on this IS a post-wipe restore: uninterruptible, never early-returns.
        post_wipe = True

    def _rlog(m):
        xbmc.log("%s : %s" % (AddonTitle, m), xbmc.LOGINFO)

    # A restore does NOT touch the IPTV client (pvr): it is never disabled, enabled, or
    # staged here. EZ Maintenance++ has zero IPTV behavior - the user manages IPTV
    # themselves. The extract below only puts the backed-up files in the right folder.

    # A backup zip is either HOME-anchored (full: members under userdata/ + addons/) or
    # USERDATA-anchored (a "kodi_settings" backup: bare userdata contents, NO userdata/
    # prefix). Extract to the MATCHING root - extracting a userdata-anchored zip to HOME is
    # exactly the bug that scattered settings into the home root and bricked the box.
    try:
        _names = zipfile.ZipFile(local).namelist()
    except Exception:
        _names = []
    items = len(_names)
    anchor = _archive_anchor(_names, hint=anchor_hint)
    extract_root = control.HOME if anchor == "home" else control.USERDATA
    _rlog(
        "restore: anchor=%s -> extract_root=%s (%d members)"
        % (anchor, extract_root, items)
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
    canceled = False
    try:
        with ui.Progress("Extracting the backup...", heading="Restoring") as p:
            canceled = ExtractWithProgress(
                local,
                extract_root,
                p,
                skip_prefix=skip_prefix,
                cancelable=not post_wipe,
                skip_member=_extract_skip(anchor, skip_prefix),
            )
    finally:
        if staged:
            try:
                os.remove(local)
            except OSError:
                pass

    if canceled and not post_wipe:
        # The user canceled a (non-wipe) restore that never committed. Nothing was changed on
        # disk except what the (now-stopped) extract wrote, and no client state was touched, so
        # a cancel is a true no-op.
        dialog.ok(AddonTitle, "Restore Canceled")
        return

    # Make the restore actually take effect on every platform - critically on tvOS,
    # where Kodi mirrors guisettings.xml in NSUserDefaults and would otherwise revert a
    # file-only restore. Mirror the official Backup add-on: re-apply settings through
    # the JSON-RPC API and rescan add-ons, then offer the (unified) restart.
    applied = 0
    try:
        from resources.lib.modules import _kodisettings

        applied = _kodisettings.apply_guisettings(
            os.path.join(control.USERDATA, "guisettings.xml")
        )
    except Exception:
        pass
    try:
        xbmc.executebuiltin("UpdateLocalAddons")
    except Exception:
        pass

    # tvOS durability: the extract wrote userdata/*.xml with plain POSIX I/O, which on Apple
    # TV BYPASSES Kodi's CTVOSFile VFS - so the restored settings never reach NSUserDefaults
    # (tvOS's only persistent store) and are shadowed by the stale mirror at boot. Re-write
    # each restored userdata/*.xml THROUGH xbmcvfs so tvOS vectors it into NSUserDefaults
    # (durable on the first reopen, no clean shutdown needed). This is a generic settings
    # durability rewrite - it does NOT enable, disable, or stage the IPTV client (EZ
    # Maintenance++ has no IPTV behavior). Runs AFTER apply_guisettings/UpdateLocalAddons (so
    # nothing re-saves defaults over it) and BEFORE the restart prompt. Fully guarded - never
    # breaks a restore.
    try:
        from resources.lib.modules import nsud

        nsud.rewrite_userdata_xml(control.USERDATA, log=_rlog)
    except Exception:
        pass

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

    ui.ask_restart(
        "Restore Complete: %d items, %d settings applied." % (items, applied)
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
    # ONE uniform gauge (percent + count). The divide-by-zero guard lives INSIDE
    # ui.Progress.items(), so an empty backup folder (0 files) no longer raises, and the
    # context manager guarantees the dialog is closed - the old DialogProgress leaked.
    with ui.Progress(message1, heading=message_header) as p:
        ITEM = []
        for _base, _dirs, files in os.walk(folder):
            ITEM.extend(files)
        n_item = len(ITEM)
        count = 0
        zip_file = zipfile.ZipFile(
            zip_filename, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        )
        written_arcs = set()  # arcnames the POSIX walk captured (for the tvOS augment)
        try:
            for dirpath, dirnames, filenames in os.walk(folder):
                if canceled:
                    break
                try:
                    dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
                    filenames[:] = [f for f in filenames if f not in exclude_files]
                    # A FULL backup (folder == special://home) must capture ONLY the allowed
                    # home-level dirs. Stray root userdata pollution (loose guisettings.xml or
                    # a sibling addon_data/ left by the old userdata-restore-to-HOME bug) would
                    # otherwise be re-captured and re-scattered on the next restore. Depth-scoped
                    # to the walk ROOT so the real userdata/addon_data/... is untouched. Never
                    # set for userdata/dropbox mode, where those names ARE the content.
                    if prune_home_root and os.path.normpath(dirpath) == abs_src:
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
                        zip_file.write(fpath, arc)
                        written_arcs.add(arc)
                except Exception:
                    pass
            # tvOS completeness: the POSIX walk above cannot see the userdata *.xml that live
            # only in NSUserDefaults (guisettings.xml, profiles.xml, addon settings, ...), so
            # on Apple TV the backup would silently omit exactly the
            # settings the owner cares about. Capture them by reading Kodi's NSUserDefaults
            # plist directly and add any the walk missed. Additive + idempotent - a pure no-op
            # on Fire TV / desktop (no such plist). Guarded; never breaks a backup. See nsub.py
            # (and why xbmcvfs reads cannot be used) + docs/plans/atv-restore-*.
            if not canceled:
                try:
                    from resources.lib.modules import nsub

                    def _blog(m):
                        xbmc.log("%s : %s" % (AddonTitle, m), xbmc.LOGINFO)

                    nsub.capture_nsud_userdata(
                        zip_file, abs_src, written_arcs, log=_blog
                    )
                except Exception:
                    pass
        finally:
            zip_file.close()

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
                    result = ui.copy_with_progress(zip_filename, target, progress=sp)
                if result == ui.COPY_CANCELLED:
                    canceled = True
        finally:
            try:
                os.remove(zip_filename)
            except Exception:
                pass

    return canceled


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
    canceled = False

    try:
        zin = zipfile.ZipFile(_in, "r")
        zin.extractall(_out)
    except Exception as e:
        print(str(e))
    return canceled


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


def ExtractWithProgress(
    _in, _out, progress, skip_prefix=None, cancelable=True, skip_member=None
):
    count = 0
    extracted = 0
    errors = 0
    skipped = 0
    canceled = False
    last_error = ""
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
                errors += 1
                last_error = "%s: %s" % (type(e).__name__, e)
    except Exception as e:
        last_error = "%s: %s" % (type(e).__name__, e)
    xbmc.log(
        "%s : extract to %s -> %d ok, %d errors, %d skipped%s"
        % (
            AddonTitle,
            _out,
            extracted,
            errors,
            skipped,
            (" | last: " + last_error) if last_error else "",
        ),
        level=xbmc.LOGERROR if errors else xbmc.LOGINFO,
    )
    return canceled
