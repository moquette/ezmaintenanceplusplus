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

    exclude_dirs = [""]
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

    # Build the zip LOCALLY in special://temp (no "://" so CreateZip skips its VFS copy
    # branch), then ship it to Dropbox. The prior remote backup stays untouched unless
    # this upload confirms, so a failed run never destroys the last good backup.
    staged = "special://temp/" + name
    exclude_dirs = [""]
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
            dp2 = xbmcgui.DialogProgress()
            dp2.create(AddonTitle, "Uploading to Dropbox..." + "\n" + "Please Wait")
            dropbox_remote.upload(staged, name)
            dp2.close()
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
        dp2.create(AddonTitle, "Downloading from Dropbox..." + "\n" + "Please Wait")
        special = dropbox_remote.download(chosen)  # special://temp/<name>
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
    if yesDialog:
        try:
            dp = xbmcgui.DialogProgress()
            dp.create("Restoring File", "In Progress..." + "\n" + "Please Wait")
            dp.update(0, "" + "\n" + "Extracting Zip Please Wait")
            local = zipFile
            if "://" in zipFile:  # remote zip: stage it local before extracting
                local = translatePath(
                    os.path.join("special://temp", os.path.basename(zipFile))
                )
                xbmcvfs.copy(zipFile, local)
            canceled = ExtractZip(local, control.HOME, dp)
            if "://" in zipFile:
                try:
                    os.remove(local)
                except:
                    pass
            if canceled:
                dialog.ok(AddonTitle, "Restore Canceled")
            else:
                dialog.ok(AddonTitle, "Restore Complete")
            xbmc.executebuiltin("ShutDown")
        except:
            pass


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
def ExtractZip(_in, _out, dp=None):
    if dp:
        return ExtractWithProgress(_in, _out, dp)
    return ExtractNOProgress(_in, _out)


def ExtractNOProgress(_in, _out):
    canceled = False

    try:
        zin = zipfile.ZipFile(_in, "r")
        zin.extractall(_out)
    except Exception as e:
        print(str(e))
    return canceled


def ExtractWithProgress(_in, _out, dp):
    zin = zipfile.ZipFile(_in, "r")
    nFiles = float(len(zin.infolist()))
    count = 0
    errors = 0
    canceled = False
    try:
        for item in zin.infolist():
            canceled = dp.iscanceled()
            if canceled:
                break
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
            except Exception as e:
                print("EXTRACTING ERRORS", e)
                pass

    except Exception as e:
        print(str(e))
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
