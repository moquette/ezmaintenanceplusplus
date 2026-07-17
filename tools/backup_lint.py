"""Backup-archive analyzer for EZ Maintenance++ backups.

Host-side ONLY: runs on the Mac with plain python3 against a local backup zip.
It never touches a device. It answers, BEFORE a restore is trusted on another
box: "is this zip a true, portable full backup?"

The fleet restores backups ACROSS tvOS (Apple TV) and Fire OS (Android) boxes,
so beyond completeness this lints for portability (device-absolute IPTV paths
that only exist on the source box) and secret hygiene (the add-on's own
settings.xml carries the Dropbox token and must never ride inside a backup).

Anchor model (mirrors resources/lib/modules/wiz.py):
- HOME-anchored (a full backup): members live under userdata/ + addons/ (+ the
  other allowed home-level dirs). On restore, a home-anchored member whose
  first path segment is NOT an allowed home-level dir is SILENTLY SKIPPED by
  _extract_skip - so mixed/unknown roots are a hard FAIL here.
- USERDATA-anchored (a settings backup): bare userdata contents, no userdata/
  prefix. Everything extracts under userdata/, nothing is skipped.

Usage:
    python3 tools/backup_lint.py <backup.zip> [--json]

Exit code 0 iff no check FAILed. --json emits machine-readable results.
"""

import argparse
import io
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

# Mirrors wiz._HOME_ALLOWED_TOP: the only top-level dirs that legitimately live
# at special://home. Anything else in a home-anchored archive is stray content
# that the restore's _extract_skip silently drops.
HOME_ALLOWED_TOP = frozenset({"userdata", "addons", "media", "system", "temp"})

# The manifest CreateZip embeds at the archive root (older backups predate it).
MANIFEST_NAME = "backup_manifest.json"

# Mirrors nsub._SECRET_TAIL: the add-on's own settings.xml carries the source
# box's Dropbox refresh token. Suffix-matched so per-profile copies are caught.
SECRET_TAIL = "addon_data/script.ezmaintenanceplusplus/settings.xml"

IPTV_SUBTREE = "addon_data/pvr.iptvsimple/"

# URL schemes that resolve the same from any box on the fleet network.
PORTABLE_SCHEMES = ("nfs://", "smb://", "http://", "https://")

# The pvr.iptvsimple instance-settings fields that name a playlist/EPG source.
IPTV_PATH_FIELDS = ("m3uPath", "m3uUrl", "epgPath", "epgUrl")

# addon.xml markers that indicate a platform-binary add-on (per-platform
# library attributes, or the binary add-on extension point family).
BINARY_ADDON_XML_RE = re.compile(
    r'(library_[a-z0-9_]+\s*=)|(point\s*=\s*"kodi\.binary)'
)
NATIVE_LIB_SUFFIXES = (".so", ".dylib", ".pyd", ".dll")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_SEVERITY = {PASS: 0, WARN: 1, FAIL: 2}


def _result(check, verdict, reason, details=None):
    out = {"check": check, "verdict": verdict, "reason": reason}
    if details is not None:
        out["details"] = details
    return out


def _norm(name):
    return (name or "").replace("\\", "/").lstrip("/")


def _first_seg(name):
    return _norm(name).split("/", 1)[0]


def _worst(verdicts):
    return max(verdicts, key=lambda v: _SEVERITY[v]) if verdicts else PASS


def archive_anchor(names):
    """Mirrors wiz._archive_anchor: 'home' if any member sits under userdata/
    or addons/, else 'userdata'."""
    for raw in names:
        if _first_seg(raw) in ("userdata", "addons"):
            return "home"
    return "userdata"


def check_anchor(names, anchor):
    if not names:
        return _result("anchor", FAIL, "empty archive: no members to classify")
    if anchor == "userdata":
        return _result(
            "anchor",
            PASS,
            "userdata-anchored (settings backup): all %d members extract under userdata/"
            % len(names),
        )
    strays = sorted(
        {
            _norm(n)
            for n in names
            if _first_seg(n) not in HOME_ALLOWED_TOP and _norm(n) != MANIFEST_NAME
        }
    )
    if strays:
        shown = ", ".join(strays[:5]) + (" ..." if len(strays) > 5 else "")
        return _result(
            "anchor",
            FAIL,
            "home-anchored but %d member(s) have unknown roots and would be "
            "silently skipped on restore: %s" % (len(strays), shown),
            details={"strays": strays},
        )
    return _result(
        "anchor",
        PASS,
        "home-anchored (full backup): all %d members classify cleanly" % len(names),
    )


def check_manifest(zf, names):
    if MANIFEST_NAME not in names:
        return _result(
            "manifest",
            WARN,
            "%s missing (older backups predate the manifest)" % MANIFEST_NAME,
        )
    try:
        data = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest is not a JSON object")
    except Exception as e:
        return _result("manifest", FAIL, "%s unparseable: %s" % (MANIFEST_NAME, e))
    failed = data.get("failed") or []
    if failed:
        shown = ", ".join(str(f) for f in failed[:3]) + (
            " ..." if len(failed) > 3 else ""
        )
        return _result(
            "manifest",
            FAIL,
            "manifest lists %d capture failure(s): %s" % (len(failed), shown),
            details={"failed": failed},
        )
    entries = data.get("entries")
    if isinstance(entries, list):
        entries = len(entries)
    return _result(
        "manifest",
        PASS,
        "manifest ok: created=%s source_os=%s entries=%s, no capture failures"
        % (data.get("created"), data.get("source_os"), entries),
        details={"created": data.get("created"), "source_os": data.get("source_os")},
    )


def check_guisettings(names, ud):
    if (ud + "guisettings.xml") in names:
        return _result("guisettings", PASS, "%sguisettings.xml present" % ud)
    return _result(
        "guisettings",
        FAIL,
        "%sguisettings.xml missing: core settings were not captured" % ud,
    )


def check_sources(names, ud):
    # Present-or-absent is informational either way: sources.xml only exists
    # once the box has file-manager sources.
    if (ud + "sources.xml") in names:
        return _result("sources", PASS, "%ssources.xml present" % ud)
    return _result(
        "sources", PASS, "%ssources.xml absent (box may define no sources)" % ud
    )


def check_database(names, ud, anchor):
    if any(_norm(n).startswith(ud + "Database/") for n in names):
        return _result("database", PASS, "%sDatabase/ present" % ud)
    if anchor == "home":
        return _result(
            "database",
            FAIL,
            "full backup without %sDatabase/: library and addon DBs missing" % ud,
        )
    return _result(
        "database",
        WARN,
        "settings backup without Database/ (may be intentional for this backup type)",
    )


def check_iptv(names, ud, anchor):
    prefix = ud + IPTV_SUBTREE
    members = sorted(
        _norm(n) for n in names if _norm(n).startswith(prefix) and not n.endswith("/")
    )
    if members:
        instances = sorted(
            m.rsplit("/", 1)[-1]
            for m in members
            if re.match(r"instance-settings-\d+\.xml$", m.rsplit("/", 1)[-1])
        )
        return _result(
            "iptv",
            PASS,
            "%s present (%d file(s)); instance settings: %s"
            % (prefix, len(members), ", ".join(instances) if instances else "none"),
            details={"instances": instances, "files": members},
        )
    if anchor == "home":
        return _result(
            "iptv",
            FAIL,
            "full backup has NO %s entries: IPTV config was not captured "
            "(the historical backup gap)" % prefix,
        )
    return _result(
        "iptv",
        WARN,
        "settings backup has no %s entries: IPTV config not captured" % prefix,
    )


def _iptv_setting_values(xml_bytes):
    """Parse a pvr.iptvsimple instance-settings XML, return {id: value} for the
    path-bearing fields that are non-empty."""
    root = ET.parse(io.BytesIO(xml_bytes)).getroot()
    out = {}
    for el in root.iter("setting"):
        sid = el.get("id")
        if sid in IPTV_PATH_FIELDS:
            val = (el.text or "").strip()
            if val:
                out[sid] = val
    return out


def check_portability(zf, names, ud):
    prefix = ud + IPTV_SUBTREE
    inst_files = sorted(
        _norm(n)
        for n in names
        if _norm(n).startswith(prefix)
        and re.match(r"instance-settings-\d+\.xml$", _norm(n).rsplit("/", 1)[-1])
    )
    if not inst_files:
        return _result("portability", PASS, "no IPTV instance-settings files to check")
    problems, notes, verdicts = [], [], []
    for fname in inst_files:
        short = fname.rsplit("/", 1)[-1]
        try:
            values = _iptv_setting_values(zf.read(fname))
        except Exception as e:
            verdicts.append(WARN)
            problems.append("%s unparseable (%s)" % (short, e))
            continue
        for sid, val in sorted(values.items()):
            if val.startswith(PORTABLE_SCHEMES):
                verdicts.append(PASS)
                notes.append("%s %s portable (%s)" % (short, sid, val))
            elif val.startswith("special://"):
                verdicts.append(PASS)
                notes.append("%s %s portable special:// path (%s)" % (short, sid, val))
            elif val.startswith("/"):
                verdicts.append(FAIL)
                problems.append(
                    "%s %s is a device-absolute path that will not exist on "
                    "another box: %s" % (short, sid, val)
                )
            else:
                verdicts.append(WARN)
                problems.append(
                    "%s %s has an unrecognized location form: %s" % (short, sid, val)
                )
    verdict = _worst(verdicts)
    if verdict == PASS:
        reason = "all IPTV path/url settings are portable (%d checked)" % len(notes)
    else:
        reason = "; ".join(problems)
    return _result(
        "portability", verdict, reason, details={"ok": notes, "problems": problems}
    )


def check_secret_hygiene(names, ud):
    hits = sorted(
        _norm(n)
        for n in names
        if _norm(n) == ud + SECRET_TAIL or _norm(n).endswith("/" + SECRET_TAIL)
    )
    if hits:
        return _result(
            "secrets",
            FAIL,
            "archive embeds EZM's own settings.xml (carries the Dropbox token): %s"
            % ", ".join(hits),
            details={"members": hits},
        )
    return _result("secrets", PASS, "no EZM settings.xml (Dropbox token) in archive")


def check_binary_addons(zf, names):
    addon_files = {}
    for n in names:
        norm = _norm(n)
        if norm.startswith("addons/") and norm.count("/") >= 2:
            addon_id = norm.split("/", 2)[1]
            addon_files.setdefault(addon_id, []).append(norm)
    if not addon_files:
        return _result(
            "binary-addons", PASS, "no addons/ tree in archive (nothing to check)"
        )
    binary_ids = {}
    for addon_id, files in sorted(addon_files.items()):
        markers = []
        if any(f.lower().endswith(NATIVE_LIB_SUFFIXES) for f in files):
            markers.append("native library member")
        axml = "addons/%s/addon.xml" % addon_id
        if axml in files:
            try:
                text = zf.read(axml).decode("utf-8", "replace")
                if BINARY_ADDON_XML_RE.search(text):
                    markers.append("platform-specific addon.xml")
            except Exception:
                pass
        if markers:
            binary_ids[addon_id] = markers
    if binary_ids:
        listed = ", ".join(
            "%s (%s)" % (i, "; ".join(m)) for i, m in sorted(binary_ids.items())
        )
        return _result(
            "binary-addons",
            WARN,
            "platform-binary add-on(s) will not run after a cross-OS restore: %s"
            % listed,
            details={"binary_addons": sorted(binary_ids)},
        )
    return _result(
        "binary-addons",
        PASS,
        "no platform-binary add-ons detected (%d add-on dir(s) checked)"
        % len(addon_files),
    )


def lint_archive(path):
    """Run every check against the backup zip at `path`.
    Returns (results, ok) where ok is True iff no check FAILed."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        results = [_result("archive", FAIL, "cannot open as a zip: %s" % e)]
        return results, False
    with zf:
        names = [_norm(n) for n in zf.namelist()]
        file_names = [n for n in names if not n.endswith("/")]
        anchor = archive_anchor(file_names)
        # ud = the archive-internal prefix where userdata content lives.
        ud = "userdata/" if anchor == "home" else ""
        results = [
            check_anchor(file_names, anchor),
            check_manifest(zf, set(file_names)),
            check_guisettings(set(file_names), ud),
            check_sources(set(file_names), ud),
            check_database(file_names, ud, anchor),
            check_iptv(file_names, ud, anchor),
            check_portability(zf, file_names, ud),
            check_secret_hygiene(file_names, ud),
            check_binary_addons(zf, file_names),
        ]
    ok = all(r["verdict"] != FAIL for r in results)
    return results, ok


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Lint an EZ Maintenance++ backup zip for completeness, "
        "cross-OS portability, and secret hygiene (host-side, never on-device)."
    )
    parser.add_argument("archive", help="path to the backup .zip")
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON results"
    )
    args = parser.parse_args(argv)

    results, ok = lint_archive(args.archive)
    if args.json:
        print(
            json.dumps({"archive": args.archive, "ok": ok, "checks": results}, indent=2)
        )
    else:
        for r in results:
            print("%-4s %-14s %s" % (r["verdict"], r["check"], r["reason"]))
        fails = sum(1 for r in results if r["verdict"] == FAIL)
        warns = sum(1 for r in results if r["verdict"] == WARN)
        print(
            "RESULT: %s (%d FAIL, %d WARN, %d checks)"
            % ("PASS" if ok else "FAIL", fails, warns, len(results))
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
