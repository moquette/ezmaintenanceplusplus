import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs
import os
import sys
import urllib
import time
import requests
from resources.lib.modules import control, tools
from resources.lib.modules.backtothefuture import unicode, PY2
from resources.lib.modules import maintenance

if PY2:
    quote_plus = urllib.quote_plus
    translatePath = xbmc.translatePath
else:
    quote_plus = urllib.parse.quote_plus
    translatePath = xbmcvfs.translatePath

AddonID = "script.ezmaintenanceplusplus"
USER_AGENT = "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-GB; rv:1.9.0.3) Gecko/2008092417 Firefox/3.0.3"
selfAddon = xbmcaddon.Addon(id=AddonID)

# ADDON SETTINGS
wizard1 = control.setting("enable_wiz1")
wizard2 = control.setting("enable_wiz2")
wizard3 = control.setting("enable_wiz3")
wizard4 = control.setting("enable_wiz4")
wizard5 = control.setting("enable_wiz5")
backupfull = control.setting("backup_database")
backupaddons = control.setting("backup_addon_data")
backupzip = control.setting("remote_backup")
USB = translatePath(os.path.join(backupzip))

# ICONS FANARTS
ADDON_FANART = control.addonFanart()
ADDON_ICON = control.addonIcon()

# DIRECTORIES
backupdir = translatePath(os.path.join("special://home/backupdir", ""))
packagesdir = translatePath(os.path.join("special://home/addons/packages", ""))
USERDATA = translatePath(os.path.join("special://home/userdata", ""))
ADDON_DATA = translatePath(os.path.join(USERDATA, "addon_data"))
HOME = translatePath("special://home/")
HOME_ADDONS = translatePath("special://home/addons")
backup_zip = translatePath(os.path.join(backupdir, "backup_addon_data.zip"))

# DIALOGS
dialog = xbmcgui.Dialog()
progressDialog = xbmcgui.DialogProgress()

AddonTitle = "EZ Maintenance++"
EXCLUDES = [
    AddonID,
    "backupdir",
    "backup.zip",
    "script.module.requests",
    "script.module.urllib3",
    "script.module.chardet",
    "script.module.idna",
    "script.module.certifi",
]
EXCLUDES_ADDONS = ["notification", "packages"]


def SETTINGS():
    xbmcaddon.Addon(id=AddonID).openSettings()


def ENABLE_WIZARD():
    try:
        query = (
            '{"jsonrpc":"2.0", "method":"Addons.SetAddonEnabled","params":{"addonid":"%s","enabled":true}, "id":1}'
            % (AddonID)
        )
        xbmc.executeJSONRPC(query)

    except:
        pass


# ######################### CATEGORIES ################################
def CATEGORIES():
    CreateDir(
        "[COLOR white][B]FRESH START[/B][/COLOR]",
        "url",
        "fresh_start",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "[COLOR white][B]ONE-TAP RESTORE[/B][/COLOR]",
        "ur",
        "onetap_menu",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "[COLOR white][B]BACKUP/RESTORE[/B][/COLOR]",
        "ur",
        "backup_restore",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    # CreateDir('[COLOR white][B]TOOLS[/B][/COLOR]','ur','tools',ADDON_ICON,ADDON_FANART,'', isFolder=True)

    CreateDir(
        "[COLOR white][B]MAINTENANCE[/B][/COLOR]",
        "ur",
        "maintenance",
        ADDON_ICON,
        ADDON_FANART,
        "",
        isFolder=True,
    )
    CreateDir(
        "[COLOR white][B]ADVANCED SETTINGS (BUFFER SIZE)[/B][/COLOR]",
        "ur",
        "adv_settings",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "[COLOR white][B]LOG VIEWER/UPLOADER[/B][/COLOR]",
        "ur",
        "log_tools",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "[COLOR white][B]SPEEDTEST[/B][/COLOR]",
        "ur",
        "speedtest",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )

    CreateDir(
        "[COLOR white][B]SETTINGS[/B][/COLOR]",
        "ur",
        "settings",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )


def CAT_TOOLS():
    print("NONE YET")


def MAINTENANCE():
    nextAutoCleanup = maintenance.getNextMaintenance()
    if nextAutoCleanup > 0:
        nextAutoCleanup = time.strftime(
            "%a, %d %b %Y %I:%M:%S %p %Z", time.localtime(nextAutoCleanup)
        )
        CreateDir(
            "Next Auto Cleanup: %s" % nextAutoCleanup,
            "xxx",
            "xxx",
            None,
            ADDON_FANART,
            "",
            isFolder=False,
            iconImage="DefaultIconInfo.png",
        )
    CreateDir("Clear Cache", "url", "clear_cache", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Clear Packages", "url", "clear_packages", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Clear Thumbnails", "url", "clear_thumbs", ADDON_ICON, ADDON_FANART, "")


# ###########################################################################################
# ###########################################################################################


def OPEN_URL(url):
    r = requests.get(url).content
    return r


def BUILDS():
    if wizard1 != "false":
        try:
            name = unicode(control.setting("name1"))
            url = unicode(control.setting("url1"))
            img = unicode(control.setting("img1"))
            fanart = unicode(control.setting("img1"))
            CreateDir(
                "[COLOR lime][B][Wizard][/B][/COLOR] " + name,
                url,
                "install_build",
                img,
                fanart,
                "My custom Build",
                isFolder=False,
            )
        except:
            pass
    if wizard2 != "false":
        try:
            name = unicode(selfAddon.getSetting("name2"))
            url = unicode(selfAddon.getSetting("url2"))
            img = unicode(selfAddon.getSetting("img2"))
            fanart = unicode(selfAddon.getSetting("img2"))
            CreateDir(
                "[COLOR skyblue][B][Wizard][/B][/COLOR] " + name,
                url,
                "install_build",
                img,
                fanart,
                "My custom Build",
                isFolder=False,
            )
        except:
            pass
    if wizard3 != "false":
        try:
            name = unicode(selfAddon.getSetting("name3"))
            url = unicode(selfAddon.getSetting("url3"))
            img = unicode(selfAddon.getSetting("img3"))
            fanart = unicode(selfAddon.getSetting("img3"))
            CreateDir(
                "[COLOR cyan][B][Wizard][/B][/COLOR] " + name,
                url,
                "install_build",
                img,
                fanart,
                "My custom Build",
                isFolder=False,
            )
        except:
            pass
    if wizard4 != "false":
        try:
            name = unicode(selfAddon.getSetting("name4"))
            url = unicode(selfAddon.getSetting("url4"))
            img = unicode(selfAddon.getSetting("img4"))
            fanart = unicode(selfAddon.getSetting("img4"))
            CreateDir(
                "[COLOR yellow][B][Wizard][/B][/COLOR] " + name,
                url,
                "install_build",
                img,
                fanart,
                "My custom Build",
                isFolder=False,
            )
        except:
            pass
    if wizard5 != "false":
        try:
            name = unicode(selfAddon.getSetting("name5"))
            url = unicode(selfAddon.getSetting("url5"))
            img = unicode(selfAddon.getSetting("img5"))
            fanart = unicode(selfAddon.getSetting("img5"))
            CreateDir(
                "[COLOR purple][B][Wizard][/B][/COLOR] " + name,
                url,
                "install_build",
                img,
                fanart,
                "My custom Build",
                isFolder=False,
            )
        except:
            pass

    # Empty-state: if no wizard is enabled the page would be blank (reads as broken).
    # Show one non-build row that opens settings so it's clearly empty-on-purpose.
    if all(w == "false" for w in (wizard1, wizard2, wizard3, wizard4, wizard5)):
        CreateDir(
            "[COLOR grey]No builds yet - add one in Settings > Wizard Creator[/COLOR]",
            "",
            "settings",
            "",
            "",
            "Open settings to enable a wizard and set its Name and Zip Url.",
            isFolder=False,
        )


def FRESHSTART(mode="verbose"):
    # Wipe to a clean Kodi. No skin-swap first (that step used to hang): we wipe, then
    # RESTART, and on restart Kodi falls back to a default skin because the custom one is
    # gone. Uses the same hardened wipe as One-Tap Restore (preserves this add-on, its
    # runtime deps, temp/, and backupdir). mode="silent" wipes with no prompts, no restart.
    if mode != "silent":
        if not xbmcgui.Dialog().yesno(
            AddonTitle,
            "Wipe this Kodi to a clean state?\n"
            "All add-ons, skins, and settings are removed (this tool survives). "
            "Kodi restarts afterward.",
            yeslabel="Wipe",
            nolabel="Cancel",
        ):
            return
    try:
        progressDialog.create(AddonTitle, "Wiping install..." + "\n" + "Please wait")
    except Exception:
        pass
    try:
        from resources.lib.modules import onetap

        # keep_addon_db() preserves Kodi's add-on state DB so EZ Maintenance++ comes back
        # ENABLED after the restart (not disabled/"gone", which was the bad UX).
        onetap._wipe(HOME, onetap._wipe_excludes(), onetap.keep_addon_db())
    except Exception:
        pass
    try:
        xbmc.executebuiltin("UpdateLocalAddons")  # reconcile the DB with what's left
    except Exception:
        pass
    try:
        progressDialog.close()
    except Exception:
        pass
    if mode != "silent":
        dialog.ok(
            AddonTitle,
            "Clean slate ready. Kodi will restart now.\n\n"
            "After it restarts, EZ Maintenance++ is under Add-ons > Program add-ons "
            "(if it is off, open it there and choose Enable).",
        )
        xbmc.executebuiltin("Quit")


def REMOVE_EMPTY_FOLDERS():
    # initialize the counters
    print("########### Start Removing Empty Folders #########")
    empty_count = 0
    used_count = 0
    for curdir, subdirs, files in os.walk(HOME):
        try:
            if (
                len(subdirs) == 0 and len(files) == 0
            ):  # check for empty directories. len(files) == 0 may be overkill
                empty_count += 1  # increment empty_count
                os.rmdir(curdir)  # delete the directory
                print("successfully removed: " + curdir)
            elif len(subdirs) > 0 and len(files) > 0:  # check for used directories
                used_count += 1  # increment used_count
        except:
            pass


def killxbmc():
    dialog.ok(
        "PROCESS COMPLETE",
        "The skin will now be reset"
        + "\n"
        + "To start using your new setup please switch the skin System > Appearance > Skin to the desired one... if images are not showing, just restart Kodi"
        + "\n"
        + "Click OK to Continue",
    )

    # xbmc.executebuiltin('Mastermode')
    xbmc.executebuiltin("LoadProfile(Master user)")
    # xbmc.executebuiltin('Mastermode')


def CreateDir(
    name,
    url,
    action,
    icon,
    fanart,
    description,
    isFolder=False,
    iconImage="DefaultFolder.png",
):
    if icon == None or icon == "":
        icon = ADDON_ICON
    u = (
        sys.argv[0]
        + "?url="
        + quote_plus(url)
        + "&action="
        + str(action)
        + "&name="
        + quote_plus(name)
        + "&icon="
        + quote_plus(icon)
        + "&fanart="
        + quote_plus(fanart)
        + "&description="
        + quote_plus(description)
    )
    ok = True
    if PY2:
        liz = xbmcgui.ListItem(name, iconImage=iconImage, thumbnailImage=icon)
    else:
        liz = xbmcgui.ListItem(name)
        liz.setArt({"icon": iconImage})
        liz.setArt({"thumbnailImage": icon})
    liz.setInfo(type="Video", infoLabels={"Title": name, "Plot": description})
    liz.setProperty("Fanart_Image", fanart)
    ok = xbmcplugin.addDirectoryItem(
        handle=int(sys.argv[1]), url=u, listitem=liz, isFolder=isFolder
    )
    return ok


def _dbtest(dropbox_remote):
    # Hidden on-device smoke test: upload -> list -> download -> delete a tiny file.
    # Logs EZPP_DBTEST lines. Only works once a Dropbox refresh token exists.
    import time as _time

    name = "ezpp_dbtest_%s.zip" % _time.strftime("%Y%m%d%H%M%S")
    local = translatePath("special://temp/" + name)
    try:
        with open(local, "wb") as fh:
            fh.write(b"EZPP dbtest payload")
        xbmc.log("EZPP_DBTEST start name=%s" % name, level=xbmc.LOGINFO)
        dropbox_remote.upload(local, name)
        xbmc.log("EZPP_DBTEST upload OK", level=xbmc.LOGINFO)
        listing = dropbox_remote.list_backups()
        xbmc.log(
            "EZPP_DBTEST list found=%s present=%s" % (len(listing), name in listing),
            level=xbmc.LOGINFO,
        )
        got = dropbox_remote.download(name)
        size = os.path.getsize(translatePath(got))
        xbmc.log("EZPP_DBTEST download OK bytes=%s" % size, level=xbmc.LOGINFO)
        dropbox_remote.delete(name)
        xbmc.log("EZPP_DBTEST delete OK", level=xbmc.LOGINFO)
        xbmc.log("EZPP_DBTEST PASS", level=xbmc.LOGINFO)
    except Exception as e:
        xbmc.log("EZPP_DBTEST FAIL %s: %s" % (type(e).__name__, e), level=xbmc.LOGERROR)
    finally:
        try:
            os.remove(local)
        except Exception:
            pass


if PY2:
    from urlparse import parse_qsl
else:
    from urllib.parse import parse_qsl

# RunScript(script.ezmaintenanceplusplus,authorize) / (...,dbtest) arrive as a bare
# positional arg in sys.argv[1], NOT as the plugin "?action=" querystring. Route those
# first and exit, before the normal plugin parsing (which assumes sys.argv[2] is a qs).
_script_arg = sys.argv[1] if len(sys.argv) > 1 else ""
if _script_arg in ("authorize", "dbtest"):
    from resources.lib.modules import dropbox_remote

    if _script_arg == "authorize":
        dropbox_remote.authorize()
    else:
        _dbtest(dropbox_remote)
    sys.exit(0)

# One-Tap Restore config actions from the settings tab:
#   RunScript(script.ezmaintenanceplusplus,onetap,<pick|verify>,<slot>)
if _script_arg == "onetap":
    from resources.lib.modules import onetap

    _verb = sys.argv[2] if len(sys.argv) > 2 else ""
    _slot = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 0
    if _slot:
        if _verb == "pick":
            onetap.pick(_slot)
        elif _verb == "verify":
            onetap.verify(_slot)
        elif _verb == "apply":
            onetap.apply(_slot)
    sys.exit(0)

params = dict(parse_qsl(sys.argv[2].replace("?", "")))
action = params.get("action")

icon = params.get("icon")

name = params.get("name")

title = params.get("title")

year = params.get("year")

fanart = params.get("fanart")

tvdb = params.get("tvdb")

tmdb = params.get("tmdb")

season = params.get("season")

episode = params.get("episode")

tvshowtitle = params.get("tvshowtitle")

premiered = params.get("premiered")

url = params.get("url")

image = params.get("image")

meta = params.get("meta")

select = params.get("select")

query = params.get("query")

description = params.get("description")

content = params.get("content")

# xbmc.log("ezmaintenanceplus: action: %s" % action, level=xbmc.LOGINFO)

if action == None:
    CATEGORIES()
elif action == "settings":
    control.openSettings()

elif action == "onetap_menu":
    from resources.lib.modules import onetap

    onetap.menu()

elif action == "fresh_start":
    FRESHSTART()

elif action == "builds":
    BUILDS()
elif action == "tools":
    CAT_TOOLS()
elif action == "maintenance":
    MAINTENANCE()

elif action == "adv_settings":
    from resources.lib.modules import tools

    tools.advancedSettings()

elif action == "clear_cache":
    from resources.lib.modules import maintenance

    maintenance.clearCache()

elif action == "log_tools":
    from resources.lib.modules import logviewer

    logviewer.logView()


elif action == "clear_packages":
    from resources.lib.modules import maintenance

    maintenance.purgePackages()
elif action == "clear_thumbs":
    from resources.lib.modules import maintenance

    maintenance.deleteThumbnails()

elif action == "backup_restore":
    from resources.lib.modules import wiz

    typeOfBackup = ["BACKUP", "RESTORE"]
    s_type = control.selectDialog(typeOfBackup)
    if s_type == 0:
        modes = ["Full Backup", "Addons Settings"]
        select = control.selectDialog(modes)
        if select == 0:
            wiz.backup(mode="full")
        elif select == 1:
            wiz.backup(mode="userdata")
    elif s_type == 1:
        wiz.restoreFolder()

elif action == "install_build":
    from resources.lib.modules import wiz

    wiz.skinswap()
    yesDialog = dialog.yesno(
        AddonTitle,
        "Do you want to perform a Fresh Start before Installing your Build?",
        yeslabel="Yes",
        nolabel="No",
    )
    if yesDialog:
        FRESHSTART(mode="silent")

    wiz.buildInstaller(url)

elif action == "speedtest":
    xbmc.executebuiltin(
        'Runscript("special://home/addons/script.ezmaintenanceplusplus/resources/lib/modules/speedtest.py")'
    )

elif action == "authorize":
    # Also reachable as a plugin action (the Settings button uses RunScript -> the
    # sys.argv[1] guard above; this elif covers the plugin:// querystring path).
    from resources.lib.modules import dropbox_remote

    dropbox_remote.authorize()

elif action == "dbtest":
    from resources.lib.modules import dropbox_remote

    _dbtest(dropbox_remote)

xbmcplugin.endOfDirectory(int(sys.argv[1]))
