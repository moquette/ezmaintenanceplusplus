# -*- coding: utf-8 -*-
"""Box setup: the surviving "Foundation" provisioning, decoupled.

Extracted from the retiring script.module.tony7bones setup layer
(tony7bones.setup.foundation) so it can live in EZ Maintenance++ instead. Three
zero-config steps for a fresh box:

  * media sources - the mini's KodiShare / KodiBackup NFS shares (+ our repo)
  * weather       - Multi Weather, installed + pointed at Sacramento
  * RSS ticker    - the lookandfeel.enablerssfeeds core setting

No dependency on the old tony7bones install machinery: weather installs via
Kodi's own InstallAddon builtin (not the module's installer), settings are set
over JSON-RPC, and the sources/weather/RssFeeds files are written directly. All
steps are idempotent and defensive (logged, never abort the box).
"""

import json
import os
from xml.etree import ElementTree as ET

import xbmc
import xbmcaddon
import xbmcvfs

from resources.lib.modules import ui

AddonTitle = "EZ Maintenance++"


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log("EZMpp box-setup: %s" % msg, level)


# --------------------------------------------------------------------------- #
# Media sources (the mini's NFS shares + our repo) - zero-config for this
# household; the host is a constant IP (never a resolvable hostname).
# --------------------------------------------------------------------------- #
MINI_HOST = "192.168.7.2"
KODI_SHARE_PATH = "Users/moquette/Kodi/Share/"
KODI_BACKUP_PATH = "Users/moquette/Kodi/Backup/"
KODI_SHARE_SOURCE_NAME = "KodiShare"
KODI_BACKUP_SOURCE_NAME = "KodiBackup"
REPO_SOURCE_NAME = ".T7B"
REPO_SOURCE_URL = "https://tony7bones.github.io/"


def _nfs_url(host, path):
    """Port-free nfs:// URL from (host, path). Kodi caches sources at startup,
    so a `:2049` variant would look like a different source - never emit a port."""
    return "nfs://{}/{}".format(str(host).split(":", 1)[0], str(path).lstrip("/"))


# userdata-relative path matches nsud's NSUserDefaults key on tvOS.
SOURCES_SPECIAL = "special://home/userdata/sources.xml"


def _sources_xml_path():
    return xbmcvfs.translatePath(SOURCES_SPECIAL)


def _read_sources_bytes():
    """Read sources.xml through xbmcvfs - the layer Kodi actually reads. On Apple
    TV that is the NSUserDefaults mirror (a plain POSIX read can see a stale or
    dropped disk copy and make us clobber existing sources); on Fire TV / desktop
    it is the same POSIX file. Returns bytes, or b'' when absent/unreadable."""
    f = None
    try:
        f = xbmcvfs.File(SOURCES_SPECIAL)
        b = f.readBytes()
        return bytes(b) if b else b""
    except Exception:
        return b""
    finally:
        try:
            if f is not None:
                f.close()
        except Exception:
            pass


def _make_source(files, name, path):
    src = ET.SubElement(files, "source")
    ET.SubElement(src, "name").text = name
    p = ET.SubElement(src, "path")
    p.set("pathversion", "1")
    p.text = path
    ET.SubElement(src, "allowsharing").text = "true"


def add_media_sources(interactive=True):
    """Add KodiShare / KodiBackup (mini NFS) + our repo to userdata/sources.xml.
    Preserves existing sources, dedupes on name AND path (a second run is a
    no-op). They appear in File Manager after a Kodi restart."""
    want = [
        (REPO_SOURCE_NAME, REPO_SOURCE_URL),
        (KODI_SHARE_SOURCE_NAME, _nfs_url(MINI_HOST, KODI_SHARE_PATH)),
        (KODI_BACKUP_SOURCE_NAME, _nfs_url(MINI_HOST, KODI_BACKUP_PATH)),
    ]
    try:
        xml_path = _sources_xml_path()
        raw = _read_sources_bytes()
        root = None
        if raw:
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                root = None
        if root is None or root.tag != "sources":
            root = ET.Element("sources")
        files = root.find("files")
        if files is None:
            files = ET.SubElement(root, "files")
        if files.find("default") is None:
            files.insert(0, ET.Element("default"))
        # Consolidate the repo source to a SINGLE entry named .T7B, deduped by URL:
        # a box may carry the old .tony.7.bones AND a newer .T7B on the same url (or two
        # old ones). Keep the first source on REPO_SOURCE_URL, ensure its name is .T7B,
        # and remove any other sources on that same url - so the migration can never
        # leave two entries pointing at the same repo (audit Finding G; the add-loop
        # below dedups by NAME and would not catch a same-url duplicate).
        renamed = 0
        repo_srcs = [
            s
            for s in files.findall("source")
            if (s.findtext("path") or "").strip() == REPO_SOURCE_URL
        ]
        if repo_srcs:
            keep = repo_srcs[0]
            nm = keep.find("name")
            if nm is None:
                nm = ET.SubElement(keep, "name")
            if (nm.text or "").strip() != REPO_SOURCE_NAME:
                nm.text = REPO_SOURCE_NAME
                renamed += 1
            for extra in repo_srcs[1:]:
                files.remove(extra)
                renamed += 1
        have_names = {
            (s.findtext("name") or "").strip() for s in files.findall("source")
        }
        have_paths = {
            (s.findtext("path") or "").strip() for s in files.findall("source")
        }
        added = 0
        for name, path in want:
            if name in have_names or path in have_paths:
                continue
            _make_source(files, name, path)
            have_names.add(name)
            have_paths.add(path)
            added += 1
        if added or renamed:
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(ET.tostring(root, encoding="unicode"))
            # tvOS-safe persist: vector into NSUserDefaults + drop the POSIX dupe
            # on Apple TV via the verified nsud path (a plain POSIX write alone
            # dual-layers the file, so File Manager lists sources twice - the
            # tvOS-restore-duplicate-userdata bug fixed in 2026.07.08.6). A no-op
            # rewrite of identical bytes on Fire TV / desktop.
            from resources.lib.modules import nsud

            nsud.persist_one("sources.xml", log=_log)
        _log("sources added=%d renamed=%d" % (added, renamed))
        if interactive:
            ui.done(
                "Media sources ready (%d added).\n\nKodi caches sources at "
                "startup, so KodiShare and KodiBackup show up in File Manager "
                "after a restart." % added,
                heading=AddonTitle,
            )
        return True
    except Exception as e:  # noqa: BLE001 - never abort the box
        _log("sources failed: %s" % e, xbmc.LOGERROR)
        if interactive:
            ui.error("Could not add media sources: %s" % e, heading=AddonTitle)
        return False


# --------------------------------------------------------------------------- #
# Weather (Multi Weather) - install via Kodi's builtin, then point it at
# Sacramento so it fetches without the interactive geocode search.
# --------------------------------------------------------------------------- #
WEATHER_ADDON = "weather.multi"
WEATHER_PROVIDER_SETTING = "weather.addon"
WEATHER_LOCATION = {
    "loc1_name": "Sacramento, CA, US",
    "loc1_url": "us/ca/sacramento",  # the load-bearing field the add-on fetches on
    "loc1_lat": "38.5816",
    "loc1_lon": "-121.4944",
}


def _set_core_setting(setting_id, value):
    """Set a CORE Kodi setting (weather.addon, lookandfeel.*) via JSON-RPC."""
    resp = xbmc.executeJSONRPC(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "Settings.SetSettingValue",
                "params": {"setting": setting_id, "value": value},
                "id": 1,
            }
        )
    )
    return '"result":true' in (resp or "")


def _is_installed(addon_id):
    try:
        xbmcaddon.Addon(addon_id)
        return True
    except Exception:
        return False


def _weather_settings_path():
    return xbmcvfs.translatePath(
        "special://profile/addon_data/weather.multi/settings.xml"
    )


def _write_weather_settings(settings):
    """Write id->value into Multi Weather's settings.xml, preserving the rest."""
    xml_path = _weather_settings_path()
    os.makedirs(os.path.dirname(xml_path), exist_ok=True)
    root = None
    if os.path.exists(xml_path):
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            root = None
    if root is None or root.tag != "settings":
        root = ET.Element("settings")
        root.set("version", "2")
    by_id = {s.get("id"): s for s in root.findall("setting") if s.get("id")}
    for sid, val in settings.items():
        el = by_id.get(sid)
        if el is None:
            el = ET.SubElement(root, "setting")
            el.set("id", sid)
            by_id[sid] = el
        el.text = val
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(ET.tostring(root, encoding="unicode"))
    # tvOS-safe persist, exactly as _add_sources does for sources.xml. Kodi core
    # loads an add-on's settings.xml through its OWN VFS, so on Apple TV that read
    # is served from NSUserDefaults FIRST (CTVOSFile::Exists/Open check the key
    # before the disk file). A plain POSIX write alone therefore (a) leaves a stale
    # key silently SHADOWING the bytes we just wrote - the weather location never
    # applies, with no error - and (b) dual-layers the file so File Manager lists it
    # twice. This call was missing here while its sibling 90 lines up had it.
    # A no-op rewrite of identical bytes on Fire TV / desktop.
    from resources.lib.modules import nsud

    nsud.persist_one("addon_data/weather.multi/settings.xml", log=_log)


def setup_weather(interactive=True):
    """Install Multi Weather (Kodi's InstallAddon builtin - needs an enabled
    repo that carries it, e.g. the official Kodi repo), set it as the provider,
    and pre-write the Sacramento location."""
    try:
        if not _is_installed(WEATHER_ADDON):
            if interactive:
                with ui.Progress("Installing Multi Weather...", heading=AddonTitle):
                    xbmc.executebuiltin("InstallAddon(%s)" % WEATHER_ADDON, True)
            else:
                xbmc.executebuiltin("InstallAddon(%s)" % WEATHER_ADDON, True)
        installed = _is_installed(WEATHER_ADDON)
        if installed:
            _set_core_setting(WEATHER_PROVIDER_SETTING, WEATHER_ADDON)
            _write_weather_settings(WEATHER_LOCATION)
        _log("weather installed=%s" % installed)
        if interactive:
            if installed:
                ui.done(
                    "Weather is set to Multi Weather (Sacramento, CA).",
                    heading=AddonTitle,
                )
            else:
                ui.error(
                    "Multi Weather did not install. Make sure a repository that "
                    "carries it (the official Kodi repo) is enabled, then retry.",
                    heading=AddonTitle,
                )
        return installed
    except Exception as e:  # noqa: BLE001
        _log("weather failed: %s" % e, xbmc.LOGERROR)
        if interactive:
            ui.error("Weather setup failed: %s" % e, heading=AddonTitle)
        return False


# --------------------------------------------------------------------------- #
# RSS ticker
# --------------------------------------------------------------------------- #
RSS_ENABLE_SETTING = "lookandfeel.enablerssfeeds"


def enable_rss(interactive=True):
    """Turn on the RSS news ticker (Kodi's shipped default feed set stands)."""
    try:
        ok = _set_core_setting(RSS_ENABLE_SETTING, True)
        _log("rss enabled=%s" % ok)
        if interactive:
            ui.done("RSS news ticker enabled.", heading=AddonTitle)
        return ok
    except Exception as e:  # noqa: BLE001
        _log("rss failed: %s" % e, xbmc.LOGERROR)
        if interactive:
            ui.error("RSS setup failed: %s" % e, heading=AddonTitle)
        return False


# --------------------------------------------------------------------------- #
# All three, with one confirm and a restart offer at the end.
# --------------------------------------------------------------------------- #
def setup_all():
    if not ui.confirm(
        "Set up this box now?\n\nAdds the mini's KodiShare/KodiBackup sources, "
        "installs and configures Multi Weather, and turns on the RSS ticker.",
        heading=AddonTitle,
    ):
        return
    with ui.Progress("Setting up this box...", heading=AddonTitle) as p:
        p.message("Adding media sources...")
        add_media_sources(interactive=False)
        p.message("Setting up weather...")
        setup_weather(interactive=False)
        p.message("Enabling RSS ticker...")
        enable_rss(interactive=False)
    ui.ask_restart(
        "Box set up. A restart is needed for the new media sources to appear.",
        heading=AddonTitle,
    )
