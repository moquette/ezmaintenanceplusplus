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
from resources.lib.modules import control, maintenance, tools
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


class VfsCopyError(Exception):
    """Raised when shipping the finished zip to a VFS destination fails.

    Lets backup() tell a FAILED copy apart from a user CANCEL: a cancel returns
    canceled=True (delete temp, no rotation), while a copy failure raises this
    so backup() can report an error and SKIP rotation - the prior good backup
    is never pruned behind a ship that never landed.
    """


# Backup filenames carry a trailing _YYYYMMDDHHMM stamp before ".zip".
_STAMP_RE = re.compile(r"_(\d{12})\.zip$", re.IGNORECASE)


def _name_stamp(name):
    """Parse the trailing _YYYYMMDDHHMM stamp from a backup filename.

    Returns the 12-digit string (lexically == chronologically sortable) or ""
    when absent, so an unstamped file sorts as the OLDEST and can never cause a
    newer, stamped file to be deleted.
    """
    m = _STAMP_RE.search(name or "")
    return m.group(1) if m else ""


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
        _rotate_vfs(backupdir)
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
        dp2 = xbmcgui.DialogProgress()
        dp2.create(AddonTitle, "Uploading to Dropbox...")
        dp2.update(
            0, "Uploading to Dropbox...\nStarting..."
        )  # avoid Kodi's default 100% bar

        def _upload_progress(sent, total):
            mb = 1024 * 1024
            pct = int(sent * 100 / total) if total else 0
            dp2.update(
                pct, "Uploading to Dropbox...\n%d of %d MB" % (sent // mb, total // mb)
            )
            return not dp2.iscanceled()

        try:
            dropbox_remote.upload(staged, name, progress=_upload_progress)
            dp2.close()
        except dropbox_remote.DropboxCanceled:
            try:
                dp2.close()
            except:
                pass
            dialog.ok(
                AddonTitle, "Backup canceled. Your previous backup was not touched."
            )
            return
        except Exception as e:
            try:
                dp2.close()
            except:
                pass
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
        _rotate_dropbox(dropbox_remote)
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


def _rotate_vfs(backupdir):
    # Destination-agnostic keep-N for Local/Network: delete oldest .zip beyond N.
    n = _keep_n()
    if n <= 0:
        return
    try:
        _dirs, files = xbmcvfs.listdir(backupdir)
        # Sort OLDEST first by the in-name _YYYYMMDDHHMM stamp, NOT raw filename
        # (users name their backups, so a lexical name sort could rank an older
        # file last and delete the newest). An unstamped file has stamp "" and
        # sorts oldest, so it is pruned before any stamped, newer backup.
        zips = sorted(
            [f for f in files if f.endswith(".zip")],
            key=lambda f: (_name_stamp(f), f),
        )
        for old in zips[: max(0, len(zips) - n)]:
            try:
                xbmcvfs.delete(translatePath(os.path.join(backupdir, old)))
            except:
                pass
    except Exception as e:
        xbmc.log(
            "%s : backup rotation skipped: %s" % (AddonTitle, type(e).__name__),
            level=xbmc.LOGWARNING,
        )


def _rotate_dropbox(dropbox_remote):
    # Destination-agnostic keep-N for Dropbox: delete oldest beyond N.
    n = _keep_n()
    if n <= 0:
        return
    try:
        names = dropbox_remote.list_backups()  # newest-first
        for old in names[n:]:
            try:
                dropbox_remote.delete(old)
            except:
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
        dp2 = xbmcgui.DialogProgress()
        dp2.create(AddonTitle, "Downloading from Dropbox...")
        dp2.update(0, "Downloading from Dropbox...\nStarting...")

        def _dl_progress(received, total):
            mb = 1024 * 1024
            pct = int(received * 100 / total) if total else 0
            dp2.update(
                pct,
                "Downloading from Dropbox...\n%d of %d MB"
                % (received // mb, total // mb),
            )

        special = dropbox_remote.download(chosen, progress=_dl_progress)
        dp2.close()
        local = translatePath(special)
    except Exception as e:
        try:
            dp2.close()
        except:
            pass
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


def restore(zipFile):
    yesDialog = dialog.yesno(
        AddonTitle,
        "This will overwrite all your current settings ... Are you sure?",
        yeslabel="Yes",
        nolabel="No",
    )
    if not yesDialog:
        return

    # Stage a remote (VFS) zip locally first; a plain local path extracts directly.
    local = zipFile
    staged = False
    if "://" in zipFile:
        local = translatePath(os.path.join("special://temp", os.path.basename(zipFile)))
        try:
            xbmcvfs.copy(zipFile, local)
            staged = True
        except Exception:
            pass

    # Validate BEFORE extracting so we never report success on a missing / empty / bad
    # zip (the false-success the QA pass killed on the backup side, now on restore too).
    # The byte count in the failure message also diagnoses a download that came up short.
    try:
        size = os.path.getsize(local)
    except OSError:
        size = 0
    if size == 0 or not zipfile.is_zipfile(local):
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

    try:
        dp = xbmcgui.DialogProgress()
        dp.create("Restoring File", "In Progress..." + "\n" + "Please Wait")
        dp.update(0, "" + "\n" + "Extracting Zip Please Wait")
        canceled = ExtractZip(local, control.HOME, dp, skip_prefix=skip_prefix)
    finally:
        if staged:
            try:
                os.remove(local)
            except OSError:
                pass

    if canceled:
        dialog.ok(AddonTitle, "Restore Canceled")
        return

    # Make the restore actually take effect on every platform - critically on tvOS,
    # where Kodi mirrors guisettings.xml in NSUserDefaults and would otherwise revert a
    # file-only restore. Mirror the official Backup add-on: re-apply settings through
    # the JSON-RPC API and rescan add-ons, then exit cleanly (Quit, not ShutDown) so the
    # applied settings flush.
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

    if dialog.yesno(
        AddonTitle,
        "Restore Complete: %d items, %d settings applied.\n"
        "Kodi must restart to finish. Restart now?" % (items, applied),
        yeslabel="Restart",
        nolabel="Later",
    ):
        xbmc.executebuiltin("Quit")


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
    for_progress = []
    ITEM = []
    dp = xbmcgui.DialogProgress()
    dp.create(message_header, message1)
    try:
        os.remove(zip_filename)
    except:
        pass
    for base, dirs, files in os.walk(folder):
        for file in files:
            ITEM.append(file)
    N_ITEM = len(ITEM)
    count = 0
    canceled = False
    zip_file = zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
    for dirpath, dirnames, filenames in os.walk(folder):
        if canceled:
            break
        try:
            dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
            filenames[:] = [f for f in filenames if f not in exclude_files]

            for file in filenames:
                if dp.iscanceled():
                    canceled = True
                    break
                count += 1
                for_progress.append(file)
                progress = len(for_progress) / float(N_ITEM) * 100
                if PY2:
                    dp.update(
                        int(progress),
                        "Backing Up",
                        "FILES: "
                        + str(count)
                        + "/"
                        + str(N_ITEM)
                        + "   [COLOR lime]"
                        + str(file)
                        + "[/COLOR]",
                        "Please Wait",
                    )
                else:
                    dp.update(
                        int(progress),
                        "Backing Up"
                        + "\n"
                        + "FILES: "
                        + str(count)
                        + "/"
                        + str(N_ITEM)
                        + "   [COLOR lime]"
                        + str(file)
                        + "[/COLOR]"
                        + "\n"
                        + "Please Wait",
                    )
                file = os.path.join(dirpath, file)
                file = os.path.normpath(file)
                arcname = file[len(abs_src) + 1 :]
                zip_file.write(file, arcname)
        except:
            pass
    zip_file.close()

    # EZ Maintenance++ : if the destination was a VFS path, the zip was built in
    # special://temp - ship the finished file to the share/cloud, then drop the temp.
    copy_ok = True
    if remote:
        if not canceled:
            # xbmcvfs.copy returns False on failure (share offline, no space,
            # permission). Capture it - a discarded False let a failed ship look
            # like success, so backup() rotated and pruned a good old backup.
            copy_ok = bool(xbmcvfs.copy(zip_filename, target))
        try:
            os.remove(zip_filename)
        except:
            pass
        if not canceled and not copy_ok:
            raise VfsCopyError("VFS copy to %s failed" % target)

    return canceled


# EXTRACT ZIP
def ExtractZip(_in, _out, dp=None, skip_prefix=None):
    if dp:
        return ExtractWithProgress(_in, _out, dp, skip_prefix=skip_prefix)
    return ExtractNOProgress(_in, _out)


def ExtractNOProgress(_in, _out):
    canceled = False

    try:
        zin = zipfile.ZipFile(_in, "r")
        zin.extractall(_out)
    except Exception as e:
        print(str(e))
    return canceled


def ExtractWithProgress(_in, _out, dp, skip_prefix=None):
    count = 0
    extracted = 0
    errors = 0
    skipped = 0
    canceled = False
    last_error = ""
    try:
        zin = zipfile.ZipFile(_in, "r")
        nFiles = float(len(zin.infolist())) or 1.0
        for item in zin.infolist():
            canceled = dp.iscanceled()
            if canceled:
                break
            # Never restore the transient temp tree: a full backup includes a partial
            # copy of itself at temp/<backup>.zip, and the restore zip is staged in temp
            # - extracting it would overwrite the source mid-read (Truncated file header).
            if skip_prefix and item.filename.startswith(skip_prefix):
                skipped += 1
                continue
            count += 1
            update = count / nFiles * 100
            try:
                name = os.path.basename(item.filename)
            except:
                name = item.filename
            label = "[COLOR skyblue][B]%s[/B][/COLOR]" % str(name)
            if PY2:
                dp.update(
                    int(update), "Extracting... Errors:  " + str(errors), label, ""
                )
            else:
                dp.update(
                    int(update), "Extracting... Errors:  " + str(errors) + "\n" + label
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
        ExtractZip(dest, control.HOME, dp)
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
