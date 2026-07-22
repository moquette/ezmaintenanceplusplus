# -*- coding: utf-8 -*-

"""
CONTROL ROUTINES

Only the surface the add-on actually uses. This module is imported at the top
of default.py, i.e. on EVERY plugin invocation - it must never instantiate
GUI objects (DialogProgress, WindowDialog, Player) at import time.
"""

import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from resources.lib.modules.backtothefuture import PY2
from resources.lib.modules import ui


setting = xbmcaddon.Addon().getSetting

# Kept even though no shipped code path writes through it: the wiz restore test
# patches it as a tripwire proving restore() never re-stamps box-local settings.
setSetting = xbmcaddon.Addon().setSetting

addonInfo = xbmcaddon.Addon().getAddonInfo

dialog = xbmcgui.Dialog()

execute = xbmc.executebuiltin

if PY2:
    translatePath = xbmc.translatePath
else:
    translatePath = xbmcvfs.translatePath

AddonID = "script.ezmaintenanceplusplus"
# DIRECTORIES
USERDATA = translatePath(os.path.join("special://home/userdata", ""))
HOME = translatePath("special://home/")


def addonIcon():
    path = translatePath(os.path.join("special://home/addons/" + AddonID, "icon.png"))
    return path


def addonFanart():
    return translatePath(os.path.join("special://home/addons/" + AddonID, "fanart.jpg"))


def infoDialog(message, heading=ui.HEADING, icon="", time=None, sound=False):
    if time is None:
        time = 3000
    else:
        time = int(time)
    if icon == "":
        icon = addonIcon()
    elif icon == "INFO":
        icon = xbmcgui.NOTIFICATION_INFO
    elif icon == "WARNING":
        icon = xbmcgui.NOTIFICATION_WARNING
    elif icon == "ERROR":
        icon = xbmcgui.NOTIFICATION_ERROR
    dialog.notification(heading, message, icon, time, sound=sound)


def selectDialog(list, heading=ui.HEADING):
    """Kodi's select dialog, plain list.

    Deliberately NOT useDetails=True. The detailed view is the only way to get a
    real second line per row, but it reserves a thumbnail column and Kodi fills an
    artless row with DefaultAddonMore.png, putting a column of "+" glyphs down a
    backup menu. Callers that want extra text put it in the label instead - see
    _menu_rows in default.py.

    Note the argument order: Kodi takes (heading, list), this takes (list,
    heading). Do not pass a third positional argument through - Kodi's third is
    `autoclose`, in milliseconds."""
    return dialog.select(heading, list)


def openSettings(query=None, id=None):
    try:
        if id is None:
            id = addonInfo("id")
        idle()
        execute("Addon.OpenSettings(%s)" % id)
        if query is None:
            raise Exception()
        c, f = query.split(".")
        execute("SetFocus(%i)" % (int(c) + 100))
        execute("SetFocus(%i)" % (int(f) + 200))
    except Exception:
        return


def idle():
    return execute("Dialog.Close(busydialog)")
