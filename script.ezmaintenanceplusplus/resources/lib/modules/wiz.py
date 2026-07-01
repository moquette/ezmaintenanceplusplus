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
import sys
import urllib
import re
import time
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

dp = xbmcgui.DialogProgress()
dialog = xbmcgui.Dialog()
addonInfo = xbmcaddon.Addon().getAddonInfo

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 (KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11"

AddonTitle = "EZ Maintenance++"
AddonID = "script.ezmaintenanceplusplus"


# VfsCopyError now lives in ui.py (one definition for the whole add-on); alias it here so
# `wiz.VfsCopyError` and `except VfsCopyError` keep working. A FAILED ship raises this so
# backup() reports an error and SKIPS rotation (a cancel instead returns canceled=True),
# and the prior good backup is never pruned behind a ship that never landed.
VfsCopyError = ui.VfsCopyError


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


def open_Settings():
    open_Settings = xbmcaddon.Addon(id=AddonID).openSettings()


def ENABLE_ADDONS():
    for root, dirs, files in os.walk(HOME_ADDONS, topdown=True):
        dirs[:] = [d for d in dirs]
        for addon_name in dirs:
            if not any(value in addon_name for value in EXCLUDES_ADDONS):
                # addLink(addon_name,'url',100,ART+'tool.png',FANART,'')
                try:
                    query = (
                        '{"jsonrpc":"2.0", "method":"Addons.SetAddonEnabled","params":{"addonid":"%s","enabled":true}, "id":1}'
                        % (addon_name)
                    )
                    xbmc.executeJSONRPC(query)

                except:
                    pass


def FIX_SPECIAL():

    HOME = translatePath("special://home")
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


def skinswap():

    skin = xbmc.getSkinDir()
    KODIV = get_Kodi_Version()
    skinswapped = 0
    from resources.lib.modules import skinSwitch

    # SWITCH THE SKIN IF THE CURRENT SKIN IS NOT CONFLUENCE
    if skin not in ["skin.confluence", "skin.estuary"]:
        choice = xbmcgui.Dialog().yesno(
            AddonTitle,
            "We can try to reset to the default Kodi Skin..."
            + "\n"
            + "Do you want to Proceed?",
            yeslabel="Yes",
            nolabel="No",
        )
        if choice == 1:
            skin = "skin.estuary" if KODIV >= 17 else "skin.confluence"
            skinSwitch.swapSkins(skin)
            skinswapped = 1
            time.sleep(1)

    # IF A SKIN SWAP HAS HAPPENED CHECK IF AN OK DIALOG (CONFLUENCE INFO SCREEN) IS PRESENT, PRESS OK IF IT IS PRESENT
    if skinswapped == 1:
        if not xbmc.getCondVisibility("Window.isVisible(yesnodialog)"):
            xbmc.executebuiltin("Action(Select)")

    # IF THERE IS NOT A YES NO DIALOG (THE SCREEN ASKING YOU TO SWITCH TO CONFLUENCE) THEN SLEEP UNTIL IT APPEARS
    if skinswapped == 1:
        while not xbmc.getCondVisibility("Window.isVisible(yesnodialog)"):
            time.sleep(1)

    # WHILE THE YES NO DIALOG IS PRESENT PRESS LEFT AND THEN SELECT TO CONFIRM THE SWITCH TO CONFLUENCE.
    if skinswapped == 1:
        while xbmc.getCondVisibility("Window.isVisible(yesnodialog)"):
            xbmc.executebuiltin("Action(Left)")
            xbmc.executebuiltin("Action(Select)")
            time.sleep(1)

    skin = xbmc.getSkinDir()

    # CHECK IF THE SKIN IS NOT CONFLUENCE
    if skin not in ["skin.confluence", "skin.estuary"]:
        choice = xbmcgui.Dialog().yesno(
            AddonTitle,
            "[COLOR lightskyblue][B]ERROR: AUTOSWITCH WAS NOT SUCCESFULL[/B][/COLOR]"
            + "\n"
            + "[COLOR lightskyblue][B]CLICK YES TO MANUALLY SWITCH TO CONFLUENCE NOW[/B][/COLOR]"
            + "\n"
            + "[COLOR lightskyblue][B]YOU CAN PRESS NO AND ATTEMPT THE AUTO SWITCH AGAIN IF YOU WISH[/B][/COLOR]",
            yeslabel="[B][COLOR green]YES[/COLOR][/B]",
            nolabel="[B][COLOR lightskyblue]NO[/COLOR][/B]",
        )
        if choice == 1:
            xbmc.executebuiltin("ActivateWindow(appearancesettings)")
            return
        else:
            sys.exit(1)


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
    backupdir = control.setting("download.path")
    if backupdir == "" or backupdir == None:
        control.infoDialog("Please Setup a Path for Downloads first")
        control.openSettings(query="1.3")
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
    zipFolder = control.setting("restore.path")
    if zipFolder == "" or zipFolder == None:
        control.infoDialog("Please Setup a Zip Files Location first")
        control.openSettings(query="2.0")
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
        restore(links[select])


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


def restore(zipFile, confirm=True, post_wipe=False):
    """Extract a backup zip over special://home and offer a restart.

    post_wipe=True is the One-Tap path: the box has ALREADY been wiped and the snapshot
    ALREADY fully validated by the caller before the wipe. In that mode the extract is a
    single UNINTERRUPTIBLE unit (no cancel) and we NEVER early-return - a wiped box must
    always be driven to the restart prompt, never left silent on a partial/empty extract.
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

    try:
        items = len(zipfile.ZipFile(local).infolist())
    except Exception:
        items = 0

    # Skip the temp/ subtree on extract: the restore zip is staged in special://temp
    # (== special://home/temp), and a full backup contains a partial copy of itself
    # there - extracting it would clobber the source mid-read.
    skip_prefix = None
    try:
        rel = os.path.relpath(
            translatePath("special://temp"), translatePath("special://home/")
        )
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
                control.HOME,
                p,
                skip_prefix=skip_prefix,
                cancelable=not post_wipe,
            )
    finally:
        if staged:
            try:
                os.remove(local)
            except OSError:
                pass

    if canceled and not post_wipe:
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

    ui.ask_restart(
        "Restore Complete: %d items, %d settings applied.\n"
        "Kodi must restart to finish. Restart now?" % (items, applied)
    )


def CreateZip(
    folder, zip_filename, message_header, message1, exclude_dirs, exclude_files
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
        try:
            for dirpath, dirnames, filenames in os.walk(folder):
                if canceled:
                    break
                try:
                    dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
                    filenames[:] = [f for f in filenames if f not in exclude_files]
                    for fname in filenames:
                        if p.cancelled():
                            canceled = True
                            break
                        count += 1
                        p.items(count, n_item, note="[COLOR lime]%s[/COLOR]" % fname)
                        fpath = os.path.normpath(os.path.join(dirpath, fname))
                        zip_file.write(fpath, fpath[len(abs_src) + 1 :])
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
def ExtractZip(_in, _out, progress=None, skip_prefix=None, cancelable=True):
    """Extract _in over _out. `progress` is a ui.Progress (gauge + cancel); None means a
    silent extract. cancelable=False makes the extract UNINTERRUPTIBLE (the post-wipe
    One-Tap path, where a cancel would strand a wiped box)."""
    if progress is not None:
        return ExtractWithProgress(
            _in, _out, progress, skip_prefix=skip_prefix, cancelable=cancelable
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


def ExtractWithProgress(_in, _out, progress, skip_prefix=None, cancelable=True):
    count = 0
    extracted = 0
    errors = 0
    skipped = 0
    canceled = False
    last_error = ""
    try:
        zin = zipfile.ZipFile(_in, "r")
        infos = zin.infolist()
        n_files = len(infos)
        for item in infos:
            # Post-wipe this is off (cancelable=False): the box is already wiped, so a
            # cancel here would strand it - the extract must run to completion.
            if cancelable and progress.cancelled():
                canceled = True
                break
            # Never restore the transient temp tree: a full backup includes a partial
            # copy of itself at temp/<backup>.zip, and the restore zip is staged in temp
            # - extracting it would overwrite the source mid-read (Truncated file header).
            if skip_prefix and item.filename.startswith(skip_prefix):
                skipped += 1
                continue
            count += 1
            try:
                name = os.path.basename(item.filename)
            except Exception:
                name = item.filename
            # The divide-by-zero guard lives inside ui.Progress.items() (n_files is never
            # 0 in the loop body - an empty archive has no items to iterate).
            progress.items(
                count, n_files, note="[COLOR skyblue][B]%s[/B][/COLOR]" % str(name)
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


# INSTALL BUILD
def buildInstaller(url):
    destination = dialog.browse(
        type=0,
        heading="Select Download Directory",
        shares="files",
        useThumbs=True,
        treatAsFolder=True,
        enableMultiple=False,
    )
    if destination:
        dest = translatePath(os.path.join(destination, "custom_build.zip"))
        downloader(url, dest)
        time.sleep(2)
        dp.create("Installing Build", "In Progress..." + "\n" + "Please Wait")
        dp.update(0, "" + "\n" + "Extracting Zip Please Wait")
        ExtractZip(dest, control.HOME)  # buildInstaller is dead (removed in a later PR)
        time.sleep(2)
        dp.close()
        dialog.ok(
            AddonTitle,
            "Installation Complete..."
            + "\n"
            + "Your interface will now be reset"
            + "\n"
            + "Click ok to Start...",
        )
        xbmc.executebuiltin("LoadProfile(Master user)")


# DOWNLOADER

try:
    if PY2:
        FancyURLopener = urllib.FancyURLopener
    else:
        FancyURLopener = urllib.request.FancyURLopener

    class customdownload(FancyURLopener):
        version = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 (KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11"

    def downloader(url, dest, dp=None):
        if not dp:
            dp = xbmcgui.DialogProgress()
            dp.create(AddonTitle)
        dp.update(0)
        start_time = time.time()
        customdownload().retrieve(
            url, dest, lambda nb, bs, fs, url=url: _pbhook(nb, bs, fs, dp, start_time)
        )

    def _pbhook(numblocks, blocksize, filesize, dp, start_time):
        try:
            percent = min(numblocks * blocksize * 100 / filesize, 100)
            currently_downloaded = float(numblocks) * blocksize / (1024 * 1024)
            kbps_speed = numblocks * blocksize / (time.time() - start_time)
            if kbps_speed > 0:
                eta = (filesize - numblocks * blocksize) / kbps_speed
            else:
                eta = 0
            kbps_speed = kbps_speed / 1024
            total = float(filesize) / (1024 * 1024)
            mbs = "%.02f MB of %.02f MB" % (currently_downloaded, total)
            e = "Speed: %.02f Kb/s " % kbps_speed
            e += "ETA: %02d:%02d" % divmod(eta, 60)
            string = "Downloading... Please Wait..."
            dp.update(percent, mbs + "\n" + e + "\n" + string)
        except:
            percent = 100
            dp.update(percent)
            dp.close()
            return

        if dp.iscanceled():
            raise Exception("Canceled")
            dp.close()

except (ImportError, AttributeError):
    import urllib.request

    def downloader(url, dest, dp=None):
        if not dp:
            dp = xbmcgui.DialogProgress()
            dp.create(AddonTitle)
        dp.update(0)
        start_time = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        response = urllib.request.urlopen(req)

        filesize = int(response.getheader("Content-Length", 0))
        downloaded = 0
        blocksize = 8192

        with open(dest, "wb") as f:
            while True:
                chunk = response.read(blocksize)
                if not chunk:
                    break

                f.write(chunk)
                downloaded += len(chunk)

                _pbhook(downloaded, filesize, dp, start_time)

                if dp.iscanceled():
                    dp.close()
                    raise Exception("Canceled")

        dp.close()

    def _pbhook(downloaded, filesize, dp, start_time):
        try:
            percent = int(min(downloaded * 100 / filesize, 100)) if filesize > 0 else 0

            currently_downloaded = downloaded / (1024 * 1024)
            total = filesize / (1024 * 1024) if filesize > 0 else 0

            elapsed = time.time() - start_time
            speed = downloaded / elapsed if elapsed > 0 else 0

            if speed > 0 and filesize > 0:
                eta = (filesize - downloaded) / speed
            else:
                eta = 0

            kbps_speed = speed / 1024

            mbs = "%.02f MB of %.02f MB" % (currently_downloaded, total)
            e = "Speed: %.02f KB/s " % kbps_speed
            e += "ETA: %02d:%02d" % divmod(int(eta), 60)

            string = "Downloading... Please Wait..."

            dp.update(percent, mbs + "\n" + e + "\n" + string)

        except:
            dp.update(100)
            dp.close()

##############################    END    #########################################
