import xbmc
import xbmcaddon
import xbmcgui
import json
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


# The skin's deferred first-boot menu rebuild. skin.estuary7's Home.xml onload arms
# AlarmClock(t7bbuild,...,00:15) on the first Home load of every boot; when the menu is
# stale - exactly what a restore produces - the resulting skinshortcuts buildxml ends in
# ReloadSkin(), which destroys the entire window stack including any dialog we have open.
# Reproduced twice on the bench 2026-07-18 at 15.05s and 15.45s after the alarm was
# armed, killing an EZM++ prompt 15.27s in. Home-visible is the moment that timer STARTS,
# so prompting there is prompting into a guaranteed-doomed window. Wait past it, plus
# margin, and also honour skinshortcuts' own in-progress flag.
# See ezmpp/docs/restore-defect-b-reproduced-2026-07-18.md.
_SKIN_DEFERRED_BUILD_SECS = 25


def _wait_skin_settled(monitor, extra=_SKIN_DEFERRED_BUILD_SECS, timeout=90):
    """Block (interruptibly) until the skin's deferred build can no longer eat our dialog.

    Two conditions, both cheap: at least ``extra`` seconds past home-visible (covering the
    skin's 15s alarm with margin), and skinshortcuts not reporting a build in progress.
    Returns False on abort, True otherwise. Never raises. A skin without the alarm simply
    costs this wait once per restored boot, which is invisible next to a restore."""
    if monitor.waitForAbort(extra):
        return False
    waited = extra
    while waited < timeout:
        try:
            if xbmc.getCondVisibility(
                "String.IsEqual(Window(10000).Property(skinshortcuts-isrunning),True)"
            ):
                if monitor.waitForAbort(2):
                    return False
                waited += 2
                continue
        except Exception:
            pass
        return True
    return True


def _wait_kodi_ready(monitor, timeout=120):
    """Block (interruptibly) until Kodi's GUI is actually up, so we never prompt on a black
    boot screen. Returns False on abort; True once the home window is visible OR the bound
    is reached (well past any black-screen phase). Never raises.

    NOTE: home-visible is NOT boot-settled. Callers that open a dialog must additionally
    call _wait_skin_settled - see its comment for the teardown this avoids."""
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


def _maybe_arm_first_run():
    """On the add-on's first-ever run, arm the SAME tune-up marker the restore path
    drops (rename this device / retune the buffer), so a brand-new box gets the offer
    a restored one does. Exactly-once per install via tools.FIRST_RUN_FLAG; a box that
    already ran an older EZM++ (its settings.xml exists) is flagged without prompting.
    Fully guarded: nothing here may block or crash the boot service."""
    try:
        from resources.lib.modules import tools

        tools.arm_first_run_tuneup()
    except Exception:
        pass


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
        # Home-visible starts the skin's 15s deferred-build timer; opening a dialog now
        # gets it destroyed. Wait past that before prompting.
        if not _wait_skin_settled(monitor):
            return
        tools.prompt_after_restore()
    except Exception:
        pass


def _maybe_restore_check(monitor):
    """On the FIRST boot after a restore, re-verify the restored state now that it is
    actually LIVE (restorecheck's two-layer probes). SILENT on a clean pass - the box
    simply working is the message; the verdict goes to the log. Only a real finding
    speaks, with the locked needs-attention line. The marker is cleared either way so
    the check runs exactly once. Fully guarded: nothing here may block or crash the
    boot service; a normal boot (no marker) is a single os-stat no-op."""
    try:
        from resources.lib.modules import tools
    except Exception:
        return
    try:
        if not tools.restore_check_pending():
            return
    except Exception:
        return
    # Wait for the GUI OUTSIDE the try/finally: an aborted or interrupted boot must
    # NOT consume the one-shot marker (the check never ran, so it is still owed).
    if not _wait_kodi_ready(monitor):
        return
    try:
        from resources.lib.modules import restorecheck

        attention = []
        try:
            attention.extend(
                "%d duplicate two-layer listing(s): %s" % (len(d), ", ".join(d[:5]))
                for d in [restorecheck.duplicate_listing_hits()]
                if d
            )
        except Exception:
            pass
        # Defect A3: the restore wrote the archive's skin to disk, then Kodi's
        # shutdown flush serialized the PRE-restore skin from live memory over it,
        # so the box can reopen on the wrong skin. This is the ONLY place it is
        # observable - the restore itself finishes before the restart that decides
        # the outcome. getSkinDir() is the read-only probe; Skin.HasSetting /
        # GetInfoBooleans MUTATE (they insert a default-false setting and schedule
        # a save) and must never be used to check skin state.
        try:
            expected = tools.restore_check_expected_skin()
            if expected:
                live = (xbmc.getSkinDir() or "").strip()
                if live and live != expected:
                    attention.append(
                        "restored skin did not become live: expected %s, running %s "
                        "(the restored skin and its settings are installed and intact; "
                        "switch in Settings > Interface > Skin)" % (expected, live)
                    )
        except Exception:
            pass
        if attention:
            for line in attention:
                xbmc.log(
                    "%s : boot restore-check ATTENTION: %s" % (AddonID, line),
                    level=xbmc.LOGWARNING,
                )
            xbmcgui.Dialog().notification(
                "EZ Maintenance++",
                "Something from the restore needs attention - open EZ Maintenance++.",
                time=8000,
            )
        else:
            xbmc.log(
                "%s : boot restore-check: restored state verified clean" % AddonID,
                level=xbmc.LOGINFO,
            )
    except Exception:
        pass
    finally:
        try:
            tools.clear_restore_check_marker()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# One-shot stale NSUserDefaults key migration (tvOS only).
#
# The 2026.07.08.2 - 2026.07.13.x releases vectored EVERY restored userdata xml
# into NSUserDefaults ("vector everything"). Boxes that ran them still hold keys
# for files current nsud policy deliberately leaves as plain POSIX (an add-on's
# private data), and on tvOS a key SHADOWS the disk file (CTVOSFile::Exists
# checks the key FIRST), so a freshly written or restored file can be silently
# invisible to Kodi forever. nsud.purge_stale_keys() materializes key-only files
# back to disk before purging the out-of-scope keys.
#
# The run-once marker is a FILE in this add-on's own addon_data (the same
# pattern as tools.BUFFER_PROMPT_MARKER, and for the same reason: a restore's
# extracted settings.xml plus Kodi's in-memory-settings clobber make setSetting
# unreliable for boot-time state). It holds the add-on version the purge last
# ran for, so each upgrade gets exactly one purge and a normal boot is a single
# os-stat no-op.
# --------------------------------------------------------------------------- #
STALE_KEY_PURGE_MARKER = translatePath(
    "special://home/userdata/addon_data/" + AddonID + "/.ezm_stale_key_purge"
)


def _read_stale_purge_marker():
    """Version string the purge last completed for, '' if never. Never raises."""
    try:
        with open(STALE_KEY_PURGE_MARKER, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_stale_purge_marker(version):
    """Record the version the purge ran for. Best-effort; never raises."""
    try:
        d = os.path.dirname(STALE_KEY_PURGE_MARKER)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(STALE_KEY_PURGE_MARKER, "w") as f:
            f.write(version)
        return True
    except Exception:
        return False


def _maybe_purge_stale_nsud_keys():
    """Run nsud.purge_stale_keys(control.USERDATA) at most once per add-on version,
    and only on Apple TV (tvOS) - the only platform where NSUserDefaults exists.

    Fully guarded: any failure logs LOUDLY (LOGERROR) and returns; the boot
    service must never be crashed or blocked by this migration. On failure the
    marker is NOT written, so the next boot retries. If nsud predates
    purge_stale_keys (hasattr guard) this is a clean no-op that also leaves the
    marker unset, so the purge still happens once the capable nsud ships."""
    try:
        try:
            is_tvos = bool(xbmc.getCondVisibility("System.Platform.TVOS"))
        except Exception:
            is_tvos = False
        if not is_tvos:
            return
        try:
            version = xbmcaddon.Addon().getAddonInfo("version") or ""
        except Exception:
            version = ""
        if not version:
            # Without a version we cannot keep the once-per-version promise;
            # skip rather than risk running on every boot.
            return
        if _read_stale_purge_marker() == version:
            return
        from resources.lib.modules import control, nsud

        if not hasattr(nsud, "purge_stale_keys"):
            return  # older nsud: nothing to run; marker stays unset on purpose
        materialized, purged, kept, failed = nsud.purge_stale_keys(control.USERDATA)
        xbmc.log(
            "ezmaintenanceplus: stale NSUserDefaults key purge (v%s): "
            "%d materialized, %d purged, %d kept, %d failed"
            % (version, materialized, purged, kept, failed),
            level=loglevel,
        )
        # The marker is written ONLY on a proven-complete run. purge_stale_keys
        # never raises by design, so its failure modes are failed>0 (keys still
        # shadowing) and an all-zeros no-op (plist transiently unreadable at boot).
        # Burning the run-once marker on either would silently strand exactly the
        # boxes this migration exists for - so both retry next boot instead.
        if failed:
            xbmc.log(
                "ezmaintenanceplus: stale key purge left %d key(s) unresolved; "
                "marker not set, will retry next boot" % failed,
                level=xbmc.LOGWARNING,
            )
            return
        if not (materialized or purged or kept):
            xbmc.log(
                "ezmaintenanceplus: stale key purge saw an empty/unreadable store; "
                "marker not set, will retry next boot",
                level=xbmc.LOGWARNING,
            )
            return
        if not _write_stale_purge_marker(version):
            xbmc.log(
                "ezmaintenanceplus: stale key purge ran but its run-once marker "
                "could not be written; the purge may repeat next boot",
                level=xbmc.LOGWARNING,
            )
    except Exception as e:
        try:
            xbmc.log(
                "ezmaintenanceplus: stale NSUserDefaults key purge FAILED "
                "%s: %s (marker not set; will retry next boot)" % (type(e).__name__, e),
                level=xbmc.LOGERROR,
            )
        except Exception:
            pass


def _maybe_resume_paused_pvr():
    """If a restore's IPTV pause was left outstanding (interrupted before re-enable),
    re-enable pvr.iptvsimple and clear the marker. Fully guarded; never blocks boot.
    Only re-enables when the marker is present, so it never fights a user who
    deliberately disabled the client."""
    try:
        from resources.lib.modules import tools

        if not tools.pvr_pause_pending():
            return
        res = _jsonrpc_service(
            "Addons.SetAddonEnabled",
            {"addonid": "pvr.iptvsimple", "enabled": True},
        )
        if res == "OK":
            tools.clear_pvr_pause_marker()
            xbmc.log(
                "ezmaintenanceplus: re-enabled pvr.iptvsimple after an interrupted "
                "restore (crash-recovery); marker cleared",
                level=loglevel,
            )
        else:
            xbmc.log(
                "ezmaintenanceplus: could not re-enable pvr.iptvsimple on boot "
                "(will retry next boot)",
                level=xbmc.LOGWARNING,
            )
    except Exception as e:
        try:
            xbmc.log(
                "ezmaintenanceplus: PVR pause recovery failed %s: %s"
                % (type(e).__name__, e),
                level=xbmc.LOGWARNING,
            )
        except Exception:
            pass


def _jsonrpc_service(method, params):
    """One JSON-RPC call from the boot service; parsed 'result' or None."""
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


if __name__ == "__main__":
    monitor = Monitor()

    # NOTE: the boot-time home-root self-heal sweep and the unattended IPTV auto-enable gate
    # were REMOVED after 2026.07.08.4. The sweep deleted files at boot and the gate turned the
    # IPTV client back on by itself - both proved unsafe on a real box. Nothing at boot deletes
    # files or enables IPTV; the user turns IPTV on deliberately. The extract-root fix (a
    # restore puts files in the right folder) stands, and the only boot action is the optional
    # tune-up prompt below (rename device / video cache buffer), armed by a restore or by
    # the add-on's first-ever run.

    # Stale-key migration FIRST, so files a "vector everything" era left shadowed
    # in NSUserDefaults are visible on disk before anything below reads them.
    _maybe_purge_stale_nsud_keys()
    # PVR pause crash-recovery: if a restore disabled pvr.iptvsimple for its extract
    # window and was interrupted before re-enabling it (PROVEN possible on a real
    # Fire TV 2026-07-16, where a heavy restore killed Kodi mid-extract), the marker
    # is still set - re-enable the client and clear it so a restore can never strand
    # IPTV disabled past the next launch.
    _maybe_resume_paused_pvr()
    _maybe_arm_first_run()
    _maybe_prompt_after_restore(monitor)
    _maybe_restore_check(monitor)

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
