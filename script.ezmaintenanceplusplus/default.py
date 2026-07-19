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
    CreateDir(
        "Tools",
        "ur",
        "tools",
        ADDON_ICON,
        ADDON_FANART,
        "",
        isFolder=True,
    )

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
        "Video Cache Buffer",
        "ur",
        "adv_settings",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )
    CreateDir(
        "Device Name",
        "ur",
        "device_name",
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
    CreateDir("Clear All", "url", "clear_all", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Clear Cache", "url", "clear_cache", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Clear Packages", "url", "clear_packages", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Clear Thumbnails", "url", "clear_thumbs", ADDON_ICON, ADDON_FANART, "")
    CreateDir(
        "Clear Recently Played Channels",
        "url",
        "clear_channels",
        ADDON_ICON,
        ADDON_FANART,
        "",
    )


def BOX_SETUP():
    CreateDir("Set up everything", "url", "setup_all_box", ADDON_ICON, ADDON_FANART, "")
    CreateDir(
        "Add media sources (mini)", "url", "setup_sources", ADDON_ICON, ADDON_FANART, ""
    )
    CreateDir("Set up weather", "url", "setup_weather", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Enable RSS ticker", "url", "setup_rss", ADDON_ICON, ADDON_FANART, "")


def TOOLS():
    # "Purge stale tvOS keys" was REMOVED (2026.07.19.5). Three clearers already
    # run the same nsud.purge_stale_keys automatically - inside every restore
    # (wiz.py, both the wipe and merge paths), once per add-on version at boot
    # (service.py), and the two-layer wipe's own key pass (onetap.py) - so the
    # menu item covered no case the box does not already handle. What it did do
    # was ask a non-technical owner to know she had restored a 2026.07.08-13 era
    # archive onto an Apple TV, which nobody knows about themselves. The purge
    # itself is untouched; only this manual entry point is gone.
    CreateDir(
        "Verify backup archive",
        "url",
        "verify_backup_archive",
        ADDON_ICON,
        ADDON_FANART,
        "Read-only check of a backup zip: entry count, manifest, failed list, "
        "IPTV data, top-level layout. Restores nothing.",
    )


# ###########################################################################################
# ##################################### OWNER TOOLS #########################################


# The manifest wiz.backup embeds ({"created","source_os","entries","failed":[...]}).
BACKUP_MANIFEST_NAME = "backup_manifest.json"
# Any entry under this addon_data path means the archive carries IPTV client state,
# whether the zip is anchored at home/ ("userdata/addon_data/...") or at userdata/.
IPTV_ADDON_DATA_MARKER = "addon_data/pvr.iptvsimple/"


def analyze_backup_zip(zip_path):
    """Read-only analysis of a backup zip (never extracts, never restores).

    Returns a dict:
      total_entries     - int, every member in the archive
      manifest_present  - bool, backup_manifest.json anywhere in the archive
      manifest_failed   - list[str], the manifest's "failed" list ([] if absent)
      iptv_present      - bool, any addon_data/pvr.iptvsimple/ entry
      composition       - {"userdata": n, "addons": n, "media": n, "other": n}
                          counted by each member's top-level path segment

    Raises whatever zipfile raises on an unreadable/corrupt archive; the caller
    turns that into a dialog."""
    import json
    import zipfile

    report = {
        "total_entries": 0,
        "manifest_present": False,
        "manifest_failed": [],
        "iptv_present": False,
        "composition": {"userdata": 0, "addons": 0, "media": 0, "other": 0},
    }
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        report["total_entries"] = len(names)
        manifest_member = None
        for member in names:
            norm = member.replace("\\", "/").lstrip("/")
            if not norm:
                continue
            top = norm.split("/", 1)[0]
            if top in report["composition"]:
                report["composition"][top] += 1
            else:
                report["composition"]["other"] += 1
            if norm.split("/")[-1] == BACKUP_MANIFEST_NAME and manifest_member is None:
                manifest_member = member
                report["manifest_present"] = True
            if IPTV_ADDON_DATA_MARKER in norm:
                report["iptv_present"] = True
        if manifest_member is not None:
            try:
                data = json.loads(zf.read(manifest_member).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                data = None
            if isinstance(data, dict):
                failed = data.get("failed")
                if isinstance(failed, list):
                    report["manifest_failed"] = [str(item) for item in failed]
    return report


def format_backup_report(report, zip_name=""):
    """Turn analyze_backup_zip()'s dict into the owner-facing dialog text."""
    comp = report["composition"]
    lines = []
    if zip_name:
        lines.append("Backup archive: %s" % zip_name)
    lines.append("Total entries: %d" % report["total_entries"])
    lines.append(
        "Manifest (%s): %s"
        % (BACKUP_MANIFEST_NAME, "present" if report["manifest_present"] else "MISSING")
    )
    failed = report["manifest_failed"]
    if failed:
        shown = ", ".join(failed[:5])
        extra = len(failed) - 5
        if extra > 0:
            shown += ", and %d more" % extra
        lines.append("Manifest failed items (%d): %s" % (len(failed), shown))
    elif report["manifest_present"]:
        lines.append("Manifest failed items: none")
    lines.append(
        "IPTV (pvr.iptvsimple) data: %s" % ("yes" if report["iptv_present"] else "no")
    )
    lines.append(
        "Top level: userdata=%d, addons=%d, media=%d, other=%d"
        % (comp["userdata"], comp["addons"], comp["media"], comp["other"])
    )
    return "\n".join(lines)


def VERIFY_BACKUP_ARCHIVE():
    """Owner tool: pick a backup zip (same restore.path picker restore uses), open
    it READ-ONLY, and report what is inside. Never extracts, never restores."""
    zipFolder = control.setting("restore.path")
    try:
        # Reuse wiz's baked-nfs-port fix when this build has it (see wiz.py: Kodi's
        # browse dialog bakes :2049 into nfs:// paths, which then fail to list).
        from resources.lib.modules import wiz

        if hasattr(wiz, "_strip_nfs_port"):
            zipFolder = wiz._strip_nfs_port(zipFolder)
    except Exception:
        pass
    if zipFolder == "" or zipFolder is None:
        control.infoDialog("Please Setup a Zip Files Location first")
        control.openSettings()
        return
    try:
        _dirs, _files = xbmcvfs.listdir(zipFolder)
    except Exception:
        _files = []
    names = [f for f in _files if f.endswith(".zip")]
    if not names:
        ui.error("No backup zips found in:\n%s" % zipFolder)
        return
    select = control.selectDialog(names)
    if select == -1:
        return
    chosen = names[select]
    source = translatePath(os.path.join(zipFolder, chosen))
    local = source
    temp_special = None
    if "://" in source:
        # Remote share: zipfile cannot open a VFS URL, so stage a read-only copy in
        # temp (the source archive itself is never touched).
        temp_special = "special://temp/ezmpp_verify_%s" % chosen
        try:
            with ui.Progress(
                "Fetching backup for verification...", heading=AddonTitle
            ) as p:
                outcome = ui.copy_with_progress(source, temp_special, progress=p)
        except Exception:
            ui.error("Could not fetch that backup from the share for verification.")
            return
        if outcome != ui.COPY_OK:
            return  # user cancelled the fetch; nothing to report
        local = translatePath(temp_special)
    try:
        try:
            report = analyze_backup_zip(local)
        except Exception as e:
            ui.error(
                "Could not read that zip (corrupt or not a zip?)\n%s: %s"
                % (type(e).__name__, e)
            )
            return
    finally:
        if temp_special is not None:
            try:
                os.remove(translatePath(temp_special))
            except OSError:
                pass
    ui.done(format_backup_report(report, chosen))


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
    wipe_failed = None  # None = the wipe itself never ran (import failure / raise)
    with ui.Progress("Wiping install...", heading=AddonTitle):
        try:
            from resources.lib.modules import onetap

            # keep_addon_db() preserves Kodi's add-on state DB so EZ Maintenance++ comes
            # back ENABLED after the restart (not disabled/"gone", which was the bad UX).
            # _wipe returns (files_removed, keys_removed, failed_count, named_leftovers);
            # Fresh Start only needs the failed COUNT.
            _f, _k, wipe_failed, _leftovers = onetap._wipe(
                HOME, onetap._wipe_excludes(), onetap.keep_addon_db()
            )
        except Exception as e:
            xbmc.log(
                "%s : Fresh Start wipe FAILED: %s: %s"
                % (AddonTitle, type(e).__name__, e),
                level=xbmc.LOGERROR,
            )
        try:
            xbmc.executebuiltin(
                "UpdateLocalAddons"
            )  # reconcile the DB with what's left
        except Exception:
            pass
    if mode != "silent":
        # Honest completion: "Clean slate ready" is only ever claimed when the wipe
        # ran AND removed everything it was asked to. A wipe that never ran, or that
        # left survivors (on tvOS: NSUserDefaults keys that resurrect old settings),
        # says so plainly instead of pretending.
        if wipe_failed is None:
            ui.done(
                "Fresh Start FAILED: the wipe did not run. Nothing was removed. "
                "See the log."
            )
            return
        if wipe_failed:
            ui.done(
                "Fresh Start INCOMPLETE: %d item(s) could not be removed and may "
                "carry old settings over (see the log). Kodi will restart now."
                % wipe_failed
            )
        else:
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
        # "thumb", NOT "thumbnailImage". The PY2 branch above passes
        # thumbnailImage= as a ListItem CONSTRUCTOR kwarg, which really did set
        # the thumbnail; the py3 port turned that kwarg name into a setArt KEY,
        # and there is no such art key, so it was silently dropped. With no
        # thumb and setInfo(type="Video") below, Kodi fell back to
        # DefaultVideo.png - the reel-to-reel movie camera that has been showing
        # in place of the add-on's own icon on every menu since the py3 port.
        liz.setArt({"icon": iconImage, "poster": icon})
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

elif action == "device_name":
    from resources.lib.modules import tools

    tools.deviceName()

elif action == "clear_all":
    from resources.lib.modules import maintenance

    maintenance.clearAll()

elif action == "clear_channels":
    from resources.lib.modules import maintenance

    maintenance.clearRecentChannels()

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

elif action == "tools":
    TOOLS()

elif action == "purge_stale_tvos_keys":
    # RETIRED in 2026.07.19.5 (the purge runs automatically in restore, at boot
    # once per version, and in the two-layer wipe). Kept as an explicit no-op so a
    # stale favourite, widget or bookmark pointing at the old action lands here
    # instead of falling through to the unknown-action path. Deliberately silent:
    # nothing failed, and there is nothing the user needs to do.
    pass

elif action == "verify_backup_archive":
    VERIFY_BACKUP_ARCHIVE()

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
