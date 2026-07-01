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


def _current_buffer_mb():
    """The buffer size already in advancedsettings.xml (MB), or None if not set. Lets us
    SHOW the current value so the user can tell whether a previous change actually stuck."""
    try:
        if not xbmcvfs.exists(ADV_XML):
            return None
        with xbmcvfs.File(ADV_XML) as f:
            data = f.read()
        m = re.search(r"<memorysize>\s*(\d+)", data) or re.search(
            r"<cachemembuffersize>\s*(\d+)", data
        )
        if m:
            return int(m.group(1)) // (1024 * 1024)
    except Exception:
        pass
    return None


def _write_advancedsettings(xml_data):
    """Write advancedsettings.xml through Kodi VFS (robust on tvOS, where a plain open() can
    land in the wrong sandbox path); verify it exists afterward. Returns True on success."""
    try:
        with xbmcvfs.File(ADV_XML, "w") as f:
            f.write(bytearray(xml_data, "utf-8"))
        if xbmcvfs.exists(ADV_XML):
            return True
    except Exception:
        pass
    try:  # fallback: plain write to the translated path
        with open(translatePath(ADV_XML), "w") as fh:
            fh.write(xml_data)
        return True
    except Exception:
        return False


def advancedSettings():
    free_mb = int(re.sub("[^0-9]", "", xbmc.getInfoLabel("System.FreeMemory")) or "0")
    optimal_mb = max(20, free_mb // 3)
    current = _current_buffer_mb()
    header = (
        "Current buffer: %d MB\n" % current
        if current is not None
        else "No buffer set yet.\n"
    )

    msg = (
        "%sOptimal for your free memory: %d MB.\n\nApply the optimal value, or enter your own?"
        % (header, optimal_mb)
    )
    # Kodi 20+/Omega: a real 3-button dialog (Use Optimal / Enter Value / Cancel).
    # yesnocustom returns 1=yes, 0=no, 2=custom, -1=cancelled.
    if hasattr(dialog, "yesnocustom"):
        choice = dialog.yesnocustom(
            AddonTitle,
            msg,
            "Cancel",
            nolabel="Enter Value",
            yeslabel="Use Optimal",
            defaultbutton=getattr(xbmcgui, "DLG_YESNO_YES_BTN", 1),  # focus Use Optimal
        )
        if choice in (-1, 2):  # Cancel button or ESC/back
            return
        use_optimal = choice == 1
    else:  # Kodi 19 fallback: two buttons (ESC -> Enter Value -> keyboard cancel aborts)
        use_optimal = dialog.yesno(
            AddonTitle, msg, yeslabel="Use Optimal", nolabel="Enter Value"
        )

    if use_optimal:
        size = optimal_mb * 1024 * 1024
    else:
        entered = _get_keyboard(
            default=str(optimal_mb * 1024 * 1024),
            heading="Buffer size in BYTES (Cancel to abort)",
            cancel="-",
        )
        if not entered or entered == "-" or not str(entered).isdigit():
            return
        size = int(entered)

    # Kodi 19+/Omega schema (<cache><memorysize>/<buffermode>/<readfactor>); this add-on
    # requires xbmc.python 3.0.0, so we never need the pre-19 layout.
    if not _write_advancedsettings(xml_data_advSettings_New(str(size))):
        dialog.ok(
            AddonTitle, "Could not write advancedsettings.xml. Nothing was changed."
        )
        return

    if dialog.yesno(
        AddonTitle,
        "Buffer size set to %d MB.\n\nKodi only reads advancedsettings.xml at startup, so it "
        "must RESTART for this to take effect. Restart now?" % (size // (1024 * 1024)),
        yeslabel="Restart now",
        nolabel="Later",
    ):
        xbmc.executebuiltin("Quit")


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
