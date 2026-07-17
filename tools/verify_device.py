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
    python3 tools/verify_device.py --diff before.json after.json

Writes/updates verification/<addon-version>.json with one entry per device class. Run it
once per class (both are required by the gate for a storage change to ship).

RESTORE CONTRACT (added after 10 restore data-loss regressions passed "verification")
-------------------------------------------------------------------------------------
The original pull only proved the box was up and running the right build. It said
NOTHING about whether a backup/restore preserved the profile - which is exactly what
regressed, ten times. Each device entry now also carries a "restore_contract" section:

  - iptv_config        instance-settings-*.xml names + sizes under
                       addon_data/pvr.iptvsimple (an empty list is a recorded finding)
  - profile_inventory  counts + names of top-level special://profile entries and
                       addon_data subdirs (a compact survival fingerprint)
  - duplicate_listing  double-listed names in special://profile, pvr.iptvsimple, and
                       script.skinshortcuts (the tvOS key/disk dual-layer symptom)
  - shadow_probe       guisettings.xml existence vs reported size from two JSON-RPC
                       vantage points (size-0-but-exists is the known VFS
                       foreign-writer symptom on tvOS)

Every check degrades per-device: a failing call records an error string in the
artifact instead of aborting the run. All pre-existing fields are unchanged.

The --diff mode compares the restore_contract sections of two artifacts (a pull taken
before a restore and one taken after) and prints what appeared, vanished, or changed,
so "the restore preserved the profile" is a machine-diffable claim, not prose.

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
NSUB = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/nsub.py"
ONETAP = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/onetap.py"
WIZ = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/wiz.py"
VERIFY_DIR = ROOT / "verification"

# The source whose change demands a fresh device run. Keep in lockstep with the gate test.
#
# SCOPE (widened 2026-07-16; original GAP 3 review 2026-07-14): the 2026-07-14 scope
# was (nsud.py, boxsetup.py), justified because the other xbmcvfs importers "only
# stage/verify/delete backup ZIPs" with no NSUserDefaults-shadow risk. That review's
# own tracked follow-up condition ("if one of those modules ever grows a NEW
# vectoring-relevant write ... add it here") has triggered: onetap.py now DELETES
# NSUserDefaults keys (_wipe_nsud_keys, the two-layer wipe), nsud.py hosts the
# two-layer IPTV instance sweep that wiz.restore() delegates to, nsub.py's plist
# capture is the only source of NSUD-resident settings in a tvOS backup (its silent
# omission WAS the 2026-07-08 incident), and wiz.py orchestrates when each of those
# runs. The AST write-lint cannot cover these (it lints raw WRITES, not
# deletes/wipes/captures), so all five are fingerprinted. This deliberately
# invalidates every pre-2026-07-16 verification artifact: the storage contract
# genuinely changed, and a fresh two-class device run is exactly what is owed.
CONTRACT_FILES = (NSUD, BOXSETUP, NSUB, ONETAP, WIZ)


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


# --------------------------------------------------------------------------- #
# Restore-contract checks
#
# Pure logic is kept separate from transport: every check takes data (or a
# `call(method, params)` callable) so tests exercise it with canned JSON-RPC
# responses and zero network. The live run passes a lambda over rpc().
# --------------------------------------------------------------------------- #

PROFILE_DIR = "special://profile/"
ADDON_DATA_DIR = "special://profile/addon_data/"
IPTV_DIR = "special://profile/addon_data/pvr.iptvsimple/"
SKINSHORTCUTS_DIR = "special://profile/addon_data/script.skinshortcuts/"
GUISETTINGS = "special://profile/guisettings.xml"

# Directories scanned for double-listed names (the tvOS NSUserDefaults/POSIX
# dual-layer symptom). skinshortcuts stays for continuity with the original check.
# LIVE-VERIFIED 2026-07-16 (Fire TV, Kodi 21.3): Files.GetDirectory and
# Files.GetFileDetails REFUSE the profile root and its direct files
# (special://profile/, guisettings.xml, keymaps/, Database/) with Invalid
# params, while the whole addon_data tree IS reachable. The checks therefore
# scope to addon_data (which is also exactly where the IPTV/profile-survival
# evidence lives) and RECORD the root as unreachable rather than erroring.
DUPLICATE_SCAN_DIRS = (ADDON_DATA_DIR, IPTV_DIR, SKINSHORTCUTS_DIR)


def find_duplicates(names):
    """Names appearing more than once in a single VFS listing = a key/disk split."""
    from collections import Counter

    return sorted(n for n, k in Counter(names).items() if k > 1)


def _error_string(exc):
    return "%s: %s" % (type(exc).__name__, exc)


def list_directory(call, directory, with_sizes=False):
    params = {"directory": directory, "media": "files"}
    if with_sizes:
        params["properties"] = ["size"]
    result = call("Files.GetDirectory", params) or {}
    return result.get("files") or []


def check_iptv_config(files):
    """instance-settings-*.xml names + sizes. An empty list is a FINDING (the exact
    thing a bad restore produces), recorded as such - never a crash."""
    instance = sorted(
        (
            {"name": f.get("label", ""), "size": f.get("size")}
            for f in files
            if f.get("label", "").startswith("instance-settings-")
            and f.get("label", "").endswith(".xml")
        ),
        key=lambda e: e["name"],
    )
    return {
        "directory": IPTV_DIR,
        "entries": len(files),
        "instance_settings": instance,
        "empty": not instance,
    }


def check_profile_inventory(addon_data_files):
    """Compact survival fingerprint: which add-ons have data under the profile.
    Diffing this before/after a restore shows what vanished. The profile ROOT is
    not remotely listable on live Kodi 21 (Invalid params), so the fingerprint is
    addon_data-scoped and says so explicitly instead of erroring."""
    addon_data_names = sorted(f.get("label", "") for f in addon_data_files)
    return {
        "profile_root": "unreachable over JSON-RPC (Kodi refuses to list it)",
        "addon_data_count": len(addon_data_names),
        "addon_data_entries": addon_data_names,
    }


def check_duplicate_listing(listings_by_dir):
    """Generalized dual-layer scan. listings_by_dir maps directory -> list of names
    (or an Exception recorded upstream). Non-empty duplicate list = live split."""
    duplicates = {}
    errors = {}
    for directory, names in listings_by_dir.items():
        if isinstance(names, Exception):
            errors[directory] = _error_string(names)
            continue
        duplicates[directory] = find_duplicates(names)
    out = {
        "duplicates": duplicates,
        "clean": not any(duplicates.values()),
    }
    if errors:
        out["errors"] = errors
    return out


def check_shadow_probe(iptv_files, details_by_name):
    """Each IPTV instance file seen from two JSON-RPC vantage points: the directory
    listing (existence + size) and Files.GetFileDetails (size). A file that EXISTS
    but reports size 0 is the known tvOS VFS foreign-writer symptom - the disk file
    is a husk and the real content lives (only) in an NSUserDefaults key.
    (guisettings.xml is NOT remotely probeable: live Kodi refuses JSON-RPC access
    to the profile root's files, so the probe uses the reachable, restore-critical
    IPTV instance files instead.)"""
    probes = []
    husk = False
    for f in iptv_files:
        name = f.get("label", "")
        if not (name.startswith("instance-settings-") and name.endswith(".xml")):
            continue
        listed_size = f.get("size")
        details_size = details_by_name.get(name)
        sizes = [s for s in (listed_size, details_size) if s is not None]
        zero = bool(sizes and min(sizes) == 0)
        husk = husk or zero
        probes.append(
            {
                "file": IPTV_DIR + name,
                "listed_size": listed_size,
                "details_size": details_size,
                "size_zero_but_exists": zero,
            }
        )
    return {
        "probed": probes,
        "size_zero_but_exists": husk,
        "note": "profile-root files (guisettings.xml) are not remotely probeable",
    }


def collect_restore_contract(call):
    """Run every restore-contract check through `call`, degrading per-check: any
    failing pull records an error string in the section instead of aborting the
    whole device run. Successful listings are cached so shared directories are
    pulled once."""
    cache = {}

    def listing(directory, with_sizes=False):
        key = (directory, with_sizes)
        if key not in cache:
            cache[key] = list_directory(call, directory, with_sizes)
        return cache[key]

    contract = {}

    try:
        contract["iptv_config"] = check_iptv_config(listing(IPTV_DIR, with_sizes=True))
    except Exception as e:
        contract["iptv_config"] = {"error": _error_string(e)}

    try:
        contract["profile_inventory"] = check_profile_inventory(
            listing(ADDON_DATA_DIR)
        )
    except Exception as e:
        contract["profile_inventory"] = {"error": _error_string(e)}

    listings_by_dir = {}
    for directory in DUPLICATE_SCAN_DIRS:
        try:
            with_sizes = directory == IPTV_DIR  # reuse cached pulls
            listings_by_dir[directory] = [
                f.get("label", "") for f in listing(directory, with_sizes=with_sizes)
            ]
        except Exception as e:
            listings_by_dir[directory] = e
    contract["duplicate_listing"] = check_duplicate_listing(listings_by_dir)

    try:
        iptv_files = listing(IPTV_DIR, with_sizes=True)
        details_by_name = {}
        for f in iptv_files:
            name = f.get("label", "")
            if not (
                name.startswith("instance-settings-") and name.endswith(".xml")
            ):
                continue
            try:
                details = call(
                    "Files.GetFileDetails",
                    {
                        "file": IPTV_DIR + name,
                        "media": "files",
                        "properties": ["size"],
                    },
                )
                details_by_name[name] = (
                    (details or {}).get("filedetails", {}).get("size")
                )
            except Exception:
                details_by_name[name] = None  # secondary vantage point only
        contract["shadow_probe"] = check_shadow_probe(iptv_files, details_by_name)
    except Exception as e:
        contract["shadow_probe"] = {"error": _error_string(e)}

    return contract


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
        "restore_contract": collect_restore_contract(
            lambda method, params=None: rpc(host, method, params)
        ),
    }


# --------------------------------------------------------------------------- #
# --diff mode: prove (or disprove) that a restore preserved the profile by
# diffing the restore_contract sections of two artifacts.
# --------------------------------------------------------------------------- #


def _fmt_size(size):
    return "?" if size is None else str(size)


def _diff_names(label, before_names, after_names, lines):
    before_set, after_set = set(before_names), set(after_names)
    changed = False
    for name in sorted(before_set - after_set):
        lines.append("  [%s] VANISHED: %s" % (label, name))
        changed = True
    for name in sorted(after_set - before_set):
        lines.append("  [%s] appeared: %s" % (label, name))
        changed = True
    return changed


def _check_errors(label, before, after, lines):
    """Returns (skip, changed). A side that recorded an error cannot be diffed."""
    b_err = isinstance(before, dict) and before.get("error")
    a_err = isinstance(after, dict) and after.get("error")
    if b_err:
        lines.append("  [%s] before recorded an error: %s" % (label, b_err))
    if a_err:
        lines.append("  [%s] after recorded an error: %s" % (label, a_err))
    if before is None:
        lines.append("  [%s] missing from before artifact" % label)
    if after is None:
        lines.append("  [%s] missing from after artifact" % label)
    skip = bool(b_err or a_err or before is None or after is None)
    return skip, skip


def _diff_iptv(before, after, lines):
    skip, changed = _check_errors("iptv_config", before, after, lines)
    if skip:
        return changed
    b_sizes = {e["name"]: e.get("size") for e in before.get("instance_settings", [])}
    a_sizes = {e["name"]: e.get("size") for e in after.get("instance_settings", [])}
    changed = _diff_names("iptv_config", b_sizes, a_sizes, lines)
    for name in sorted(set(b_sizes) & set(a_sizes)):
        if b_sizes[name] != a_sizes[name]:
            lines.append(
                "  [iptv_config] size changed: %s %s -> %s"
                % (name, _fmt_size(b_sizes[name]), _fmt_size(a_sizes[name]))
            )
            changed = True
    if not before.get("empty") and after.get("empty"):
        lines.append(
            "  [iptv_config] REGRESSION: instance settings present before, EMPTY after"
        )
        changed = True
    elif before.get("empty") and not after.get("empty"):
        lines.append("  [iptv_config] instance settings empty before, present after")
        changed = True
    return changed


def _diff_inventory(before, after, lines):
    skip, changed = _check_errors("profile_inventory", before, after, lines)
    if skip:
        return changed
    changed = _diff_names(
        "addon_data",
        before.get("addon_data_entries", []),
        after.get("addon_data_entries", []),
        lines,
    )
    for key, label in (("addon_data_count", "addon_data entries"),):
        if before.get(key) != after.get(key):
            lines.append(
                "  [profile_inventory] %s: %s -> %s"
                % (label, before.get(key), after.get(key))
            )
            changed = True
    return changed


def _diff_duplicates(before, after, lines):
    skip, changed = _check_errors("duplicate_listing", before, after, lines)
    if skip:
        return changed
    b_dupes = before.get("duplicates", {})
    a_dupes = after.get("duplicates", {})
    for directory in sorted(set(b_dupes) | set(a_dupes)):
        b_set = set(b_dupes.get(directory, []))
        a_set = set(a_dupes.get(directory, []))
        for name in sorted(a_set - b_set):
            lines.append(
                "  [duplicate_listing] NEW dual-layer split in %s: %s"
                % (directory, name)
            )
            changed = True
        for name in sorted(b_set - a_set):
            lines.append(
                "  [duplicate_listing] split cleared in %s: %s" % (directory, name)
            )
            changed = True
    return changed


def _diff_shadow(before, after, lines):
    skip, changed = _check_errors("shadow_probe", before, after, lines)
    if skip:
        return changed
    b_probes = {p.get("file"): p for p in before.get("probed", [])}
    a_probes = {p.get("file"): p for p in after.get("probed", [])}
    for f in sorted(set(b_probes) | set(a_probes)):
        b, a = b_probes.get(f, {}), a_probes.get(f, {})
        for key in ("listed_size", "details_size", "size_zero_but_exists"):
            if b.get(key) != a.get(key):
                lines.append(
                    "  [shadow_probe] %s %s: %s -> %s"
                    % (f, key, b.get(key), a.get(key))
                )
                changed = True
    if after.get("size_zero_but_exists") and not before.get("size_zero_but_exists"):
        lines.append(
            "  [shadow_probe] REGRESSION: an instance file now exists with size 0 "
            "(the tvOS VFS foreign-writer symptom)"
        )
        changed = True
    return changed


def diff_restore_contract(before_doc, after_doc):
    """Human-readable diff of the restore_contract sections of two artifacts.
    Returns a list of lines; per-device-class, so a two-class artifact diffs both."""
    lines = []
    b_devices = before_doc.get("devices", {})
    a_devices = after_doc.get("devices", {})
    classes = sorted(set(b_devices) | set(a_devices))
    if not classes:
        return ["no device entries in either artifact"]
    for cls in classes:
        lines.append("== device class: %s ==" % cls)
        if cls not in b_devices:
            lines.append("  only present in the after artifact")
            continue
        if cls not in a_devices:
            lines.append("  only present in the before artifact")
            continue
        before = b_devices[cls].get("restore_contract")
        after = a_devices[cls].get("restore_contract")
        if before is None or after is None:
            for side, contract in (("before", before), ("after", after)):
                if contract is None:
                    lines.append(
                        "  %s artifact has no restore_contract section "
                        "(produced by an older verify_device.py) - cannot diff" % side
                    )
            continue
        changed = _diff_iptv(before.get("iptv_config"), after.get("iptv_config"), lines)
        changed |= _diff_inventory(
            before.get("profile_inventory"), after.get("profile_inventory"), lines
        )
        changed |= _diff_duplicates(
            before.get("duplicate_listing"), after.get("duplicate_listing"), lines
        )
        changed |= _diff_shadow(
            before.get("shadow_probe"), after.get("shadow_probe"), lines
        )
        if not changed:
            lines.append("  restore_contract unchanged: the profile survived intact")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--class", dest="device_class", choices=["tvos", "android"])
    ap.add_argument(
        "--diff",
        nargs=2,
        metavar=("BEFORE_JSON", "AFTER_JSON"),
        help="diff the restore_contract sections of two artifacts (no device contact)",
    )
    args = ap.parse_args()

    if args.diff:
        before = json.loads(pathlib.Path(args.diff[0]).read_text())
        after = json.loads(pathlib.Path(args.diff[1]).read_text())
        for line in diff_restore_contract(before, after):
            print(line)
        return

    if not args.host or not args.device_class:
        ap.error("--host and --class are required unless --diff is used")

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
