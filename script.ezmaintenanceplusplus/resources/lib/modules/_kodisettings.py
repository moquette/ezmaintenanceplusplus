"""Re-apply Kodi settings through the JSON-RPC API after a restore.

Why this exists: on iOS/tvOS, Kodi mirrors guisettings.xml in NSUserDefaults and
rewrites the file from that mirror on boot - so a file-only restore of guisettings.xml
is silently reverted, which is why a restored Apple TV came up "empty". The official
Backup add-on (robweber/xbmcbackup) works around this by applying settings through
Settings.SetSettingValue, which updates Kodi's LIVE store so the values persist. We do
the same, reading the values from the just-restored guisettings.xml and coercing each
to the type the live setting expects (so it works with existing backups, no new format).
On Fire TV / Android this is harmless reinforcement; on tvOS it's what makes restore
actually stick.
"""

import json
import os
import xml.etree.ElementTree as ET

import xbmc


def _rpc(method, params):
    req = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return json.loads(xbmc.executeJSONRPC(json.dumps(req)))


def _live_settings():
    """Return {id: setting_dict} for every setting, used for type + change detection."""
    resp = _rpc("Settings.GetSettings", {"level": "expert"})
    out = {}
    for s in resp.get("result", {}).get("settings", []):
        sid = s.get("id")
        if sid:
            out[sid] = s
    return out


def _coerce(raw, typ):
    """Coerce a guisettings.xml text value to the type Settings.SetSettingValue wants."""
    if typ == "boolean":
        return str(raw).lower() in ("true", "1", "yes", "on")
    if typ == "integer":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if typ == "number":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return "" if raw is None else str(raw)  # string / path / addon / etc.


def apply_guisettings(guisettings_path):
    """Push each value from a restored guisettings.xml into Kodi's live settings via
    JSON-RPC so the restore survives (notably tvOS). Returns the count applied."""
    if not os.path.exists(guisettings_path):
        return 0
    try:
        live = _live_settings()
    except Exception:
        return 0
    try:
        root = ET.parse(guisettings_path).getroot()
    except Exception:
        return 0

    applied = 0
    for node in root.iter("setting"):
        sid = node.get("id")
        if not sid or sid not in live:
            continue
        meta = live[sid]
        if meta.get("type") == "action":
            continue
        val = _coerce(node.text, meta.get("type"))
        if val is None or meta.get("value") == val:
            continue
        try:
            resp = _rpc("Settings.SetSettingValue", {"setting": sid, "value": val})
            if resp.get("result") is True:
                applied += 1
        except Exception:
            pass
    return applied
