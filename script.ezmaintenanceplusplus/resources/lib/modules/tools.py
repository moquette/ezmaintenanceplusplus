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
import re
from resources.lib.modules.backtothefuture import unicode, PY2

if PY2:
    translatePath = xbmc.translatePath
else:
    translatePath = xbmcvfs.translatePath

dp = xbmcgui.DialogProgress()
dialog = xbmcgui.Dialog()
addonInfo = xbmcaddon.Addon().getAddonInfo

AddonTitle = "EZ Maintenance++"
AddonID = "script.ezmaintenanceplusplus"


def xml_data_advSettings_old(size):
    xml_data = (
        """<advancedsettings>
      <network>
        <curlclienttimeout>10</curlclienttimeout>
        <curllowspeedtime>20</curllowspeedtime>
        <curlretries>2</curlretries>
        <cachemembuffersize>%s</cachemembuffersize>
        <buffermode>2</buffermode>
        <readbufferfactor>20</readbufferfactor>
      </network>
</advancedsettings>"""
        % size
    )
    return xml_data


def xml_data_advSettings_New(size):
    xml_data = (
        """<advancedsettings>
      <network>
        <curlclienttimeout>10</curlclienttimeout>
        <curllowspeedtime>20</curllowspeedtime>
        <curlretries>2</curlretries>
      </network>
      <cache>
        <memorysize>%s</memorysize>
        <buffermode>2</buffermode>
        <readfactor>20</readfactor>
      </cache>
</advancedsettings>"""
        % size
    )
    return xml_data


ADV_XML = "special://home/userdata/advancedsettings.xml"
# On Kodi 21 Omega the cache buffer lives in the GUI setting `filecache.memorysize` (in MB);
# advancedsettings.xml <cache> is DEPRECATED and IGNORED. So we read/write it via JSON-RPC,
# which is what actually takes effect. (Confirmed from the Omega source + v21 wiki.)
CACHE_SETTING = "filecache.memorysize"
KODI_DEFAULT_MB = 20  # Kodi's factory-default cache buffer


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
        if mb > 400 and not dialog.yesno(
            AddonTitle,
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
        dialog.ok(
            AddonTitle,
            "Cache buffer set to %d MB.\n"
            "Applies to the next video you play - no restart needed." % mb,
        )
    else:
        dialog.ok(
            AddonTitle, "Could not change the cache setting. Nothing was changed."
        )


def open_Settings():
    open_Settings = xbmcaddon.Addon(id=AddonID).openSettings()


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
