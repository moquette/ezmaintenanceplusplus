"""Pure-logic tests for tools/verify_device.py's restore-contract checks.

Everything here runs against CANNED JSON-RPC responses fed through a fake
`call(method, params)` - no network, no device, no Kodi. The transport (rpc(),
pull(), main()) is deliberately untested here: the whole point of the split in
verify_device.py is that parsing, duplicate detection, and diffing are plain
functions a test can drive without a box answering.
"""

import ast
import json
import os
import pathlib
import re
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
        "special://profile/addon_data/pvr.iptvsimple/: instance-settings-1.xml" in out
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


# --------------------------------------------------------------------------- #
# JSON-RPC configuration.
#
# This tool used to carry a hardcoded `kodi:kodi` Basic-auth header in source, in
# a PUBLIC repository. The credential now comes from the environment with NO
# fallback, and these tests exist to keep it that way: the dangerous regression is
# not "the env var is unread", it is "an unset env var quietly falls back to the
# stock default again", which no functional test would notice.
# --------------------------------------------------------------------------- #
def test_credentials_come_from_the_environment():
    user, password = vd.jsonrpc_credentials(
        {vd.ENV_USER: "someuser", vd.ENV_PASSWORD: "s3cret"}
    )
    assert (user, password) == ("someuser", "s3cret")


def test_credentials_build_the_basic_auth_header():
    # RFC 7617: base64("user:password"). Verified against a known-good encoding
    # rather than by re-deriving it the same way the implementation does.
    assert vd.auth_header("aladdin", "opensesame") == "Basic YWxhZGRpbjpvcGVuc2VzYW1l"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {vd.ENV_USER: "someuser"},
        {vd.ENV_PASSWORD: "s3cret"},
        {vd.ENV_USER: "", vd.ENV_PASSWORD: ""},
        {vd.ENV_USER: "   ", vd.ENV_PASSWORD: "s3cret"},
    ],
)
def test_missing_credentials_fail_loudly_and_never_default(env):
    """A missing or blank credential must be a hard, named failure. Anything that
    quietly proceeded here would be contacting a box with a guessed credential."""
    with pytest.raises(SystemExit) as excinfo:
        vd.jsonrpc_credentials(env)
    message = str(excinfo.value)
    assert vd.ENV_USER in message or vd.ENV_PASSWORD in message
    assert "REFUSING to contact a device" in message


def _tool_source():
    return (ROOT / "tools/verify_device.py").read_text()


def _function_node(name):
    for node in ast.walk(ast.parse(_tool_source())):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s() not found in verify_device.py" % name)


def test_the_exact_old_credential_never_returns():
    """A literal check for the specific credential that WAS published here.

    Deliberately narrow, and narrow is stated: this catches the copy-paste
    regression (someone restoring the old line from git history) and nothing
    more. The general case is covered structurally and behaviourally below -
    an earlier version of this test claimed to catch a default "in any form"
    while only banning three literals, which is the kind of overclaim that
    makes a guard worse than no guard."""
    body = _tool_source().split('"""', 2)[2]  # skip the docstring explaining the fix
    for banned in ("kodi:kodi", 'b"kodi', "'kodi:"):
        assert banned not in body, "the old stock credential is back in the tool"


def test_credential_resolution_holds_no_literal_credential(monkeypatch):
    """The structural guard: no short string literal may live in
    jsonrpc_credentials.

    This is what catches the forms a literal grep cannot - `user = "kodi"`,
    `password = "admin"`, a base64 blob, or any other stock credential.

    The discriminator is stated plainly, because a guard that hides its own
    limits is the problem this replaced: a literal counts as credential-shaped
    if it is non-empty, at most 40 characters, single-line, and space-free.
    Prose in this function (its docstring, the multi-line error message, the
    " and " join separator) is exempt under those rules. A credential
    CONTAINING a space would slip past - the behavioural guarantee in
    test_an_empty_environment_can_produce_no_credential_at_all is what holds
    the line; this check exists to localise a regression to its source."""
    node = _function_node("jsonrpc_credentials")
    literals = [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    suspicious = [
        s
        for s in literals
        if s.strip() and len(s) <= 40 and "\n" not in s and " " not in s
    ]
    assert suspicious == [], (
        "jsonrpc_credentials contains short string literal(s) %r - a credential "
        "default must never live in this source" % (suspicious,)
    )


def test_nothing_outside_credential_resolution_reads_the_credential_env(monkeypatch):
    """One door in. A second reader elsewhere could reintroduce a fallback that
    jsonrpc_credentials' own guarantees would never see.

    Scoped to actual environment READS - `<mapping>.get(ENV_USER)` - rather than
    every mention of the names, so that documenting them (main()'s --help text
    names all four variables) is not mistaken for reading them."""
    readers = set()
    for node in ast.walk(ast.parse(_tool_source())):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                continue
            if sub.func.attr != "get":
                continue
            for arg in sub.args:
                if isinstance(arg, ast.Name) and arg.id in ("ENV_USER", "ENV_PASSWORD"):
                    readers.add(node.name)
    assert readers <= {"jsonrpc_credentials"}, (
        "the credential environment is read outside jsonrpc_credentials, by %s"
        % sorted(readers - {"jsonrpc_credentials"})
    )


def test_an_empty_environment_can_produce_no_credential_at_all(monkeypatch):
    """The behavioural backstop, and the one that actually matters: whatever the
    source looks like, an empty environment must yield NO usable credential from
    any entry point. This is the guarantee; the checks above only make a
    regression easier to localise."""
    for name in (vd.ENV_USER, vd.ENV_PASSWORD, vd.ENV_HOST, vd.ENV_PORT):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit):
        vd.jsonrpc_credentials()

    # ...including through rpc(), which resolves lazily when handed no auth.
    # urlopen is stubbed to EXPLODE rather than connect: if a regression put a
    # default back, this test must fail on the assertion, never by opening a
    # socket to whatever happens to be at that address. (Observed while mutation
    # testing this very guard: with the credential check removed, an unstubbed
    # rpc() sat in a real connect for 8 seconds.)
    def _no_network(*a, **k):
        raise AssertionError("rpc() attempted a connection with no credential set")

    monkeypatch.setattr(vd.urllib.request, "urlopen", _no_network)
    with pytest.raises(SystemExit):
        vd.rpc("203.0.113.1", "XBMC.GetInfoLabels")  # TEST-NET-3, never routable


def test_the_environment_variable_names_are_documented_where_they_are_used():
    body = _tool_source().split('"""', 2)[2]
    for env_name in ("KODI_JSONRPC_USER", "KODI_JSONRPC_PASSWORD"):
        assert env_name in body


def test_no_device_address_is_baked_into_the_source():
    """The usage examples carried real box IPs. The addresses are configuration
    too - the fleet's addressing should not be published by this file."""
    src = (ROOT / "tools/verify_device.py").read_text()
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", src), (
        "a literal device address is back in verify_device.py"
    )


def _pull_with_fake_transport(monkeypatch, host="10.0.0.5", files=None, calls=None):
    """Drive the real pull() against a fake rpc() - no network, no device.

    The credential is supplied through the environment because pull() resolves it
    before its first request; that ordering is asserted directly by
    test_pull_refuses_before_contacting_anything_without_a_credential."""
    monkeypatch.setenv(vd.ENV_USER, "someuser")
    monkeypatch.setenv(vd.ENV_PASSWORD, "s3cret")
    recorded = calls if calls is not None else []

    def fake_rpc(host_, method, params=None, timeout=8, auth=None, port=None):
        recorded.append((method, host_, auth, port))
        if method == "XBMC.GetInfoLabels":
            # The box publishes the hash of ITS OWN installed contract files (G-2).
            # A fake that omits it is correctly REFUSED, so model a box running the
            # code under test by echoing the tool's own fingerprint.
            return {
                "System.BuildVersion": "21.3 (21.3.0) Git:20260101-abcdef",
                "System.FriendlyName": "Living Room",
                "Window(10000).Property(ezm_contract_fingerprint)": (
                    vd.storage_fingerprint()
                ),
            }
        if method == "Addons.GetAddonDetails":
            return {"addon": {"version": vd.addon_version()}}
        if method == "Files.GetDirectory":
            directory = (params or {}).get("directory")
            return {"files": (files or {}).get(directory, [])}
        return {}

    monkeypatch.setattr(vd, "rpc", fake_rpc)
    return vd.pull(host, "tvos")


def test_pull_refuses_before_contacting_anything_without_a_credential(monkeypatch):
    """The ordering guarantee: an unset credential must stop the run BEFORE the
    first request, not surface as a 401 partway through a device pull."""
    monkeypatch.delenv(vd.ENV_USER, raising=False)
    monkeypatch.delenv(vd.ENV_PASSWORD, raising=False)
    calls = []
    monkeypatch.setattr(vd, "rpc", lambda *a, **k: calls.append(a) or {})
    with pytest.raises(SystemExit, match="REFUSING to contact a device"):
        vd.pull("10.0.0.5", "tvos")
    assert calls == [], "a request was made before the credential was checked"


def test_pull_resolves_the_credential_once_for_the_whole_run(monkeypatch):
    """Every call must carry the same prebuilt header rather than re-reading the
    environment per request."""
    calls = []
    _pull_with_fake_transport(monkeypatch, calls=calls)
    assert len(calls) >= 3
    auths = {auth for _method, _host, auth, _port in calls}
    ports = {port for _method, _host, _auth, port in calls}
    assert auths == {vd.auth_header("someuser", "s3cret")}
    assert ports == {vd.DEFAULT_PORT}


def test_the_artifact_writer_records_no_device_address(monkeypatch):
    """Scrubbing the SOURCE was not enough: the tool also WROTE the address into
    every artifact it produced (`"host": host`), and artifacts are committed, so
    a public repo republished the fleet's addressing on every device run. The
    address was the one field in the artifact the box never reported - it was
    echoed back from --host - so it was never evidence to begin with.

    This asserts on pull()'s own output shape, driven by a fake transport, so it
    covers the writer rather than whatever happens to be committed today."""
    evidence = _pull_with_fake_transport(monkeypatch, host="10.0.0.5")

    assert "host" not in evidence, "the artifact writer records the box address again"
    flat = json.dumps(evidence)
    assert "10.0.0.5" not in flat, "the box address leaked into the artifact"
    # The device-REPORTED identity is still there: dropping the address must not
    # cost the artifact its ability to say which box answered.
    assert evidence["friendly_name"] == "Living Room"
    assert evidence["kodi_build"].startswith("21.3")
    # ...and the gate-consumed fields are untouched.
    assert evidence["addon_version_on_box"] == vd.addon_version()
    assert evidence["skinshortcuts_duplicates"] == []
    assert evidence["clean_single_layer"] is True


def test_committed_verification_artifacts_carry_no_device_address():
    """The guard the source-only check was missing. Artifacts are committed to a
    PUBLIC repo; several already carry live box IPs from before the writer was
    fixed. Scrubbing them is safe: `storage_fingerprint` is a hash of the
    CONTRACT_FILES source bytes only, so editing an artifact's host line cannot
    invalidate it, and nothing (gate or --diff) reads the field."""
    offenders = []
    for path in sorted((ROOT / "verification").glob("*.json")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", line):
                offenders.append("%s:%d" % (path.relative_to(ROOT), lineno))
    assert not offenders, (
        "committed verification artifacts publish live device addresses:\n  %s\n"
        "The writer no longer emits them; these are pre-existing entries. Remove "
        'the `"host": ...` lines (they are read by nothing - not the gate, not '
        "--diff) - this does NOT affect storage_fingerprint, which hashes source "
        "files, not artifacts." % "\n  ".join(offenders)
    )


def test_a_missing_label_cannot_manufacture_a_duplicate():
    """A phantom-duplicate guard. Defaulting a missing label to "" made two
    label-less entries collapse to the same name, and a repeated name in this
    tool MEANS a key/disk split - so a malformed listing fabricated a warning
    about the exact damage the tool exists to report truthfully, on the
    gate-consumed clean_single_layer field."""
    files = [{"size": 1}, {"size": 2}, {"label": "real.xml"}]
    names, unlabelled = vd.labelled_names(files)
    assert names == ["real.xml"]
    assert unlabelled == 2
    assert vd.find_duplicates(names) == []


def test_pull_does_not_report_a_phantom_split_for_unlabelled_entries(monkeypatch):
    """The flagged line itself (pull()'s skinshortcuts scan), end to end.

    Added after mutation testing: reverting pull() to the `"" default` while
    leaving labelled_names intact was NOT caught by the unit tests around
    labelled_names or by the restore-contract scan test, because neither drives
    this path. `clean_single_layer` is gate-consumed and `skinshortcuts_duplicates`
    triggers a printed WARNING, so a phantom duplicate here is a fabricated
    finding about the precise damage this tool exists to report truthfully."""
    files = {
        vd.SKINSHORTCUTS_DIR: [
            {"size": 1},  # no label
            {"size": 2},  # no label - these two used to collide on ""
            {"label": "script-skinshortcuts-includes.xml", "size": 3},
        ]
    }
    evidence = _pull_with_fake_transport(monkeypatch, files=files)
    assert evidence["skinshortcuts_duplicates"] == []
    assert evidence["clean_single_layer"] is True
    assert evidence["skinshortcuts_vfs_entries"] == 1
    # Excluded, but recorded - a malformed listing stays visible.
    assert evidence["skinshortcuts_unlabelled_entries"] == 2


def test_pull_still_reports_a_genuine_split(monkeypatch):
    """The counterpart: the real key/disk split must still come through pull()."""
    files = {
        vd.SKINSHORTCUTS_DIR: [
            {"label": "script-skinshortcuts-includes.xml", "size": 1},
            {"label": "script-skinshortcuts-includes.xml", "size": 1},
        ]
    }
    evidence = _pull_with_fake_transport(monkeypatch, files=files)
    assert evidence["skinshortcuts_duplicates"] == ["script-skinshortcuts-includes.xml"]
    assert evidence["clean_single_layer"] is False
    assert "skinshortcuts_unlabelled_entries" not in evidence


def test_a_genuine_duplicate_is_still_detected():
    """The counterpart: excluding unlabelled entries must not blunt the check the
    whole tool exists for."""
    files = [{"label": "dupe.xml"}, {"label": "dupe.xml"}, {"label": "solo.xml"}]
    names, unlabelled = vd.labelled_names(files)
    assert unlabelled == 0
    assert vd.find_duplicates(names) == ["dupe.xml"]


def test_unlabelled_entries_are_counted_not_silently_dropped():
    """Excluding them must not shrink the evidence silently - a malformed listing
    is itself a finding and has to stay visible in the artifact."""
    names, unlabelled = vd.labelled_names([{"label": ""}, {"label": None}, {}])
    assert names == []
    assert unlabelled == 3


def test_duplicate_scan_does_not_report_a_phantom_split_for_missing_labels():
    """The same flaw lived in the restore-contract duplicate scan (it predates
    the skinshortcuts one). End to end through collect_restore_contract."""
    dirs = dict(HEALTHY_DIRS)
    dirs[vd.SKINSHORTCUTS_DIR] = [{"size": 1}, {"size": 2}]  # both label-less
    contract = vd.collect_restore_contract(make_call(dirs, HEALTHY_DETAILS))
    assert contract["duplicate_listing"]["duplicates"][vd.SKINSHORTCUTS_DIR] == []
    assert contract["duplicate_listing"]["clean"] is True


def test_port_defaults_to_kodi_default_and_honours_the_override():
    assert vd.jsonrpc_port({}) == vd.DEFAULT_PORT == 8080
    assert vd.jsonrpc_port({vd.ENV_PORT: "8081"}) == 8081
    with pytest.raises(SystemExit):
        vd.jsonrpc_port({vd.ENV_PORT: "not-a-port"})


def test_diff_mode_needs_no_credential_at_all(tmp_path):
    """--diff reads two local files. It must keep working in an environment with
    no credential set, or the release gate's offline path becomes unusable."""
    doc = artifact(healthy_contract())
    b = tmp_path / "before.json"
    a = tmp_path / "after.json"
    b.write_text(json.dumps(doc))
    a.write_text(json.dumps(doc))
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in (vd.ENV_USER, vd.ENV_PASSWORD, vd.ENV_HOST, vd.ENV_PORT)
    }
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
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "the profile survived intact" in proc.stdout


def test_addon_version_reports_a_clear_error_when_the_pattern_misses(
    monkeypatch, tmp_path
):
    """A no-match used to raise `AttributeError: 'NoneType' object has no attribute
    'group'` - an opaque traceback for a release-gate tool."""
    broken = tmp_path / "addon.xml"
    broken.write_text(
        '<addon id="script.ezmaintenanceplusplus" name="EZM">\n</addon>\n'
    )
    monkeypatch.setattr(vd, "ADDON_XML", broken)
    with pytest.raises(SystemExit) as excinfo:
        vd.addon_version()
    assert "could not read a version" in str(excinfo.value)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


@pytest.mark.parametrize("device_class", ["tvos", "android"])
def test_pull_refuses_a_box_running_different_contract_code(monkeypatch, device_class):
    """G-2: a box whose installed contract code differs must be REFUSED.

    The version string cannot catch this - it is hand-typed and does not move when
    contract code changes, so a box on an older build under the same version produced
    a fully green artifact certifying code it never ran (observed 2026-07-19). This
    drives the real refusal path rather than asserting on source text."""
    monkeypatch.setenv("KODI_JSONRPC_USER", "u")
    monkeypatch.setenv("KODI_JSONRPC_PASSWORD", "p")

    def _rpc(host, method, params=None, **kw):
        if method == "XBMC.GetInfoLabels":
            return {
                "System.BuildVersion": "21.3 (21.3.0) Git:20260101-abcdef",
                "System.FriendlyName": "Living Room",
                # A DIFFERENT build: right version string, wrong code.
                "Window(10000).Property(ezm_contract_fingerprint)": "d" * 64,
            }
        if method == "Addons.GetAddonDetails":
            return {"addon": {"version": vd.addon_version()}}
        return {}

    monkeypatch.setattr(vd, "rpc", _rpc)
    with pytest.raises(SystemExit) as e:
        vd.pull("192.0.2.1", device_class)
    msg = str(e.value)
    assert "not running the storage-contract code at HEAD" in msg
    assert "d" * 64 in msg, "the refusal must show what the BOX reported"


@pytest.mark.parametrize("device_class", ["tvos", "android"])
def test_pull_refuses_a_box_that_publishes_no_fingerprint(monkeypatch, device_class):
    """An absent property must REFUSE, never pass.

    Empty means "the box could not tell us" - an older build, or a service that has
    not started. Treating unknown as acceptable is the failure-open direction and
    would leave G-2 fully open on exactly the boxes most likely to be stale."""
    monkeypatch.setenv("KODI_JSONRPC_USER", "u")
    monkeypatch.setenv("KODI_JSONRPC_PASSWORD", "p")

    def _rpc(host, method, params=None, **kw):
        if method == "XBMC.GetInfoLabels":
            return {
                "System.BuildVersion": "21.3 (21.3.0) Git:20260101-abcdef",
                "System.FriendlyName": "Living Room",
            }
        if method == "Addons.GetAddonDetails":
            return {"addon": {"version": vd.addon_version()}}
        return {}

    monkeypatch.setattr(vd, "rpc", _rpc)
    with pytest.raises(SystemExit) as e:
        vd.pull("192.0.2.1", device_class)
    assert "<not published>" in str(e.value)
