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


def test_generator_stamps_the_fingerprint_it_compared_against_the_box(monkeypatch):
    """The entry must carry the fingerprint pull() COMPARED against the box.

    Two failures this pins at once. A top-level-only stamp is what let one class
    launder another (G-1). And re-hashing the tree at WRITE time rather than reusing
    the compared value reopens the edit-after-evidence window: a contract file edited
    between the pull and the write would be recorded as verified without any box
    having been checked against it - the exact shape of the 2026-07-19 incident.

    Driven through the real pull() rather than asserted against source text, so
    moving the assignment cannot silently satisfy it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_vd_stamp_test", ROOT / "tools" / "verify_device.py"
    )
    vd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vd)

    monkeypatch.setenv("KODI_JSONRPC_USER", "u")
    monkeypatch.setenv("KODI_JSONRPC_PASSWORD", "p")
    monkeypatch.setattr(vd, "storage_fingerprint", lambda: "F" * 64)

    def _rpc(host, method, params=None, **kw):
        if method == "XBMC.GetInfoLabels":
            return {
                "System.BuildVersion": "21.3",
                "System.FriendlyName": "bench",
                "Window(10000).Property(ezm_contract_fingerprint)": "F" * 64,
            }
        if method == "Addons.GetAddonDetails":
            return {"addon": {"version": vd.addon_version()}}
        return {}

    monkeypatch.setattr(vd, "rpc", _rpc)
    evidence = vd.pull("192.0.2.1", "tvos")
    assert evidence.get("storage_fingerprint") == "F" * 64, (
        "the device entry must carry the fingerprint that was compared against the "
        "box, got %r" % (evidence.get("storage_fingerprint"),)
    )


# --------------------------------------------------------------------------- #
# The gate's ASSERTION BODY, driven directly.
#
# Found 2026-07-19: mutation-testing this suite normally deselects the gate file,
# because any edit to a CONTRACT_FILE trips the fingerprint check and a failure
# there says nothing about behaviour. That deselection made the gate's own
# assertions invisible: making the per-entry fingerprint check vacuous, or the
# addon_version_on_box check vacuous, left the entire suite GREEN. The assertion
# that closes the laundering defect was itself unprotected - the same shape as
# the defect it fixes, a gate that can be walked past.
#
# These drive assert_artifact_covers_head against synthetic artifacts, so the
# protection no longer depends on the real repository's state.
# --------------------------------------------------------------------------- #


def _entry(fp, version="9999.01.01.0", **over):
    e = {
        "class": "tvos",
        "storage_fingerprint": fp,
        "addon_version_on_box": version,
        "skinshortcuts_duplicates": [],
    }
    e.update(over)
    return e


def _doc(tvos, android, version="9999.01.01.0"):
    return {"version": version, "devices": {"tvos": tvos, "android": android}}


def test_assertion_body_accepts_a_fully_current_artifact(gate):
    """Baseline: if this fails, every negative case below proves nothing."""
    gate.assert_artifact_covers_head(
        _doc(_entry("FP_HEAD"), _entry("FP_HEAD")), "9999.01.01.0", "FP_HEAD"
    )


def test_assertion_body_rejects_a_stale_per_class_fingerprint(gate):
    """THE laundering defect. One class captured against older code must fail.

    Mutating this assertion to `assert True or ...` previously left the whole
    suite green."""
    with pytest.raises(AssertionError) as e:
        gate.assert_artifact_covers_head(
            _doc(_entry("FP_HEAD"), _entry("FP_OLD")), "9999.01.01.0", "FP_HEAD"
        )
    assert "certifies DIFFERENT storage code" in str(e.value)


def test_assertion_body_rejects_a_wrong_version_on_box(gate):
    """A box running a different build cannot certify this one."""
    with pytest.raises(AssertionError) as e:
        gate.assert_artifact_covers_head(
            _doc(_entry("FP_HEAD"), _entry("FP_HEAD", addon_version_on_box="1111.01.01.0")),
            "9999.01.01.0",
            "FP_HEAD",
        )
    assert "captured on a box running" in str(e.value)


def test_assertion_body_rejects_a_missing_class(gate):
    """Both classes required: the fix must work where the bug is AND be a no-op elsewhere."""
    with pytest.raises(AssertionError) as e:
        gate.assert_artifact_covers_head(
            {"version": "9999.01.01.0", "devices": {"tvos": _entry("FP_HEAD")}},
            "9999.01.01.0",
            "FP_HEAD",
        )
    assert "has no 'android' entry" in str(e.value)


def test_assertion_body_rejects_a_hand_written_entry(gate):
    """No skinshortcuts listing means it did not come from a real device pull."""
    bad = _entry("FP_HEAD")
    del bad["skinshortcuts_duplicates"]
    with pytest.raises(AssertionError) as e:
        gate.assert_artifact_covers_head(
            _doc(_entry("FP_HEAD"), bad), "9999.01.01.0", "FP_HEAD"
        )
    assert "Do not hand-write these" in str(e.value)


def test_a_waiver_bypasses_only_its_own_class(gate):
    """A recorded waiver is a deliberate bypass for THAT class and no other."""
    gate.assert_artifact_covers_head(
        _doc({"class": "tvos", "waived": "hardware unreachable"}, _entry("FP_HEAD")),
        "9999.01.01.0",
        "FP_HEAD",
    )
    with pytest.raises(AssertionError):
        gate.assert_artifact_covers_head(
            _doc({"class": "tvos", "waived": "hardware unreachable"}, _entry("FP_OLD")),
            "9999.01.01.0",
            "FP_HEAD",
        )
