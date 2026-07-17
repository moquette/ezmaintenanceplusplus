"""Pure-logic tests for tools/verify_device.py's restore-contract checks.

Everything here runs against CANNED JSON-RPC responses fed through a fake
`call(method, params)` - no network, no device, no Kodi. The transport (rpc(),
pull(), main()) is deliberately untested here: the whole point of the split in
verify_device.py is that parsing, duplicate detection, and diffing are plain
functions a test can drive without a box answering.
"""

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_device as vd  # noqa: E402


# --------------------------------------------------------------------------- #
# Canned-response fake transport
# --------------------------------------------------------------------------- #
def make_call(directories=None, file_details=None, dir_errors=None, detail_errors=None):
    """A fake JSON-RPC `call(method, params)`.

    directories:   {directory: [file dicts]} for Files.GetDirectory
    file_details:  {file: {"size": N}} for Files.GetFileDetails
    dir_errors:    {directory: "message"} -> raises RuntimeError for that directory
    detail_errors: {file: "message"} -> raises RuntimeError for that file
    """
    directories = directories or {}
    file_details = file_details or {}
    dir_errors = dir_errors or {}
    detail_errors = detail_errors or {}
    calls = []

    def call(method, params=None):
        params = params or {}
        calls.append((method, params))
        if method == "Files.GetDirectory":
            d = params["directory"]
            if d in dir_errors:
                raise RuntimeError(dir_errors[d])
            return {"files": directories.get(d, [])}
        if method == "Files.GetFileDetails":
            f = params["file"]
            if f in detail_errors:
                raise RuntimeError(detail_errors[f])
            if f not in file_details:
                raise RuntimeError("File not found: %s" % f)
            return {"filedetails": file_details[f]}
        raise AssertionError("unexpected JSON-RPC method: %s" % method)

    call.calls = calls
    return call


def entry(label, size=None):
    e = {"label": label, "file": "special://profile/" + label, "filetype": "file"}
    if size is not None:
        e["size"] = size
    return e


HEALTHY_DIRS = {
    vd.IPTV_DIR: [
        entry("instance-settings-1.xml", 2048),
        entry("instance-settings-2.xml", 512),
        entry("settings.xml", 300),
    ],
    vd.PROFILE_DIR: [
        entry("addon_data"),
        entry("Database"),
        entry("guisettings.xml", 41000),
        entry("sources.xml", 900),
    ],
    vd.ADDON_DATA_DIR: [
        entry("pvr.iptvsimple"),
        entry("script.ezmaintenanceplusplus"),
        entry("script.skinshortcuts"),
    ],
    vd.SKINSHORTCUTS_DIR: [
        entry("mainmenu.DATA.xml"),
        entry("overrides.xml"),
    ],
}

HEALTHY_DETAILS = {
    vd.IPTV_DIR + "instance-settings-1.xml": {"size": 2048},
    vd.IPTV_DIR + "instance-settings-2.xml": {"size": 512},
}


def healthy_contract():
    return vd.collect_restore_contract(make_call(HEALTHY_DIRS, HEALTHY_DETAILS))


# --------------------------------------------------------------------------- #
# find_duplicates
# --------------------------------------------------------------------------- #
def test_find_duplicates_reports_only_double_listed_names_sorted():
    names = ["b.xml", "a.xml", "b.xml", "c.xml", "a.xml", "a.xml"]
    assert vd.find_duplicates(names) == ["a.xml", "b.xml"]


def test_find_duplicates_empty_and_unique_are_clean():
    assert vd.find_duplicates([]) == []
    assert vd.find_duplicates(["x", "y"]) == []


# --------------------------------------------------------------------------- #
# iptv_config
# --------------------------------------------------------------------------- #
def test_iptv_config_records_instance_settings_names_and_sizes():
    contract = healthy_contract()
    iptv = contract["iptv_config"]
    assert iptv["directory"] == vd.IPTV_DIR
    assert iptv["entries"] == 3  # settings.xml counted in entries, not instances
    assert iptv["instance_settings"] == [
        {"name": "instance-settings-1.xml", "size": 2048},
        {"name": "instance-settings-2.xml", "size": 512},
    ]
    assert iptv["empty"] is False


def test_iptv_config_empty_directory_is_a_recorded_finding_not_a_crash():
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = []
    contract = vd.collect_restore_contract(make_call(dirs, HEALTHY_DETAILS))
    iptv = contract["iptv_config"]
    assert iptv["empty"] is True
    assert iptv["entries"] == 0
    assert iptv["instance_settings"] == []
    assert "error" not in iptv


def test_iptv_config_directory_error_is_recorded_and_run_continues():
    contract = vd.collect_restore_contract(
        make_call(
            HEALTHY_DIRS,
            HEALTHY_DETAILS,
            dir_errors={vd.IPTV_DIR: "Files.GetDirectory -> invalid params"},
        )
    )
    assert "invalid params" in contract["iptv_config"]["error"]
    # the failing directory must not abort the other checks
    assert contract["profile_inventory"]["addon_data_count"] == 3
    # the shadow probe reads the SAME failing IPTV dir: it records the error too
    assert "error" in contract["shadow_probe"]


# --------------------------------------------------------------------------- #
# profile_inventory
# --------------------------------------------------------------------------- #
def test_profile_inventory_counts_and_names():
    # The profile ROOT is not remotely listable on live Kodi 21 (Invalid params,
    # live-verified 2026-07-16), so the fingerprint is addon_data-scoped and the
    # artifact says so explicitly.
    inv = healthy_contract()["profile_inventory"]
    assert "unreachable" in inv["profile_root"]
    assert inv["addon_data_count"] == 3
    assert inv["addon_data_entries"] == [
        "pvr.iptvsimple",
        "script.ezmaintenanceplusplus",
        "script.skinshortcuts",
    ]


# --------------------------------------------------------------------------- #
# duplicate_listing
# --------------------------------------------------------------------------- #
def test_duplicate_listing_clean_on_healthy_box():
    dup = healthy_contract()["duplicate_listing"]
    assert dup["clean"] is True
    assert set(dup["duplicates"]) == set(vd.DUPLICATE_SCAN_DIRS)
    assert all(v == [] for v in dup["duplicates"].values())
    assert "errors" not in dup


def test_duplicate_listing_flags_dual_layer_split_in_iptv_and_addon_data():
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = HEALTHY_DIRS[vd.IPTV_DIR] + [
        entry("instance-settings-1.xml", 0)  # same name listed twice = key/disk split
    ]
    dirs[vd.ADDON_DATA_DIR] = HEALTHY_DIRS[vd.ADDON_DATA_DIR] + [
        entry("pvr.iptvsimple")
    ]
    dup = vd.collect_restore_contract(make_call(dirs, HEALTHY_DETAILS))[
        "duplicate_listing"
    ]
    assert dup["clean"] is False
    assert dup["duplicates"][vd.IPTV_DIR] == ["instance-settings-1.xml"]
    assert dup["duplicates"][vd.ADDON_DATA_DIR] == ["pvr.iptvsimple"]
    assert dup["duplicates"][vd.SKINSHORTCUTS_DIR] == []


def test_duplicate_listing_records_per_directory_errors():
    dup = vd.collect_restore_contract(
        make_call(
            HEALTHY_DIRS,
            HEALTHY_DETAILS,
            dir_errors={vd.SKINSHORTCUTS_DIR: "boom"},
        )
    )["duplicate_listing"]
    assert "boom" in dup["errors"][vd.SKINSHORTCUTS_DIR]
    # the scannable directories still get scanned
    assert dup["duplicates"][vd.ADDON_DATA_DIR] == []
    assert dup["duplicates"][vd.IPTV_DIR] == []


# --------------------------------------------------------------------------- #
# shadow_probe
# --------------------------------------------------------------------------- #
def test_shadow_probe_healthy_files_record_both_vantage_points():
    probe = healthy_contract()["shadow_probe"]
    assert probe["size_zero_but_exists"] is False
    by_file = {p["file"]: p for p in probe["probed"]}
    one = by_file[vd.IPTV_DIR + "instance-settings-1.xml"]
    assert one["listed_size"] == 2048
    assert one["details_size"] == 2048
    assert one["size_zero_but_exists"] is False
    assert len(probe["probed"]) == 2  # settings.xml is not an instance file


def test_shadow_probe_size_zero_but_exists_is_flagged():
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = [
        entry("instance-settings-1.xml", 0),  # exists, size 0: the husk symptom
        entry("settings.xml", 300),
    ]
    probe = vd.collect_restore_contract(
        make_call(dirs, {vd.IPTV_DIR + "instance-settings-1.xml": {"size": 0}})
    )["shadow_probe"]
    assert probe["size_zero_but_exists"] is True
    assert probe["probed"][0]["size_zero_but_exists"] is True


def test_shadow_probe_details_failure_degrades_to_listing_only():
    probe = vd.collect_restore_contract(
        make_call(
            HEALTHY_DIRS,
            detail_errors={
                vd.IPTV_DIR + "instance-settings-1.xml": "not exposed",
                vd.IPTV_DIR + "instance-settings-2.xml": "not exposed",
            },
        )
    )["shadow_probe"]
    by_file = {p["file"]: p for p in probe["probed"]}
    one = by_file[vd.IPTV_DIR + "instance-settings-1.xml"]
    assert one["listed_size"] == 2048
    assert one["details_size"] is None
    assert probe["size_zero_but_exists"] is False


def test_shadow_probe_no_instance_files_records_empty_probe():
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = [entry("settings.xml", 300)]
    probe = vd.collect_restore_contract(make_call(dirs, {}))["shadow_probe"]
    assert probe["probed"] == []
    assert probe["size_zero_but_exists"] is False


# --------------------------------------------------------------------------- #
# graceful degradation of the whole collection
# --------------------------------------------------------------------------- #
def test_every_check_degrades_when_every_call_fails():
    def dead_call(method, params=None):
        raise RuntimeError("connection refused")

    contract = vd.collect_restore_contract(dead_call)
    assert set(contract) == {
        "iptv_config",
        "profile_inventory",
        "duplicate_listing",
        "shadow_probe",
    }
    assert "connection refused" in contract["iptv_config"]["error"]
    assert "connection refused" in contract["profile_inventory"]["error"]
    assert "connection refused" in contract["shadow_probe"]["error"]
    dup = contract["duplicate_listing"]
    assert dup["clean"] is True  # nothing scanned, nothing double-listed
    for directory in vd.DUPLICATE_SCAN_DIRS:
        assert "connection refused" in dup["errors"][directory]


def test_backward_compat_fields_are_untouched_by_the_new_section():
    """The gate test consumes addon_version_on_box and skinshortcuts_duplicates;
    the new section must be additive only. Guard the shape at the source."""
    src = (ROOT / "tools/verify_device.py").read_text()
    for field in (
        '"addon_version_on_box"',
        '"skinshortcuts_duplicates"',
        '"skinshortcuts_vfs_entries"',
        '"clean_single_layer"',
        '"restore_contract"',
    ):
        assert field in src


# --------------------------------------------------------------------------- #
# diff mode
# --------------------------------------------------------------------------- #
def artifact(contract, cls="tvos"):
    return {
        "version": "2026.07.16.1",
        "storage_fingerprint": "f" * 64,
        "devices": {
            cls: {
                "class": cls,
                "addon_version_on_box": "2026.07.16.1",
                "skinshortcuts_duplicates": [],
                "restore_contract": contract,
            }
        },
    }


def test_diff_identical_contracts_reports_survival():
    doc = artifact(healthy_contract())
    lines = vd.diff_restore_contract(doc, json.loads(json.dumps(doc)))
    assert lines == [
        "== device class: tvos ==",
        "  restore_contract unchanged: the profile survived intact",
    ]


def test_diff_reports_vanished_iptv_instance_and_empty_regression():
    before = artifact(healthy_contract())
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = [entry("settings.xml", 300)]  # instances wiped by the restore
    after = artifact(vd.collect_restore_contract(make_call(dirs, HEALTHY_DETAILS)))
    out = "\n".join(vd.diff_restore_contract(before, after))
    assert "[iptv_config] VANISHED: instance-settings-1.xml" in out
    assert "[iptv_config] VANISHED: instance-settings-2.xml" in out
    assert "REGRESSION: instance settings present before, EMPTY after" in out


def test_diff_reports_appeared_and_size_change():
    before = artifact(healthy_contract())
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = [
        entry("instance-settings-1.xml", 2048),
        entry("instance-settings-2.xml", 0),  # truncated by the restore
        entry("instance-settings-3.xml", 700),  # new
        entry("settings.xml", 300),
    ]
    after = artifact(vd.collect_restore_contract(make_call(dirs, HEALTHY_DETAILS)))
    out = "\n".join(vd.diff_restore_contract(before, after))
    assert "[iptv_config] appeared: instance-settings-3.xml" in out
    assert "[iptv_config] size changed: instance-settings-2.xml 512 -> 0" in out


def test_diff_reports_addon_data_lost_in_restore():
    before = artifact(healthy_contract())
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.ADDON_DATA_DIR] = [entry("script.skinshortcuts")]
    after = artifact(vd.collect_restore_contract(make_call(dirs, HEALTHY_DETAILS)))
    out = "\n".join(vd.diff_restore_contract(before, after))
    assert "[addon_data] VANISHED: pvr.iptvsimple" in out
    assert "[addon_data] VANISHED: script.ezmaintenanceplusplus" in out
    assert "addon_data entries: 3 -> 1" in out


def test_diff_reports_new_dual_layer_split_and_shadow_regression():
    before = artifact(healthy_contract())
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = [
        entry("instance-settings-1.xml", 0),
        entry("instance-settings-1.xml", 0),  # double-listed AND size 0
        entry("settings.xml", 300),
    ]
    after = artifact(
        vd.collect_restore_contract(
            make_call(dirs, {vd.IPTV_DIR + "instance-settings-1.xml": {"size": 0}})
        )
    )
    out = "\n".join(vd.diff_restore_contract(before, after))
    assert (
        "[duplicate_listing] NEW dual-layer split in "
        "special://profile/addon_data/pvr.iptvsimple/: instance-settings-1.xml"
        in out
    )
    assert "size_zero_but_exists" in out
    assert "now exists with size 0" in out


def test_diff_handles_artifacts_without_restore_contract():
    before = artifact(healthy_contract())
    del before["devices"]["tvos"]["restore_contract"]
    after = artifact(healthy_contract())
    out = "\n".join(vd.diff_restore_contract(before, after))
    assert "before artifact has no restore_contract section" in out


def test_diff_handles_error_sections_without_crashing():
    before = artifact(healthy_contract())
    broken = healthy_contract()
    broken["iptv_config"] = {"error": "RuntimeError: connection refused"}
    after = artifact(broken)
    out = "\n".join(vd.diff_restore_contract(before, after))
    assert (
        "[iptv_config] after recorded an error: RuntimeError: connection refused" in out
    )


def test_diff_handles_disjoint_device_classes():
    before = artifact(healthy_contract(), cls="tvos")
    after = artifact(healthy_contract(), cls="android")
    out = vd.diff_restore_contract(before, after)
    assert "== device class: android ==" in out
    assert "  only present in the after artifact" in out
    assert "== device class: tvos ==" in out
    assert "  only present in the before artifact" in out


# --------------------------------------------------------------------------- #
# CLI --diff (subprocess on local JSON files only; still no network, no device)
# --------------------------------------------------------------------------- #
def test_cli_diff_mode_runs_offline(tmp_path):
    before = artifact(healthy_contract())
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.IPTV_DIR] = []
    after = artifact(vd.collect_restore_contract(make_call(dirs, HEALTHY_DETAILS)))
    b = tmp_path / "before.json"
    a = tmp_path / "after.json"
    b.write_text(json.dumps(before))
    a.write_text(json.dumps(after))
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/verify_device.py"),
            "--diff",
            str(b),
            str(a),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "VANISHED: instance-settings-1.xml" in proc.stdout
    assert "REGRESSION" in proc.stdout


def test_cli_requires_host_and_class_without_diff():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools/verify_device.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "--host and --class are required unless --diff is used" in proc.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
