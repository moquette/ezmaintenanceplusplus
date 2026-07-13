import xbmc
import xbmcaddon
import xbmcgui
import os
import xbmcvfs
import time
from resources.lib.modules.backtothefuture import PY2
from resources.lib.modules import maintenance

# Code to map the old translatePath
if PY2:
    translatePath = xbmc.translatePath
    loglevel = xbmc.LOGNOTICE
else:
    translatePath = xbmcvfs.translatePath
    loglevel = xbmc.LOGINFO

AddonID = "script.ezmaintenanceplusplus"
packagesdir = translatePath(os.path.join("special://home/addons/packages", ""))
thumbnails = translatePath("special://home/userdata/Thumbnails")
iconpath = translatePath(os.path.join("special://home/addons/" + AddonID, "icon.png"))


class Monitor(xbmc.Monitor):
    def __init__(self):
        xbmc.Monitor.__init__(self)
        maintenance.logMaintenance("Monitor init")
        maintenance.determineNextMaintenance()

    def onSettingsChanged(self):
        maintenance.logMaintenance("onSettingsChanged")
        maintenance.determineNextMaintenance()


def _wait_kodi_ready(monitor, timeout=120):
    """Block (interruptibly) until Kodi's GUI is actually up, so we never prompt on a black
    boot screen. Returns False on abort; True once the home window is visible OR the bound
    is reached (well past any black-screen phase). Never raises."""
    waited = 0
    while waited < timeout:
        if monitor.abortRequested():
            return False
        try:
            if xbmc.getCondVisibility("Window.IsVisible(home)"):
                return True
        except Exception:
            pass
        if monitor.waitForAbort(2):
            return False
        waited += 2
    return True


def _folder_size_and_count(top):
    """Total byte size and file count of a tree. Per-file errors (a file deleted
    mid-scan) are skipped, never raised - this runs unattended at every boot."""
    total = 0
    count = 0
    for dirpath, dirnames, filenames in os.walk(top):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
                count += 1
            except OSError:
                pass
    return total, count


def _int_setting(setting, sid, default):
    try:
        return int(setting(sid))
    except (TypeError, ValueError):
        return default


def _startup_checks():
    """The one-shot boot checks: packages/thumbnails size alerts (each offering a
    clean-now), the status notification, and the optional startup cache clean.

    Runs INSIDE the service after _wait_kodi_ready - NOT at import. At import time
    (Kodi startup) the two full-tree walks delayed every boot, and the modal yesno
    prompts could park a black boot screen; a "yes" then ran deleteThumbnails()
    synchronously in the boot path. Settings are read here, not at module scope, so
    a fresh boot sees current values."""
    setting = xbmcaddon.Addon().getSetting
    notify_mode = setting("notify_mode")
    auto_clean = setting("startup.cache")
    # Fallbacks mirror the settings.xml schema defaults.
    filesize = _int_setting(setting, "filesize_alert", 200)
    filesize_thumb = _int_setting(setting, "filesizethumb_alert", 500)

    total_size, count = _folder_size_and_count(packagesdir)
    total_sizetext = "%.0f" % (total_size / 1024000.0)

    if int(total_sizetext) > filesize:
        choice2 = xbmcgui.Dialog().yesno(
            "[COLOR=red]Autocleaner[/COLOR]",
            "The packages folder is [COLOR red]"
            + str(total_sizetext)
            + " MB [/COLOR] - [COLOR red]"
            + str(count)
            + "[/COLOR] zip files"
            + "\n"
            + "The folder can be cleaned up without issues to save space..."
            + "\n"
            + "Do you want to clean it now?",
            yeslabel="Yes",
            nolabel="No",
        )
        if choice2 == 1:
            maintenance.purgePackages()

    total_size2, _ = _folder_size_and_count(thumbnails)
    total_sizetext2 = "%.0f" % (total_size2 / 1024000.0)

    if int(total_sizetext2) > filesize_thumb:
        choice2 = xbmcgui.Dialog().yesno(
            "[COLOR=red]Autocleaner[/COLOR]",
            "The images folder is [COLOR red]"
            + str(total_sizetext2)
            + " MB   [/COLOR]"
            + "\n"
            + "The folder can be cleaned up without issues to save space..."
            + "\n"
            + "Do you want to clean it now?",
            yeslabel="Yes",
            nolabel="No",
        )
        if choice2 == 1:
            maintenance.deleteThumbnails()

    if notify_mode == "true":
        xbmc.executebuiltin(
            "Notification(%s, %s, %s, %s)"
            % (
                "Maintenance Status",
                "Packages: " + str(total_sizetext) + " MB"
                " - Images: " + str(total_sizetext2) + " MB",
                "5000",
                iconpath,
            )
        )
    if auto_clean == "true":
        maintenance.clearCache()


def _maybe_prompt_after_restore(monitor):
    """On the FIRST boot after a restore, run the post-restore tune-up: offer to rename THIS
    device and to retune the video cache buffer for it (a restore cloned the source box's name
    and buffer size). Fully guarded: nothing here may block or crash the boot service. The marker
    check happens BEFORE _wait_kodi_ready, so a normal boot (no marker) returns immediately and
    never delays the maintenance loop; only a genuinely pending restore waits for the GUI."""
    try:
        from resources.lib.modules import tools
    except Exception:
        return
    try:
        if not tools.buffer_prompt_pending():
            return
    except Exception:
        return
    try:
        if not _wait_kodi_ready(monitor):
            return
        tools.prompt_after_restore()
    except Exception:
        pass


if __name__ == "__main__":
    monitor = Monitor()

    # NOTE: the boot-time home-root self-heal sweep and the unattended IPTV auto-enable gate
    # were REMOVED after 2026.07.08.4. The sweep deleted files at boot and the gate turned the
    # IPTV client back on by itself - both proved unsafe on a real box. Nothing at boot deletes
    # files or enables IPTV; the user turns IPTV on deliberately. The extract-root fix (a
    # restore puts files in the right folder) stands, and the only boot action is the optional
    # post-restore tune-up prompt below (rename device / video cache buffer).

    _maybe_prompt_after_restore(monitor)

    if _wait_kodi_ready(monitor):
        try:
            _startup_checks()
        except Exception as e:
            # The alerts are best-effort; the maintenance loop below must start anyway.
            xbmc.log(
                "ezmaintenanceplus: startup checks failed %s: %s"
                % (type(e).__name__, e),
                level=xbmc.LOGWARNING,
            )

    while not monitor.abortRequested():
        # The auto-clean schedule is measured in days; a 60s tick is plenty.
        if monitor.waitForAbort(60):
            # Abort was requested while waiting. We should exit
            break
        if not xbmc.Player().isPlayingVideo():
            nextMaintenance = maintenance.getNextMaintenance()
            if nextMaintenance > 0 and time.time() >= nextMaintenance:
                xbmc.log("ezmaintenanceplus: AutoClean started", level=loglevel)
                maintenance.clearCache()
                xbmc.log("ezmaintenanceplus: AutoClean done", level=loglevel)
                maintenance.determineNextMaintenance()

    del monitor
