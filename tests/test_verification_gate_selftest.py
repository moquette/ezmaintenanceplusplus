"""Self-tests for the hardware-verification gate itself.

The gate (`test_storage_change_requires_device_verification.py`) SKIPS whenever the
storage contract is unchanged, which is the normal state of the tree. That means its
entire assertion body is dead code in almost every CI run, and an adversarial QA pass
on 2026-07-18 demonstrated the consequence: the completeness check, the version check
and the generator's fingerprint stamping could each be deleted with the full suite
still green.

These tests drive the gate's logic against SYNTHETIC artifacts in tmp_path, so they
assert regardless of the real repository's state. They exist to make the gate's own
guarantees mutation-detectable.

The laundering defect they pin (QA P0-1, reproduced end to end): `verify_device.py`
refreshes ONE class but rewrites the WHOLE artifact. With a single top-level
fingerprint, pulling android, changing wiz.py, then pulling tvos yields a document
that reads complete-and-current while the android box never ran the new code - and
because a verified fingerprint is remembered forever, the laundering is permanent.
"""

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = ROOT / "tests" / "test_storage_change_requires_device_verification.py"


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """The gate module, with VERIFY_DIR redirected at an empty tmp_path."""
    spec = importlib.util.spec_from_file_location("_gate_under_test", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "VERIFY_DIR", tmp_path)
    return mod


def _artifact(fingerprints, version="9999.01.01.0", top=None):
    """A synthetic artifact: {class: fingerprint} -> the document verify_device writes."""
    devices = {}
    for cls, fp in fingerprints.items():
        if fp is None:
            devices[cls] = {"waived": "hotfix", "class": cls}
            continue
        devices[cls] = {
            "class": cls,
            "storage_fingerprint": fp,
            "addon_version_on_box": version,
            "skinshortcuts_duplicates": [],
        }
    return {
        "version": version,
        "storage_fingerprint": top
        if top is not None
        else next(iter(fingerprints.values()), ""),
        "devices": devices,
    }


def _write(gate, doc):
    (gate.VERIFY_DIR / ("%s.json" % doc["version"])).write_text(json.dumps(doc))


def test_matching_two_class_artifact_satisfies_the_gate(gate, monkeypatch):
    """Baseline: both classes verified against HEAD means nothing is owed."""
    monkeypatch.setattr(gate, "_fingerprint", lambda: "FP_HEAD")
    _write(gate, _artifact({"tvos": "FP_HEAD", "android": "FP_HEAD"}))
    assert gate._contract_changed_since_last_verification() is False


def test_one_class_fresh_run_cannot_launder_the_other_classes_stale_entry(
    gate, monkeypatch
):
    """QA P0-1. THE defect this gate structure exists to prevent.

    android was captured at FP_OLD; the code then changed to FP_HEAD; a tvos-only
    pull rewrote the document and stamped the top level FP_HEAD. If the gate trusts
    that top-level value, it certifies an android box that never ran FP_HEAD - and
    remembers that verdict forever."""
    monkeypatch.setattr(gate, "_fingerprint", lambda: "FP_HEAD")
    _write(
        gate,
        _artifact({"tvos": "FP_HEAD", "android": "FP_OLD"}, top="FP_HEAD"),
    )
    assert gate._contract_changed_since_last_verification() is True, (
        "a mixed-fingerprint artifact must NOT count as verified: the stale class's "
        "box never ran the code the top-level fingerprint claims"
    )


def test_incomplete_artifact_does_not_mint_a_verified_fingerprint(gate, monkeypatch):
    """A single-class pull must leave the gate still demanding the other class."""
    monkeypatch.setattr(gate, "_fingerprint", lambda: "FP_HEAD")
    _write(gate, _artifact({"tvos": "FP_HEAD"}))
    assert gate._contract_changed_since_last_verification() is True


def test_entry_without_a_fingerprint_does_not_count_as_verified(gate, monkeypatch):
    """Pre-per-class artifacts carry no per-entry stamp. They cannot certify HEAD.

    Legacy artifacts are not evidence for the new scheme; they must read as unverified
    rather than silently satisfying the gate through a missing key."""
    monkeypatch.setattr(gate, "_fingerprint", lambda: "FP_HEAD")
    doc = _artifact({"tvos": "FP_HEAD", "android": "FP_HEAD"}, top="FP_HEAD")
    for entry in doc["devices"].values():
        entry.pop("storage_fingerprint")
    _write(gate, doc)
    assert gate._contract_changed_since_last_verification() is True


def test_a_waiver_stands_in_for_its_own_class_only(gate, monkeypatch):
    """A recorded waiver is a deliberate bypass for THAT class.

    It must not also excuse the other class from carrying a current fingerprint."""
    monkeypatch.setattr(gate, "_fingerprint", lambda: "FP_HEAD")
    _write(gate, _artifact({"tvos": None, "android": "FP_HEAD"}, top="FP_HEAD"))
    assert gate._contract_changed_since_last_verification() is False
    _write(gate, _artifact({"tvos": None, "android": "FP_OLD"}, top="FP_HEAD"))
    assert gate._contract_changed_since_last_verification() is True, (
        "a waiver on one class must not launder a stale entry on the other"
    )


def test_unrelated_versions_artifact_can_still_satisfy_the_gate(gate, monkeypatch):
    """The gate remembers any COMPLETE, self-consistent artifact, not just this version.

    That is deliberate - unchanged storage code owes no new run - but it is exactly why
    the per-class check matters: a laundered artifact would be blessed permanently."""
    monkeypatch.setattr(gate, "_fingerprint", lambda: "FP_HEAD")
    _write(
        gate,
        _artifact({"tvos": "FP_HEAD", "android": "FP_HEAD"}, version="1111.01.01.0"),
    )
    assert gate._contract_changed_since_last_verification() is False


def test_generator_stamps_the_fingerprint_into_each_device_entry(gate):
    """verify_device.py must write a PER-ENTRY fingerprint.

    Without it every artifact reads as unverified (see the test above), so the gate
    would demand a device run forever. This pins the generator's side of the contract."""
    src = (ROOT / "tools" / "verify_device.py").read_text()
    assert 'evidence["storage_fingerprint"] = fingerprint' in src, (
        "verify_device.py must stamp the fingerprint into the device entry it just "
        "captured; a top-level-only stamp is what allowed one class to launder another"
    )
