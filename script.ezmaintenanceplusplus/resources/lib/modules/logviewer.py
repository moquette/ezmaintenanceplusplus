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
from resources.lib.modules import control
from resources.lib.modules.backtothefuture import unicode

dp = xbmcgui.DialogProgress()
dialog = xbmcgui.Dialog()
addonInfo = xbmcaddon.Addon().getAddonInfo

AddonTitle = "EZ Maintenance++"
AddonID = "script.ezmaintenanceplusplus"


def logView():
    modes = ["View Log", "Upload Log to Pastebin"]
    logPaths = []
    logNames = []
    select = control.selectDialog(modes)

    # Code to map the old translatePath
    try:
        translatePath = xbmcvfs.translatePath
    except AttributeError:
        translatePath = xbmc.translatePath

    try:
        if select == -1:
            raise Exception()
        logfile_path = translatePath("special://logpath")
        logfile_names = (
            "kodi.log",
            "kodi.old.log",
            "spmc.log",
            "spmc.old.log",
            "tvmc.log",
            "freetelly.log",
            "ftmc.log",
            "firemc.log",
            "nodi.log",
        )
        for logfile_name in logfile_names:
            log_file_path = os.path.join(logfile_path, logfile_name)
            if os.path.isfile(log_file_path):
                logNames.append(logfile_name)
                logPaths.append(log_file_path)

        selectLog = control.selectDialog(logNames)
        selectedLog = logPaths[selectLog]
        if selectLog == -1:
            raise Exception()
        if select == 0:
            from resources.lib.modules import TextViewer

            TextViewer.text_view(selectedLog)
        elif select == 1:
            xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
            f = open(selectedLog, "rb")
            text = f.read()
            text = text.decode("UTF-8")
            f.close()
            from resources.lib.modules import pastebin

            upload_Link = pastebin.api().paste(unicode(text))
            xbmc.executebuiltin("Dialog.Close(busydialognocancel)")
            print("LOGVIEW UPLOADED LINK", upload_Link)
            if upload_Link != None:
                if "Error" not in upload_Link:
                    label = (
                        "Log Link: [COLOR skyblue][B]" + upload_Link + "[/B][/COLOR]"
                    )
                    dialog.ok(AddonTitle, "Log Uploaded to Pastebin" + "\n" + label)
                else:
                    dialog.ok(
                        AddonTitle,
                        "Cannot Upload Log to Pastebin"
                        + "\n"
                        + "Reason "
                        + upload_Link,
                    )
            else:
                dialog.ok(AddonTitle, "Cannot Upload Log to Pastebin")

    except:
        pass


##############################    END    #########################################
