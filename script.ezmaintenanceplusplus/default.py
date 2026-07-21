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
    # "Device Name" moved here from the top level. Naming a box is exactly what this
    # folder is for, and since 2026.07.19.4 removed the post-restore name prompt
    # (restore now PRESERVES the box's existing name rather than asking), this item
    # is the only deliberate way to change a name. It sits FIRST because naming is
    # the first thing an owner does with a new box and the item most likely to be
    # wanted on its own, so it must not be buried under the bulk actions.
    CreateDir(
        "Device Name",
        "ur",
        "device_name",
        ADDON_ICON,
        ADDON_FANART,
        "Name this box so you can tell it apart from the others.",
    )
    CreateDir("Set up everything", "url", "setup_all_box", ADDON_ICON, ADDON_FANART, "")
    CreateDir(
        "Add media sources (mini)", "url", "setup_sources", ADDON_ICON, ADDON_FANART, ""
    )
    CreateDir("Set up weather", "url", "setup_weather", ADDON_ICON, ADDON_FANART, "")
    CreateDir("Enable RSS ticker", "url", "setup_rss", ADDON_ICON, ADDON_FANART, "")


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


# --------------------------------------------------------------------------- #
# Guards for the looping Backup/Restore menu (2026.07.19.8)
#
# The menu re-presents itself after each sub-action. These decide when it must NOT,
# because a sub-action returns None whether it worked, cancelled, or fired an ASYNC
# builtin that took the screen away from us.
# --------------------------------------------------------------------------- #
# Published by wiz.restore() (wiz.py:1769) on every path that got as far as touching
# the box. Read as "a restore really ran", not as a verdict - any value counts.
RESTORE_VERDICT_PROP = "ezm_restore_verdict"


def _clear_restore_verdict():
    """Drop a stale verdict from an earlier restore in this same Kodi session.

    Without this, one restore would poison every later RESTORE pick in the same
    session: the property would still be set, and the menu would exit on a restore
    the user actually cancelled. Best-effort - a failure here only costs an early
    exit from a menu, so it must never raise into the menu loop."""
    try:
        xbmcgui.Window(10000).clearProperty(RESTORE_VERDICT_PROP)
    except Exception:
        pass


def _restore_verdict():
    """True if wiz.restore() published a verdict since the last clear."""
    try:
        return bool(xbmcgui.Window(10000).getProperty(RESTORE_VERDICT_PROP))
    except Exception:
        # Unreadable means unknown. Return False so the menu stays open: the failure
        # mode of a false True (ejecting her to the root) is the bug being fixed,
        # while a false False is caught by _safe_to_re_present's abort check.
        return False


def _safe_to_re_present(monitor=None, settle=0.25):
    """False when re-presenting the Backup/Restore menu would fight another window.

    Two ASYNC builtins can take the screen between iterations, invisibly to a
    sub-action's return value:

      * `Quit` (ui.restart, from the post-restore ask_restart). executebuiltin is
        called WITHOUT the wait flag, so Kodi's teardown runs while this script is
        still alive. Monitor.waitForAbort is Kodi's own "we are shutting down" signal.
      * `Addon.OpenSettings` (control.openSettings). ALL THREE sub-actions bail to it
        when their path setting is unconfigured - wiz.backup on download.path
        (wiz.py:337), wiz.restoreFolder (wiz.py:684) and VERIFY_BACKUP_ARCHIVE on
        restore.path. Re-presenting would drop a modal select dialog on top of the
        settings window the user was just sent to.

    The short wait is load-bearing twice over: it IS the abort check, and it gives
    the async OpenSettings time to actually become the active window before we look.
    Best-effort by design - if the probes themselves fail we keep the menu open,
    because staying is the behaviour the owner asked for and the abort check is the
    backstop for the one case where leaving matters."""
    try:
        if monitor is None:
            monitor = xbmc.Monitor()
        if monitor.waitForAbort(settle):
            return False  # Kodi is shutting down
        return not xbmc.getCondVisibility("Window.IsActive(addonsettings)")
    except Exception:
        return True


# ###########################################################################################
# ###########################################################################################


def FRESHSTART(mode="verbose"):
    # Wipe to a clean Kodi, then hard-exit via ui.terminate() (os._exit, NOT a graceful
    # Quit). Skipping CApplication::Stop() skips its save-skin-settings-on-exit flush,
    # which used to re-write the wiped custom skin's addon_data AFTER the wipe and
    # re-dirty the slate. No pre-wipe skin-swap (that step used to hang). Uses the shared
    # hardened wipe engine in onetap.py (preserves this add-on, its runtime deps, temp/,
    # and backupdir); the two Fresh Start settings can also keep the user's file-manager
    # sources (+ credentials) and repositories. mode="silent" wipes with no prompts, no exit.
    if mode != "silent":
        # Fresh Start deletes everything under the wipe root (special://home), INCLUDING
        # the active skin's files when that skin is installed there. A skin that lives
        # OUTSIDE the wipe root (the built-in Estuary, bundled read-only in the APK)
        # survives, so its dialogs can still draw the completion prompt after the wipe.
        # Refuse when the live skin sits under the wipe root: it would be pulled out from
        # under Kodi mid-wipe and nothing could render. Checked by PATH, never by skin
        # id, so EZM++ stays skin-agnostic.
        skin_path = os.path.normpath(translatePath("special://skin/"))
        wipe_root = os.path.normpath(HOME)
        if skin_path == wipe_root or skin_path.startswith(wipe_root + os.sep):
            ui.error(
                "Fresh Start needs a skin that survives the wipe. Your current skin is "
                "installed under userdata and would be removed mid-wipe.\n"
                "Please switch to the built-in Estuary skin (Settings > Interface > "
                "Skin), then run Fresh Start again.",
                heading=AddonTitle,
            )
            return
        if not ui.confirm_wipe(
            "Wipe this Kodi to a clean state?\n"
            "EZ Maintenance++ will survive the wipe. You must relaunch Kodi when done.",
            heading=AddonTitle,
        ):
            return
    # The wipe is a single step (no per-item progress); the context-managed gauge shows a
    # 'Wiping install...' spinner and is always closed.
    # Opt-in "keep across wipe" (Fresh Start settings tab; default OFF == full wipe).
    keep_sources = control.setting("freshstart.keep_sources") == "true"
    keep_repos = control.setting("freshstart.keep_repos") == "true"
    wipe_failed = None  # None = the wipe itself never ran (import failure / raise)
    # Did the destructive pass BEGIN? Distinct from wipe_failed, which only says whether
    # it ran to completion. _wipe deletes files first and sweeps NSUserDefaults keys last
    # (onetap._wipe_nsud_keys), so a raise from the key pass lands here with the POSIX
    # tree - including every userdata/Database file - ALREADY GONE. Treating that as
    # "the wipe did not run" both told the owner a falsehood and, worse, returned without
    # terminating: Kodi then stayed alive on a tree whose open databases had been
    # unlinked, which is precisely the SIGABRT this release exists to prevent.
    wipe_started = False
    with ui.Progress("Wiping install...", heading=AddonTitle) as p:
        try:
            from resources.lib.modules import onetap

            # keep_addon_db() preserves Kodi's add-on state DB so EZ Maintenance++ comes
            # back ENABLED after the restart (not disabled/"gone", which was the bad UX).
            # The opt-in keeps add the user's file-manager sources (+ credentials) and/or
            # their repositories to what survives. _wipe returns
            # (files_removed, keys_removed, failed_count, named_leftovers); Fresh Start
            # only needs the failed COUNT. progress=p.items drives the wipe gauge.
            excludes = onetap._wipe_excludes()
            if keep_repos:
                excludes = excludes | onetap.repository_addon_names()
            keep = onetap.keep_addon_db()
            if keep_sources:
                keep = keep | onetap.keep_source_files()
            wipe_started = (
                True  # set BEFORE the call: anything after this may have deleted
            )
            _f, _k, wipe_failed, _leftovers = onetap._wipe(
                HOME, excludes, keep, progress=p.items
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
        if wipe_failed is None and not wipe_started:
            # Genuinely nothing happened (import error, or a raise before the first
            # delete). Kodi is untouched, so it is safe to stay up.
            ui.done(
                "Fresh Start FAILED: the wipe did not run. Nothing was removed. "
                "See the log."
            )
            return
        if wipe_failed is None:
            # The wipe BEGAN and then raised. Files are gone - including databases Kodi
            # holds open - so staying up is the one thing we must not do. Terminate, and
            # say what actually happened instead of "nothing was removed".
            ui.ask_terminate(
                "Fresh Start did not finish: it stopped part way through, so some "
                "items were removed and others were not (see the log).",
                heading=AddonTitle,
            )
            return
        # Name what the opt-in keeps preserved, so a non-empty "clean" slate is honest.
        kept = []
        if keep_sources:
            kept.append("file manager sources")
        if keep_repos:
            kept.append("repositories")
        kept_line = ("\n\nKept: " + ", ".join(kept) + ".") if kept else ""
        # Completion notice: the box MUST close, so ask_terminate always exits. It
        # renders because Fresh Start required stock Estuary, which survived the wipe.
        if wipe_failed:
            ui.ask_terminate(
                "Fresh Start INCOMPLETE: %d item(s) could not be removed and may "
                "carry old settings over (see the log)." % wipe_failed,
                heading=AddonTitle,
            )
        else:
            ui.ask_terminate(
                "Clean slate ready.%s\n\nAfter you reopen Kodi, EZ Maintenance++ is "
                "under Add-ons > Program add-ons (if it is off, open it there and "
                "choose Enable)." % kept_line,
                heading=AddonTitle,
            )


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

    # "VERIFY BACKUP ARCHIVE" moved here from the retired Tools category, which had
    # shrunk to this single entry once the manual stale-key purge was removed in
    # 2026.07.19.5 - a folder a user had to open to find one item, and that item is
    # plainly a backup operation. It sits LAST because it is a diagnostic on an
    # archive that already exists, not a primary action. Its Tools-era description,
    # kept verbatim because this select dialog has no plot slot to render it in:
    # "Read-only check of a backup zip: entry count, manifest, failed list, IPTV
    # data, top-level layout. Restores nothing."
    typeOfBackup = ["BACKUP", "RESTORE", "VERIFY BACKUP ARCHIVE"]
    # This menu LOOPS. Presented once, any sub-action that ended - a cancelled file
    # picker, a dismissed verify report, a cancelled backup-mode dialog - fell off the
    # end of this branch, the script exited, and Kodi dropped the user at the ROOT
    # menu. To check a second archive she had to walk back in from the top. Now every
    # sub-action returns HERE.
    #
    # There are THREE ways out, and the two beyond "she cancelled the menu" exist
    # because a sub-action's return tells us nothing: every one of them returns None
    # whether it worked, cancelled, or handed the screen to another window.
    #
    #   1. s_type is not 0/1/2 - she cancelled this menu (or an unexpected value came
    #      back, which must never spin).
    #   2. A restore actually RAN (see the RESTORE branch).
    #   3. Kodi is shutting down, or a sub-action opened the Settings window
    #      (see _safe_to_re_present).
    #
    # JUDGEMENT CALL - looping after a COMPLETED backup/restore, not just after a
    # cancel. An earlier revision of this comment argued it was uniformly safe on the
    # grounds that `Quit` tears the script down before the loop can act. THAT WAS
    # FALSE and is corrected here: ui.restart() calls executebuiltin("Quit") WITHOUT
    # the wait flag (this codebase documents the blocking form as
    # `executebuiltin(..., True)`, wiz.py:867), so it returns immediately and this
    # Python outlives it. That is not a theory - defect A is precisely a
    # CApplication::Stop settings flush running after the add-on returned. So:
    #   * after a BACKUP, looping is safe. wiz.backup() never calls ask_restart and
    #     never quits; the box is unchanged and she may well want a second archive.
    #   * after a RESTORE, it is NOT safe, and the branch below breaks instead.
    while True:
        s_type = control.selectDialog(typeOfBackup)
        if s_type == 0:
            modes = ["Full Backup", "Addons Settings"]
            select = control.selectDialog(modes)
            if select == 0:
                wiz.backup(mode="full")
            elif select == 1:
                wiz.backup(mode="userdata")
            # select == -1: she backed out of the mode dialog. Fall through to the
            # top of the loop and re-present Backup/Restore - backing out of a
            # sub-dialog must never eject her all the way to the root menu.
        elif s_type == 1:
            # A restore that REACHED THE BOX ends this menu. Cleared first, then read
            # back: wiz.restore() publishes this Home-window property on every path
            # that got as far as touching the box, so its presence afterwards is a
            # reliable "a restore really ran here" - and there is no other signal,
            # since restoreFolder() returns None either way and wiz.py is a frozen
            # contract file this fix may not touch.
            #
            # Two independent reasons a restore must not come back to this menu:
            #   * Every terminal path of restore() ends in ui.ask_restart()
            #     (wiz.py:1725/1815/1818/1829). Accept it and Kodi is ALREADY tearing
            #     down behind this line (the async `Quit` above), so re-presenting
            #     would open a modal into a shutting-down message pump.
            #   * Decline it ("Later") and the box now carries restored files whose
            #     settings only land at the next clean shutdown. Offering BACKUP into
            #     that half-applied state is how you archive the pre-restore values -
            #     the kodi-settings-clobber class this project has four instances of.
            # A cancel at the file picker, the how-dialog, or the missing-zip-location
            # guard never reaches restore(), publishes nothing, and so DOES come back
            # to this menu. That is the owner's reported case and it still works.
            _clear_restore_verdict()
            wiz.restoreFolder()
            if _restore_verdict():
                break
        elif s_type == 2:
            VERIFY_BACKUP_ARCHIVE()
        else:
            break
        if not _safe_to_re_present():
            break

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
    # RETIRED: the Tools category is gone. Its last remaining item, "Verify backup
    # archive", now lives at the bottom of Backup/Restore where it belongs, so the
    # category was a folder wrapping a single backup action. Kept as an explicit
    # no-op so a stale favourite, widget or bookmark pointing at the old category
    # lands here instead of falling through to the unknown-action path. Deliberately
    # silent, same shape as the retired purge action below.
    pass

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
