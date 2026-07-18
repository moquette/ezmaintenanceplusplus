"""HARDWARE GATE: a change to the tvOS storage-contract code may not ship unverified.

WHY THIS EXISTS
---------------
Our own docs have carried the action item "add a hardware-verification gate to the EZM
release checklist" UNCHECKED since 2026-07-08. In the meantime we shipped storage fix after
storage fix "verified in code", several of which were wrong on the device - and on
2026-07-14 an incident doc claimed "Hardware-confirmed" for a build that had never run on a
box. A checklist line a human ticks is exactly the thing that failed. This is the mechanical
version.

THE RULE
--------
The storage-contract source (nsud.py, boxsetup.py) is fingerprinted. When it changes, the
last device run no longer covers the code, so shipping requires a FRESH verification
artifact (verification/<version>.json) that:

  1. is for the CURRENT addon version (addon.xml at HEAD), and
  2. carries the CURRENT storage fingerprint (so it certifies THIS code, not older code), and
  3. has an entry for BOTH device classes (tvos AND android - the fix must work on the box
     that has the bug AND the box that must stay a no-op), and
  4. reports each box running the version under review (the generator enforces this at pull
     time; we re-check it here so a hand-edited artifact still fails).

The artifact is produced only by tools/verify_device.py, which PULLS the evidence off a
live box over JSON-RPC. It cannot be satisfied by typing prose. See that file's header.

ESCAPE HATCH (loud, recorded, never silent)
-------------------------------------------
A genuine hotfix that cannot wait for a device run may ship by committing an artifact with
{"waived": "<reason>"} for a class. That is a deliberate, reviewable act in git history -
the opposite of a checklist quietly left unticked.

WHEN YOU WRITE A WAIVER: name the box in words ("atv2", "the office Fire TV"), never its
IP. This repo is PUBLIC and these artifacts are committed, so an address typed into a
waiver rationale is a published address. Nothing generates this prose - a human writes it,
so nothing but this note and the guard stands between a typed IP and publication. The
guard is test_committed_verification_artifacts_carry_no_device_address in
test_verify_device_checks.py; it scans the WHOLE artifact including waiver prose, and it
is deliberately not narrowed to machine fields for exactly this reason. Three IPs were
redacted out of waiver prose on 2026-07-18; the box names were already in the same
sentences, so nothing evidentiary was lost.

FINGERPRINT SCOPE (widened 2026-07-16; original GAP 3 review 2026-07-14)
------------------------------------------------------------------------
The 2026-07-14 review scoped CONTRACT_FILES to (nsud.py, boxsetup.py) because the
other xbmcvfs importers "only stage/verify/delete backup ZIPs ... no
NSUserDefaults-shadow risk". That justification is FALSE as of 2026-07-16 and its
tracked follow-up condition has triggered: onetap.py now deletes NSUserDefaults keys
(_wipe_nsud_keys, the two-layer wipe), nsud.py hosts the two-layer IPTV instance
sweep wiz.restore() delegates to, and nsub.py's plist capture is the ONLY source of
NSUD-resident settings in a tvOS backup (its silent omission WAS the 2026-07-08
incident). All three are storage-contract mutations of exactly the class this gate
exists for, and the AST write-lint cannot catch them (it lints raw WRITES, not
deletes/wipes/captures). CONTRACT_FILES therefore includes nsud, boxsetup, nsub,
onetap - and wiz.py, whose restore orchestration decides when each of those runs.
"""

import hashlib
import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDON_XML = ROOT / "script.ezmaintenanceplusplus/addon.xml"
NSUD = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/nsud.py"
BOXSETUP = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/boxsetup.py"
NSUB = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/nsub.py"
ONETAP = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/onetap.py"
WIZ = ROOT / "script.ezmaintenanceplusplus/resources/lib/modules/wiz.py"
VERIFY_DIR = ROOT / "verification"

# MUST match verify_device.CONTRACT_FILES.
CONTRACT_FILES = (NSUD, BOXSETUP, NSUB, ONETAP, WIZ)
REQUIRED_CLASSES = ("tvos", "android")


def _fingerprint():
    h = hashlib.sha256()
    for f in sorted(CONTRACT_FILES):
        h.update(f.read_bytes())
    return h.hexdigest()


def _addon_version():
    txt = ADDON_XML.read_text()
    m = re.search(r'id="script\.ezmaintenanceplusplus"[^>]*version="([^"]+)"', txt)
    return m.group(1)


def _artifact_path():
    return VERIFY_DIR / ("%s.json" % _addon_version())


def _contract_changed_since_last_verification():
    """True if the storage code differs from every fingerprint we have ever verified.

    Only COMPLETE artifacts count: one carrying an entry (or an explicit waiver)
    for EVERY required device class. Without this, a single-class pull would mint
    a 'verified' fingerprint and the gate would skip with the other class never
    run - an incomplete verification must leave the gate demanding the rest."""
    verified = set()
    if VERIFY_DIR.is_dir():
        for p in VERIFY_DIR.glob("*.json"):
            try:
                doc = json.loads(p.read_text())
            except (ValueError, OSError):
                continue
            devices = doc.get("devices", {})
            if all(k in devices for k in REQUIRED_CLASSES):
                verified.add(doc.get("storage_fingerprint"))
    return _fingerprint() not in verified


def test_storage_contract_change_has_a_device_run():
    """If nsud/boxsetup changed, a fresh two-class device artifact must exist for this version.

    Skipped only when the storage code is byte-identical to something already verified -
    i.e. this commit did not touch the contract, so no new device run is owed.
    """
    if not _contract_changed_since_last_verification():
        pytest.skip("storage contract unchanged since a prior verified run")

    path = _artifact_path()
    version = _addon_version()
    assert path.exists(), (
        "The storage-contract code (nsud.py / boxsetup.py) changed, so it needs a fresh "
        "device run before it can ship, and there is no artifact for the current version.\n"
        "  Deploy this build to a real Fire TV AND a real Apple TV, then run:\n"
        "    python3 tools/verify_device.py --host <firetv-ip>  --class android\n"
        "    python3 tools/verify_device.py --host <appletv-ip> --class tvos\n"
        "  (tvOS has no adb; the tool pulls evidence over Kodi JSON-RPC.)\n"
        '  A genuine hotfix may ship with {"waived": "<reason>"} per class - loud and '
        "recorded, never a silently unticked box.\n"
        "  Expected artifact: verification/%s.json" % version
    )

    doc = json.loads(path.read_text())
    assert doc.get("storage_fingerprint") == _fingerprint(), (
        "verification/%s.json exists but certifies DIFFERENT storage code than is committed "
        "here (its fingerprint does not match nsud.py+boxsetup.py at HEAD). A stale artifact "
        "cannot cover new code - re-run verify_device.py on a box carrying THIS build."
        % version
    )

    devices = doc.get("devices", {})
    for cls in REQUIRED_CLASSES:
        assert cls in devices, (
            "verification/%s.json has no '%s' entry. A storage change must be proven on BOTH "
            "a Fire TV (android) and an Apple TV (tvos) - the fix must work where the bug is "
            "AND stay a no-op where it isn't." % (version, cls)
        )
        entry = devices[cls]
        if entry.get("waived"):
            continue  # deliberate, recorded bypass
        assert entry.get("addon_version_on_box") == version, (
            "The '%s' verification for %s was captured on a box running %s, not %s. Verify on "
            "a box that actually has this build installed."
            % (cls, version, entry.get("addon_version_on_box"), version)
        )
        assert "skinshortcuts_duplicates" in entry, (
            "The '%s' artifact is missing the live skinshortcuts listing - it was not produced "
            "by verify_device.py's device pull. Do not hand-write these." % cls
        )


def _load_generator():
    """Import tools/verify_device.py directly (it is import-safe: no side effects, no
    device contact at module scope)."""
    spec = importlib.util.spec_from_file_location(
        "_gate_verify_device", ROOT / "tools/verify_device.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fingerprint_helper_matches_the_generator():
    """The gate and the generator must fingerprint the SAME files, or the gate is blind.

    verify_device.py computes the fingerprint the artifacts are stamped with; if these two
    lists ever drift, an artifact could 'match' while covering different code.

    This used to assert `f.name in verify_device_source_text` for each file, which was
    weak in three ways and blind in one that matters:
      1. A filename mentioned only in a COMMENT or docstring satisfied a substring test,
         and this tool's own docstring names every one of these modules.
      2. It compared basenames, not resolved paths, so a same-named module in another
         directory would collide and a path change would go unnoticed.
      3. Worst: it was ONE-DIRECTIONAL (gate subset of generator). Adding a file to
         verify_device.CONTRACT_FILES that this gate lacked passed GREEN - the loop
         never inspected it. The generator would then stamp artifacts with a fingerprint
         covering N+1 files while the gate validated N, so a storage-contract change to
         that extra file would never demand a device run. The gate goes blind in exactly
         the scenario it exists to prevent, and this list DOES get edited under pressure
         (it was widened from 2 files to 5 on 2026-07-16).

    So compare the artifacts themselves, bidirectionally, plus the computed fingerprint.
    The two assertions fail for different reasons and both are worth having: set equality
    catches list drift, fingerprint equality additionally catches divergence in the
    HASHING itself (sort order, bytes-vs-text read, an added salt) that identical file
    lists would still hide.
    """
    gen = _load_generator()

    ours = {p.resolve() for p in CONTRACT_FILES}
    theirs = {pathlib.Path(p).resolve() for p in gen.CONTRACT_FILES}
    assert ours == theirs, (
        "this gate and verify_device.py disagree about which files the storage "
        "fingerprint covers.\n  only in the gate:      %s\n  only in the generator: %s\n"
        "Keep the two lists in lockstep, or an artifact can 'match' while covering "
        "different code."
        % (
            sorted(p.name for p in ours - theirs) or "(none)",
            sorted(p.name for p in theirs - ours) or "(none)",
        )
    )

    assert gen.storage_fingerprint() == _fingerprint(), (
        "the file lists agree but the computed fingerprints differ, so the two sides "
        "hash the same files differently (sort order, read mode, or salt). An artifact "
        "stamped by the generator can never match this gate."
    )
