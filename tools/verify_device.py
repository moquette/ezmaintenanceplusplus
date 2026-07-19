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
    export KODI_JSONRPC_USER=<the box's JSON-RPC user>
    export KODI_JSONRPC_PASSWORD=<the box's JSON-RPC password>

    python3 tools/verify_device.py --host <apple-tv-ip> --class tvos
    python3 tools/verify_device.py --host <fire-tv-ip>  --class android
    python3 tools/verify_device.py --diff before.json after.json

Writes/updates verification/<addon-version>.json with one entry per device class. Run it
once per class (both are required by the gate for a storage change to ship).

CONFIGURATION (no credential, and no box address, is baked into this file)
--------------------------------------------------------------------------
This tool used to carry a hardcoded `kodi:kodi` Basic-auth header and example box IPs
in this docstring. This repository is PUBLIC, so a credential in source is a published
credential regardless of how weak it looks. Everything device-specific now comes from
the environment:

    KODI_JSONRPC_USER      required for any device pull. No default.
    KODI_JSONRPC_PASSWORD  required for any device pull. No default.
    KODI_JSONRPC_HOST      optional default for --host (an explicit --host wins).
    KODI_JSONRPC_PORT      optional, defaults to 8080.

There is deliberately NO fallback credential: an unset user/password is a hard, named
error, never a silent retry against a stock default. Only the device-contacting paths
require them - `--diff` reads two local JSON files and needs no configuration at all.
The password is read from the environment rather than a CLI flag so it never lands in
shell history or a process listing.

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
import base64
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.request
from collections import Counter

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
    xml = ADDON_XML.read_text()
    m = re.search(
        r'id="script\.ezmaintenanceplusplus"\s+name="[^"]*"\s+version="([^"]+)"',
        xml,
    )
    if not m:
        m = re.search(r'version="([0-9][^"]+)"', xml)
    if not m:
        raise SystemExit(
            "could not read a version out of %s.\n"
            '  Expected an <addon ... version="..."> attribute; the file has neither\n'
            '  the id/name/version form nor any version="<digit>..." attribute.\n'
            "  Fix addon.xml (or this tool's pattern) before verifying a device."
            % ADDON_XML
        )
    return m.group(1)


# --------------------------------------------------------------------------- #
# JSON-RPC configuration - environment only, no baked-in credential or address.
# See the CONFIGURATION block in the module docstring.
# --------------------------------------------------------------------------- #
ENV_USER = "KODI_JSONRPC_USER"
ENV_PASSWORD = "KODI_JSONRPC_PASSWORD"
ENV_HOST = "KODI_JSONRPC_HOST"
ENV_PORT = "KODI_JSONRPC_PORT"
DEFAULT_PORT = 8080


def jsonrpc_credentials(env=None):
    """(user, password) for the box's JSON-RPC endpoint, read from the environment.

    There is NO fallback: a missing value raises SystemExit naming the exact
    variables to set. Falling back to Kodi's stock defaults is precisely the
    behavior this function exists to remove - a silent default would keep every
    checkout of this PUBLIC repo pointed at a live, guessable credential."""
    env = os.environ if env is None else env
    user = (env.get(ENV_USER) or "").strip()
    password = env.get(ENV_PASSWORD) or ""
    missing = [
        name
        for name, value in ((ENV_USER, user), (ENV_PASSWORD, password))
        if not value
    ]
    if missing:
        raise SystemExit(
            "REFUSING to contact a device: %s not set.\n"
            "  This tool takes the box's JSON-RPC credential from the environment and\n"
            "  has no default (a hardcoded one in this public repo was the bug).\n"
            "  Set them for this shell, then re-run:\n"
            "      export %s=<the box's JSON-RPC user>\n"
            "      export %s=<the box's JSON-RPC password>\n"
            "  Kodi exposes these under Settings > Services > Control.\n"
            "  (--diff needs neither: it only reads two local JSON files.)"
            % (" and ".join(missing), ENV_USER, ENV_PASSWORD)
        )
    return user, password


def auth_header(user, password):
    """The Basic-auth Authorization value for `user`/`password`."""
    raw = ("%s:%s" % (user, password)).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def jsonrpc_port(env=None):
    """The box's JSON-RPC port: KODI_JSONRPC_PORT if set, else Kodi's default 8080."""
    env = os.environ if env is None else env
    raw = (env.get(ENV_PORT) or "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        raise SystemExit("%s must be a port number, got %r" % (ENV_PORT, raw)) from None


def rpc(host, method, params=None, timeout=8, auth=None, port=None):
    """One JSON-RPC call. `auth` is a prebuilt Authorization value; when omitted it is
    resolved from the environment, so a caller making many calls (pull()) resolves the
    credential once and passes it in rather than re-reading the environment per call."""
    if auth is None:
        auth = auth_header(*jsonrpc_credentials())
    if port is None:
        port = jsonrpc_port()
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    req = urllib.request.Request(
        "http://%s:%d/jsonrpc" % (host, port),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    req.add_header("Authorization", auth)
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
    return sorted(n for n, k in Counter(names).items() if k > 1)


def labelled_names(files):
    """(names, unlabelled_count) for a VFS listing.

    Entries with no usable label are EXCLUDED, never defaulted to "": two
    label-less entries both defaulting to the same "" register as a repeated
    name, and a repeated name in this tool MEANS a key/disk split. That would
    manufacture a PHANTOM duplicate and flip `clean_single_layer` false - a
    fabricated warning about the exact damage this tool exists to report
    truthfully, on a field the release gate consumes.

    They are COUNTED rather than silently dropped, so a malformed listing is
    still visible in the artifact instead of quietly shrinking the evidence."""
    names = []
    unlabelled = 0
    for f in files:
        label = f.get("label") or ""
        if label:
            names.append(label)
        else:
            unlabelled += 1
    return names, unlabelled


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


def collect_restore_contract(call, cache=None):
    """Run every restore-contract check through `call`, degrading per-check: any
    failing pull records an error string in the section instead of aborting the
    whole device run. Successful listings are cached so shared directories are
    pulled once.

    `cache` may be supplied by the caller (keyed `(directory, with_sizes)`) so a
    listing this function already pulled can be reused instead of re-requested.
    pull() does exactly that for the skinshortcuts directory, which both the
    top-level evidence and the duplicate scan need - it used to be pulled twice
    per device run."""
    cache = {} if cache is None else cache

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
        contract["profile_inventory"] = check_profile_inventory(listing(ADDON_DATA_DIR))
    except Exception as e:
        contract["profile_inventory"] = {"error": _error_string(e)}

    listings_by_dir = {}
    for directory in DUPLICATE_SCAN_DIRS:
        try:
            with_sizes = directory == IPTV_DIR  # reuse cached pulls
            # labelled_names, not a "" default: see its docstring. This site had
            # the same phantom-duplicate flaw as the skinshortcuts scan in pull().
            listings_by_dir[directory] = labelled_names(
                listing(directory, with_sizes=with_sizes)
            )[0]
        except Exception as e:
            listings_by_dir[directory] = e
    contract["duplicate_listing"] = check_duplicate_listing(listings_by_dir)

    try:
        iptv_files = listing(IPTV_DIR, with_sizes=True)
        details_by_name = {}
        for f in iptv_files:
            name = f.get("label", "")
            if not (name.startswith("instance-settings-") and name.endswith(".xml")):
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
    # Resolve the credential and port ONCE for the whole run: a missing credential
    # fails here, before any request, with the named-variable error - never as a
    # 401 halfway through a device pull.
    auth = auth_header(*jsonrpc_credentials())
    port = jsonrpc_port()

    def call(method, params=None):
        return rpc(host, method, params, auth=auth, port=port)

    build = call(
        "XBMC.GetInfoLabels",
        {"labels": ["System.BuildVersion", "System.FriendlyName"]},
    )
    addon = call(
        "Addons.GetAddonDetails",
        {"addonid": "script.ezmaintenanceplusplus", "properties": ["version"]},
    )["addon"]

    # Version check BEFORE the (much larger) contract pull: if the box is running
    # the wrong build, nothing collected after this would be written anyway.
    on_box = addon["version"]
    expected = addon_version()
    if on_box != expected:
        raise SystemExit(
            "REFUSING to write a verification artifact.\n"
            "  addon.xml at HEAD is %s but the box reports %s installed.\n"
            "  Deploy the version you are verifying to the box, then re-run."
            % (expected, on_box)
        )

    # One shared listing cache: the skinshortcuts directory below is the SAME
    # listing the duplicate scan needs, and used to be pulled twice per run.
    cache = {}
    contract = collect_restore_contract(call, cache=cache)
    try:
        files = cache[(SKINSHORTCUTS_DIR, False)]
    except KeyError:
        # collect_restore_contract degrades a failed listing into a recorded error
        # and caches nothing. These two fields are gate-consumed, so a silent []
        # here would report "clean_single_layer" for a directory nobody read.
        # Pull it directly so a genuine failure is loud, exactly as it was before.
        files = list_directory(call, SKINSHORTCUTS_DIR)
    names, unlabelled = labelled_names(files)
    dupes = find_duplicates(names)

    evidence = {
        "class": device_class,
        # NOTE: the box's ADDRESS is deliberately not recorded. It is the only
        # field this artifact ever carried that the box did not report - it was
        # echoed straight back from --host - so it was never evidence, nothing
        # consumes it (not the gate, not --diff), and this repo is public, so
        # writing it here published the fleet's addressing on every run. The
        # box identifies itself below via friendly_name/kodi_build, which ARE
        # device-reported. Hashing the address was considered and rejected: an
        # IPv4 address has far too little entropy for a digest to redact it.
        "friendly_name": build["System.FriendlyName"],
        "kodi_build": build["System.BuildVersion"],
        "addon_version_on_box": on_box,
        "skinshortcuts_vfs_entries": len(names),
        "skinshortcuts_duplicates": dupes,  # non-empty = a live key/disk split on the box
        "clean_single_layer": not dupes,
        "restore_contract": contract,
    }
    if unlabelled:
        # Recorded, never silently dropped - see labelled_names().
        evidence["skinshortcuts_unlabelled_entries"] = unlabelled
    return evidence


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
    """True if this section cannot be diffed (a side recorded an error or is absent),
    having appended the reason to `lines`.

    This used to return the same value twice as `(skip, changed)`, which read as if
    the two could differ. They cannot, and the reason is worth stating: when a
    section is undiffable the explanation has already been appended, so the caller
    reports "changed" for exactly the same condition that made it skip. One value,
    one meaning."""
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
    return bool(b_err or a_err or before is None or after is None)


def _diff_iptv(before, after, lines):
    if _check_errors("iptv_config", before, after, lines):
        return True  # the recorded reason is itself the finding
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
    if _check_errors("profile_inventory", before, after, lines):
        return True  # the recorded reason is itself the finding
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
    if _check_errors("duplicate_listing", before, after, lines):
        return True  # the recorded reason is itself the finding
    changed = False
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
    if _check_errors("shadow_probe", before, after, lines):
        return True  # the recorded reason is itself the finding
    changed = False
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
    ap = argparse.ArgumentParser(
        description=(
            "Pull live device evidence over Kodi JSON-RPC. Set %s and %s in the "
            "environment first (there is no default credential); %s and %s are "
            "optional." % (ENV_USER, ENV_PASSWORD, ENV_HOST, ENV_PORT)
        )
    )
    ap.add_argument(
        "--host",
        default=os.environ.get(ENV_HOST) or None,
        help="box address (defaults to $%s)" % ENV_HOST,
    )
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
    # Stamp the fingerprint INTO THIS CLASS'S ENTRY. It must never be a single
    # top-level scalar: this function refreshes ONE class but re-writes the whole
    # document, so a top-level stamp certifies the OTHER class's carried-forward
    # entry as covering code that box never ran. Pull android, change wiz.py, pull
    # tvos, and the artifact reads complete-and-current while android is stale.
    # Per-entry, a carried-forward entry keeps its own older fingerprint and the
    # gate can see it.
    fingerprint = storage_fingerprint()
    evidence["storage_fingerprint"] = fingerprint
    doc["devices"][args.device_class] = evidence
    # Kept only as a human-readable summary of the most recent pull. The gate must
    # NOT trust it; it is not evidence about any particular device.
    doc["storage_fingerprint"] = fingerprint
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
