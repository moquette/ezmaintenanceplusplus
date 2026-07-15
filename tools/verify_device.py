#!/usr/bin/env python3
"""Produce a device-verification artifact by PULLING live evidence off a real box.

WHY THIS EXISTS
---------------
On 2026-07-14 an incident doc was written saying a storage fix was "Hardware-confirmed on
ATV1". It was not: the build had never been installed on any box, and the menu had been
repaired by hand. A "verified" claim satisfiable by typing prose is worth nothing - this
project's whole failure mode is "shipped confident, wrong on the device."

So verification is not a sentence a human writes. It is this script connecting to the box
over Kodi JSON-RPC and recording what the box ITSELF reports:

  - System.BuildVersion         (the device's Kodi build - it says it, we don't)
  - the installed addon version (Addons.GetAddonDetails)
  - the live skinshortcuts VFS listing (the box's ACTUAL storage state, including whether
    any userdata file is double-listed = a key/disk split - the exact damage we ship to fix)

None of that can be produced without a box answering. The artifact also fingerprints the
storage-contract source at HEAD, so a stale artifact cannot cover new code (the gate test
checks the fingerprint matches the tree it is committed with).

tvOS has no adb; JSON-RPC is the portable device channel (memory: tvos-atv-remote-debug).
For deeper tvOS pulls (container files) the devicectl/pymobiledevice3 path exists; this
tool uses JSON-RPC because it works identically on Fire TV and Apple TV.

USAGE
-----
    python3 tools/verify_device.py --host 192.168.7.183 --class tvos
    python3 tools/verify_device.py --host 192.168.7.162 --class android

Writes/updates verification/<addon-version>.json with one entry per device class. Run it
once per class (both are required by the gate for a storage change to ship).

This tool CANNOT be satisfied by hand: it fails unless a box answers and its reported addon
version matches addon.xml at HEAD. Faking it means editing this file to lie about a live
pull, which is a visible, reviewable act - not filling in a template.
"""

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDON_XML = ROOT / "script.ezmaintenanceplusplus/addon.xml"
NSUD = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/nsud.py"
BOXSETUP = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/boxsetup.py"
VERIFY_DIR = ROOT / "verification"

# The source whose change demands a fresh device run. Keep in lockstep with the gate test.
#
# SCOPE DECISION (GAP 3, reviewed 2026-07-14): nsud.py + boxsetup.py are the ONLY two
# modules that WRITE userdata through the tvOS vectoring path (nsud.py IS the vectoring
# chokepoint; boxsetup.py is the module the 2026-07-14 incident and its follow-up review
# found doing raw userdata writes). Other xbmcvfs/special://profile-touching modules
# (control.py, wiz.py, maintenance.py, onetap.py) were audited when this decision was
# made: none of them write a userdata/addon_data XML outside the sanctioned nsud path -
# they stage/verify/delete backup ZIPs (onetap.py's _verify_vfs reads a zip header;
# wiz.py stages+copies backup archives; control.py/maintenance.py only translatePath a
# few directories) - operations that behave identically on every Kodi platform and are
# NOT subject to the NSUserDefaults-shadow hazard this gate exists to catch. They are
# already covered for the ACTUAL risk (an unguarded raw userdata write) by
# tests/test_no_raw_userdata_writer.py, whose AST lint walks EVERY .py file in the
# add-on (except this chokepoint) - not just these two.
#
# Widening CONTRACT_FILES to include those four modules was considered and deliberately
# NOT done: it would invalidate every existing verification/<version>.json fingerprint
# (forcing an immediate re-run on both device classes) for zero new coverage, since the
# chokepoint lint already scans them and reports nothing to catch. If a future change
# adds a genuine raw userdata write to one of those files, the chokepoint lint catches
# it immediately regardless of this list. Tracked follow-up: if one of those modules
# ever grows a NEW vectoring-relevant write (not just VFS-generic file I/O), add it here
# AND re-run tools/verify_device.py against both device classes in the same change.
CONTRACT_FILES = (NSUD, BOXSETUP)


def storage_fingerprint():
    """A stable hash of the storage-contract source. If this changes, the last device run
    no longer covers the code and the gate demands a new one."""
    h = hashlib.sha256()
    for f in sorted(CONTRACT_FILES):
        h.update(f.read_bytes())
    return h.hexdigest()


def addon_version():
    import re

    m = re.search(
        r'id="script\.ezmaintenanceplusplus"\s+name="[^"]*"\s+version="([^"]+)"',
        ADDON_XML.read_text(),
    )
    if not m:
        m = re.search(r'version="([0-9][^"]+)"', ADDON_XML.read_text())
    return m.group(1)


def rpc(host, method, params=None, timeout=8):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    req = urllib.request.Request(
        "http://%s:8080/jsonrpc" % host,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    import base64

    req.add_header("Authorization", "Basic " + base64.b64encode(b"kodi:kodi").decode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    if "error" in d:
        raise RuntimeError("%s -> %s" % (method, d["error"]))
    return d["result"]


def pull(host, device_class):
    build = rpc(
        host,
        "XBMC.GetInfoLabels",
        {"labels": ["System.BuildVersion", "System.FriendlyName"]},
    )
    addon = rpc(
        host,
        "Addons.GetAddonDetails",
        {"addonid": "script.ezmaintenanceplusplus", "properties": ["version"]},
    )["addon"]
    listing = rpc(
        host,
        "Files.GetDirectory",
        {
            "directory": "special://profile/addon_data/script.skinshortcuts/",
            "media": "files",
        },
    )
    names = [f["label"] for f in listing.get("files", [])]
    from collections import Counter

    dupes = sorted(n for n, k in Counter(names).items() if k > 1)

    on_box = addon["version"]
    expected = addon_version()
    if on_box != expected:
        raise SystemExit(
            "REFUSING to write a verification artifact.\n"
            "  addon.xml at HEAD is %s but the box reports %s installed.\n"
            "  Deploy the version you are verifying to the box, then re-run."
            % (expected, on_box)
        )
    return {
        "class": device_class,
        "host": host,
        "friendly_name": build["System.FriendlyName"],
        "kodi_build": build["System.BuildVersion"],
        "addon_version_on_box": on_box,
        "skinshortcuts_vfs_entries": len(names),
        "skinshortcuts_duplicates": dupes,  # non-empty = a live key/disk split on the box
        "clean_single_layer": not dupes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument(
        "--class", dest="device_class", required=True, choices=["tvos", "android"]
    )
    args = ap.parse_args()

    evidence = pull(args.host, args.device_class)
    version = addon_version()
    VERIFY_DIR.mkdir(exist_ok=True)
    path = VERIFY_DIR / ("%s.json" % version)
    doc = (
        json.loads(path.read_text())
        if path.exists()
        else {
            "version": version,
            "storage_fingerprint": storage_fingerprint(),
            "devices": {},
        }
    )
    # Always re-stamp the fingerprint to the current tree: a device run certifies the code
    # as it stands right now.
    doc["storage_fingerprint"] = storage_fingerprint()
    doc["devices"][args.device_class] = evidence
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print("wrote %s" % path.relative_to(ROOT))
    print(json.dumps(evidence, indent=2))
    if evidence["skinshortcuts_duplicates"]:
        print(
            "\nWARNING: this box has a live key/disk split: %s"
            % evidence["skinshortcuts_duplicates"],
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
