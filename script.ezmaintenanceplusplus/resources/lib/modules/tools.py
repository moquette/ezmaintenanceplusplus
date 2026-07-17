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
from resources.lib.modules.backtothefuture import unicode, PY2
from resources.lib.modules import ui

if PY2:
    translatePath = xbmc.translatePath
else:
    translatePath = xbmcvfs.translatePath

dialog = xbmcgui.Dialog()
addonInfo = xbmcaddon.Addon().getAddonInfo

AddonTitle = "EZ Maintenance++"
AddonID = "script.ezmaintenanceplusplus"


ADV_XML = "special://home/userdata/advancedsettings.xml"
# On Kodi 21 Omega the cache buffer lives in the GUI setting `filecache.memorysize` (in MB);
# advancedsettings.xml <cache> is DEPRECATED and IGNORED. So we read/write it via JSON-RPC,
# which is what actually takes effect. (Confirmed from the Omega source + v21 wiki.)
CACHE_SETTING = "filecache.memorysize"
KODI_DEFAULT_MB = 20  # Kodi's factory-default cache buffer

# Device name lives in the core setting `services.devicename` (Settings > Services > General).
# Set via JSON-RPC like the cache buffer; persisted both-ways (see _set_devicename).
DEVICENAME_SETTING = "services.devicename"
GUISETTINGS_XML = translatePath("special://home/userdata/guisettings.xml")


def _jsonrpc(method, params):
    import json

    try:
        resp = xbmc.executeJSONRPC(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        )
        return json.loads(resp)
    except Exception:
        return {}


def _get_cache_mb():
    """Kodi's current cache buffer in MB from the live GUI setting (the one Omega uses)."""
    r = _jsonrpc("Settings.GetSettingValue", {"setting": CACHE_SETTING})
    try:
        return int(r["result"]["value"])
    except Exception:
        return None


def _set_cache_mb(mb):
    """Set Kodi's cache buffer (MB) via the live GUI setting. Returns True on success."""
    r = _jsonrpc(
        "Settings.SetSettingValue", {"setting": CACHE_SETTING, "value": int(mb)}
    )
    return bool(r.get("result"))


def _get_devicename():
    """This box's current device name from the live core setting. '' if unavailable."""
    r = _jsonrpc("Settings.GetSettingValue", {"setting": DEVICENAME_SETTING})
    try:
        return r["result"]["value"]
    except Exception:
        return ""


def _set_devicename(name):
    """Set the device name durably on EVERY platform, then return True iff the live set took.

    Two persistence hazards, opposite per platform, so we do BOTH writes:
      - Settings.SetSettingValue updates Kodi's LIVE store. On tvOS that is the durable path
        (guisettings.xml is rewritten from NSUserDefaults on boot, so a file-only write reverts).
      - write_guisetting() puts the value straight into guisettings.xml, which is what survives a
        Fire TV / Android UNCLEAN shutdown (there the live store only flushes to the file on a
        clean exit). On tvOS it is harmless same-value reinforcement.
    The live set is the authoritative in-session result; the file write is best-effort. On failure
    we log so a wrong setting id / rejected value is diagnosable from kodi.log."""
    r = _jsonrpc(
        "Settings.SetSettingValue", {"setting": DEVICENAME_SETTING, "value": name}
    )
    ok = bool(r.get("result"))
    if ok:
        try:
            from resources.lib.modules import _kodisettings

            _kodisettings.write_guisetting(GUISETTINGS_XML, DEVICENAME_SETTING, name)
        except Exception:
            pass
    else:
        try:
            xbmc.log(
                "ezmaintenanceplus: could not set %s to '%s' (JSON-RPC returned %r)"
                % (DEVICENAME_SETTING, name, r),
                level=xbmc.LOGWARNING,
            )
        except Exception:
            pass
    return ok


def _total_ram_mb():
    try:
        digits = re.sub("[^0-9]", "", xbmc.getInfoLabel("System.Memory(total)"))
        return int(digits) if digits else 0
    except Exception:
        return 0


def _recommended_mb():
    """A STABLE recommendation off TOTAL RAM (a constant) - never the drifting free memory.
    The buffer is ~1x RAM per stream on Omega, so ~10% of total is safe; clamp to 50-200 MB."""
    total = _total_ram_mb()
    if total <= 0:
        return 100
    return max(50, min(200, int(total * 0.10)))


def _clean_stale_advancedsettings():
    """Best-effort removal of a stale advancedsettings.xml <cache> written by older versions
    (Omega ignores it, but it can confuse). Only deletes if it contains a cache block."""
    try:
        if xbmcvfs.exists(ADV_XML):
            with xbmcvfs.File(ADV_XML) as f:
                data = f.read()
            if "<cache>" in data or "cachemembuffersize" in data:
                xbmcvfs.delete(ADV_XML)
    except Exception:
        pass


def advancedSettings():
    current = _get_cache_mb()
    rec = _recommended_mb()
    cur_txt = "%d MB" % current if current is not None else "unknown"
    idx = dialog.select(
        "Video cache buffer  (current: %s . Kodi default %d MB)"
        % (cur_txt, KODI_DEFAULT_MB),
        [
            "Use recommended for this device:  %d MB" % rec,
            "Enter a value (MB)...",
            "Reset to Kodi default (%d MB)" % KODI_DEFAULT_MB,
        ],
    )
    if idx == -1:
        return  # cancel / back

    if idx == 0:
        mb = rec
    elif idx == 1:
        entered = _get_keyboard(
            default=str(rec),
            heading="Cache size in MEGABYTES (Cancel to abort)",
            cancel="-",
        )
        if not entered or entered == "-" or not str(entered).isdigit():
            return
        mb = int(entered)
        if mb > 400 and not ui.confirm(
            "%d MB is very large. Kodi buffers up to ~500 MB and a big buffer can make "
            "playback fail on low-memory devices. Use it anyway?" % mb,
            yeslabel="Use it",
            nolabel="Cancel",
        ):
            return
    else:  # Reset to Kodi default
        mb = KODI_DEFAULT_MB
        _clean_stale_advancedsettings()

    if _set_cache_mb(mb):
        ui.done(
            "Cache buffer set to %d MB.\n"
            "Applies to the next video you play - no restart needed." % mb
        )
    else:
        ui.error("Could not change the cache setting. Nothing was changed.")


# --------------------------------------------------------------------------- #
# Post-restore, per-device video-cache-buffer retune.
#
# A restore (especially a cross-device clone from a golden image) brings the SOURCE box's
# guisettings, so `filecache.memorysize` (the video cache buffer) is now sized for the
# WRONG device. The buffer is the one performance-critical setting that must differ per
# device (per its RAM), so on the FIRST boot after a restore we prompt to retune it for
# THIS device. wiz.restore() drops a persistent MARKER FILE (not a setSetting flag: a full
# restore's extracted settings.xml + Kodi's in-memory-settings clobber make setSetting
# unreliable here) that survives the restart; the boot service (service.py) checks it once
# Kodi is ready and calls prompt_buffer_after_restore(). The file lives in this add-on's
# own data dir, which the restore wipe preserves and the extract writes AFTER (see wiz.py).
#
# Since 2026.07.15.1 the SAME marker is also armed on the add-on's first-ever run
# (owner request 2026-07-15: a brand-new box deserves the same name+buffer offer a
# restored one gets). See arm_first_run_tuneup(); the prompts and the exactly-once
# clearing are shared with the restore path unchanged.
# --------------------------------------------------------------------------- #
BUFFER_PROMPT_MARKER = translatePath(
    "special://home/userdata/addon_data/script.ezmaintenanceplusplus/.ezm_buffer_prompt"
)

# First-ever-run flag: written the first time the boot service looks for it, making that
# check exactly-once for the lifetime of this install (a rebuilt box has no addon_data,
# so it legitimately counts as a first run again).
FIRST_RUN_FLAG = translatePath(
    "special://home/userdata/addon_data/script.ezmaintenanceplusplus/.ezm_first_run"
)


def mark_buffer_prompt_pending():
    """Drop the post-restore buffer-prompt marker. Best-effort; never raises."""
    try:
        d = os.path.dirname(BUFFER_PROMPT_MARKER)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(BUFFER_PROMPT_MARKER, "w") as f:
            f.write("1")
        return True
    except Exception:
        return False


def buffer_prompt_pending():
    """True iff a restore asked for a post-restart buffer prompt. Never raises."""
    try:
        return os.path.exists(BUFFER_PROMPT_MARKER)
    except Exception:
        return False


def clear_buffer_prompt_marker():
    """Remove the marker so the prompt fires exactly once. Never raises."""
    try:
        if os.path.exists(BUFFER_PROMPT_MARKER):
            os.remove(BUFFER_PROMPT_MARKER)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# PVR pause crash-recovery marker. A restore that carries IPTV briefly DISABLES
# pvr.iptvsimple for the extract window (so its teardown flush cannot overwrite
# the restored instance settings) and re-enables it afterward. If the restore is
# interrupted between the two (a crash mid-extract, a power loss - PROVEN on a
# real Fire TV 2026-07-16, where a heavy merge-restore killed Kodi after the
# pause), the client would be left DISABLED forever. This marker records the
# outstanding pause; the boot service re-enables the client and clears it, so the
# pause can never strand the IPTV client past the next launch.
# --------------------------------------------------------------------------- #
PVR_PAUSE_MARKER = translatePath(
    "special://home/userdata/addon_data/script.ezmaintenanceplusplus/.ezm_pvr_paused"
)


def mark_pvr_paused():
    """Record that a restore disabled the IPTV client and owes a re-enable.
    Best-effort; never raises."""
    try:
        d = os.path.dirname(PVR_PAUSE_MARKER)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(PVR_PAUSE_MARKER, "w") as f:
            f.write("1")
        return True
    except Exception:
        return False


def pvr_pause_pending():
    """True iff a restore disabled the IPTV client and has not re-enabled it.
    Never raises."""
    try:
        return os.path.exists(PVR_PAUSE_MARKER)
    except Exception:
        return False


def clear_pvr_pause_marker():
    """Clear the outstanding-pause marker once the client is confirmed enabled.
    Never raises."""
    try:
        if os.path.exists(PVR_PAUSE_MARKER):
            os.remove(PVR_PAUSE_MARKER)
    except Exception:
        pass


def arm_first_run_tuneup():
    """On the add-on's FIRST-EVER run, arm the same tune-up the restore path uses (name
    the device, size the buffer), so a brand-new box gets the offer too. Exactly-once
    via FIRST_RUN_FLAG, which is written on the first check REGARDLESS of outcome. An
    UPGRADED box (this add-on's settings.xml already exists when the flag is first
    checked, i.e. EZM++ ran before this feature) gets the flag but NOT the prompt, so
    shipping this does not re-prompt the whole fleet. Never raises; returns True iff
    the tune-up was armed."""
    try:
        if os.path.exists(FIRST_RUN_FLAG):
            return False
        d = os.path.dirname(FIRST_RUN_FLAG)
        if not os.path.isdir(d):
            os.makedirs(d)
        established = os.path.exists(os.path.join(d, "settings.xml"))
        with open(FIRST_RUN_FLAG, "w") as f:
            f.write("1")
        if established:
            return False
        return mark_buffer_prompt_pending()
    except Exception:
        return False


# NOTE: EZ Maintenance++ has NO IPTV behavior. The former post-restore IPTV auto-enable
# intent flag and the unattended boot gate that turned the IPTV client back on were REMOVED
# (they auto-enabled a client that crashed natively on a real box). A restore never touches,
# enables, disables, or stages the IPTV client; the user turns IPTV on deliberately.


def prompt_buffer_after_restore():
    """If a restore dropped the marker, offer to retune the video cache buffer for THIS
    device (the restore cloned the source box's buffer size). Three choices: set the
    recommended size, choose manually (the existing Buffer Size screen), or keep current.
    The marker is deleted regardless of choice so it fires EXACTLY once. Fully defensive:
    any failure is swallowed so the boot service can never be broken by it. Returns True
    iff a prompt was shown (no marker => False, no prompt)."""
    try:
        if not buffer_prompt_pending():
            return False
    except Exception:
        return False
    try:
        rec = _recommended_mb()
        idx = dialog.select(
            "Finish setup (2 of 2): Video quality",
            [
                "Use the recommended video buffer - %d MB (best for this device)" % rec,
                "Pick a different amount myself...",
                "Leave it as it is",
            ],
        )
        if idx == 0:
            if _set_cache_mb(rec):
                try:
                    dialog.notification(
                        AddonTitle,
                        "Video cache buffer set to %d MB for this device." % rec,
                    )
                except Exception:
                    pass
        elif idx == 1:
            advancedSettings()
        # idx == 2 (Keep current) or -1 (cancel/back): do nothing.
    except Exception:
        pass
    finally:
        clear_buffer_prompt_marker()
    return True


def prompt_devicename_after_restore():
    """Offer to rename THIS device after a restore cloned the SOURCE box's name (Settings >
    Services > General). Unlike the buffer there is no derivable "right" value, so this is
    text-entry: the keyboard is prefilled with the current name for the user to edit. Does NOT
    touch the post-restore marker (the buffer step owns clearing it, so the combined flow fires
    exactly once). Fully defensive. Returns True iff a new name was applied; False on
    keep / cancel / unchanged / empty / failure."""
    try:
        cur = _get_devicename()
        idx = dialog.select(
            "Finish setup (1 of 2): Device name",
            [
                "Rename this device (currently '%s')" % (cur or "unknown"),
                "Keep the name '%s'" % (cur or "unknown"),
            ],
        )
        if idx != 0:
            return False  # Keep (1) or cancel / back (-1)
        entered = _get_keyboard(
            default=cur, heading="Device name (Cancel to keep current)", cancel=cur
        )
        new = (entered or "").strip()
        if not new or new == cur:
            return False
        if _set_devicename(new):
            try:
                dialog.notification(
                    AddonTitle,
                    "Device name set to '%s'. The network name (AirPlay/UPnP) updates "
                    "after the next restart." % new,
                )
            except Exception:
                pass
            return True
        try:
            ui.error("Could not change the device name. Nothing was changed.")
        except Exception:
            pass
    except Exception:
        pass
    return False


def prompt_after_restore():
    """Single post-restore tune-up, gated ONCE by the (buffer-named) marker: run the device-name
    step FIRST, then the buffer step. ONLY the buffer step clears the marker, and it always runs
    after the wrapped device-name step, so the whole flow is exactly-once and a device-name-step
    failure can never strand the marker. Returns True iff the flow ran (the marker was present)."""
    try:
        if not buffer_prompt_pending():
            return False
    except Exception:
        return False
    try:
        prompt_devicename_after_restore()  # never clears the marker
    except Exception:
        pass
    prompt_buffer_after_restore()  # clears the marker in its finally
    return True


def _get_keyboard(default="", heading="", hidden=False, cancel=""):
    """shows a keyboard and returns a value"""
    if cancel == "":
        cancel = default
    keyboard = xbmc.Keyboard(default, heading, hidden)
    keyboard.doModal()
    if keyboard.isConfirmed():
        return unicode(keyboard.getText())
    return cancel


##############################    END    #########################################
