import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs
import os
import sys
import time
from resources.lib.modules import control, ui
from resources.lib.modules.backtothefuture import PY2
from resources.lib.modules import maintenance

# Explicit submodule imports: a bare `import urllib` does NOT expose
# urllib.parse - the old code only worked because `import requests`
# (now removed) loaded it transitively. Proven live on the Office box:
# AttributeError: module 'urllib' has no attribute 'parse'.
if PY2:
    from urllib import quote_plus

    translatePath = xbmc.translatePath
else:
    from urllib.parse import quote_plus

    translatePath = xbmcvfs.translatePath

AddonID = "script.ezmaintenanceplusplus"

# ICONS FANARTS
ADDON_FANART = control.addonFanart()
ADDON_ICON = control.addonIcon()

# DIRECTORIES
HOME = translatePath("special://home/")

AddonTitle = "EZ Maintenance++"


# ######################### CATEGORIES ################################
def CATEGORIES():
    CreateDir(
        "Fresh Start",
        "url",
        "fresh_start",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "One-Tap Restore",
        "ur",
        "onetap_menu",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "Backup/Restore",
        "ur",
        "backup_restore",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "Set up this box",
        "ur",
        "box_setup",
        ADDON_ICON,
        ADDON_FANART,
        "",
        isFolder=True,
    )
    # CreateDir('Tools','ur','tools',ADDON_ICON,ADDON_FANART,'', isFolder=True)

    CreateDir(
        "Maintenance",
        "ur",
        "maintenance",
        ADDON_ICON,
        ADDON_FANART,
        "",
        isFolder=True,
    )
    CreateDir(
        "Advanced Settings (Buffer Size)",
        "ur",
        "adv_settings",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "Log Viewer/Uploader",
        "ur",
        "log_tools",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "Speedtest",
        "ur",
        "speedtest",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )

    CreateDir(
        "Settings",
        "ur",
        "settings",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )

    # Plain informational version line at the very bottom (non-clickable: the
    # "xxx" action matches no route, so selecting it just returns to the menu).
    # Version is read live from addon.xml so it stays correct on every release.
    CreateDir(
        "%s %s" % (AddonTitle, control.addonInfo("version")),
        "xxx",
        "xxx",
        None,
        ADDON_FANART,
        "",
        isFolder=False,
        iconImage="DefaultIconInfo.png",
    )


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


def BOX_SETUP():
    CreateDir("Set up everything", "url", "setup_all_box", ADDON_ICON, ADDON_FANART, "")
    CreateDir(
        "Add media sources (mini)", "url", "setup_sources", ADDON_ICON, ADDON_FANART, ""
    )
    CreateDir("Set up weather", "url", "setup_weather", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Enable RSS ticker", "url", "setup_rss", ADDON_ICON, ADDON_FANART, "")


# ###########################################################################################
# ###########################################################################################


def FRESHSTART(mode="verbose"):
    # Wipe to a clean Kodi. No skin-swap first (that step used to hang): we wipe, then
    # RESTART, and on restart Kodi falls back to a default skin because the custom one is
    # gone. Uses the same hardened wipe as One-Tap Restore (preserves this add-on, its
    # runtime deps, temp/, and backupdir). mode="silent" wipes with no prompts, no restart.
    if mode != "silent":
        if not ui.confirm_wipe(
            "Wipe this Kodi to a clean state?\n"
            "All add-ons, skins, and settings are removed (this tool survives). "
            "Kodi restarts afterward.",
            heading=AddonTitle,
        ):
            return
    # The wipe is a single step (no per-item progress); the context-managed gauge shows a
    # 'Wiping install...' spinner and is always closed.
    with ui.Progress("Wiping install...", heading=AddonTitle):
        try:
            from resources.lib.modules import onetap

            # keep_addon_db() preserves Kodi's add-on state DB so EZ Maintenance++ comes
            # back ENABLED after the restart (not disabled/"gone", which was the bad UX).
            onetap._wipe(HOME, onetap._wipe_excludes(), onetap.keep_addon_db())
        except Exception:
            pass
        try:
            xbmc.executebuiltin(
                "UpdateLocalAddons"
            )  # reconcile the DB with what's left
        except Exception:
            pass
    if mode != "silent":
        ui.done(
            "Clean slate ready. Kodi will restart now.\n\n"
            "After it restarts, EZ Maintenance++ is under Add-ons > Program add-ons "
            "(if it is off, open it there and choose Enable)."
        )
        ui.restart()


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
    if icon is None or icon == "":
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

# xbmc.log("ezmaintenanceplus: action: %s" % action, level=xbmc.LOGINFO)

if action is None:
    CATEGORIES()
elif action == "settings":
    # Open Kodi's native add-on settings dialog. Every label now resolves through
    # resources/language/.../strings.po, so it renders correctly (the old custom
    # in-app screen was a workaround for a mis-labelled settings.xml, now removed).
    control.openSettings()

elif action == "onetap_menu":
    from resources.lib.modules import onetap

    onetap.menu()

elif action == "fresh_start":
    FRESHSTART()

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

elif action == "box_setup":
    BOX_SETUP()

elif action == "setup_all_box":
    from resources.lib.modules import boxsetup

    boxsetup.setup_all()

elif action == "setup_sources":
    from resources.lib.modules import boxsetup

    boxsetup.add_media_sources()

elif action == "setup_weather":
    from resources.lib.modules import boxsetup

    boxsetup.setup_weather()

elif action == "setup_rss":
    from resources.lib.modules import boxsetup

    boxsetup.enable_rss()

xbmcplugin.endOfDirectory(int(sys.argv[1]))
