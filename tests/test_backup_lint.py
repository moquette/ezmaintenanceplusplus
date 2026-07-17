"""Tests for tools/backup_lint.py - the host-side backup-archive analyzer.

Builds synthetic backup zips exercising every verdict path: anchor
classification, manifest presence/parse/failed-list, completeness essentials,
IPTV capture, cross-OS path portability, secret hygiene, and the
platform-binary add-on heuristic. Pure host-side: no Kodi fakes needed, no
device is ever touched.
"""

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import backup_lint  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

GOOD_MANIFEST = json.dumps(
    {
        "created": "2026-07-16T12:00:00Z",
        "source_os": "android",
        "entries": 6,
        "failed": [],
    }
)

INSTANCE_PORTABLE = """<settings version="2">
    <setting id="kodi_addon_instance_name">Network24</setting>
    <setting id="kodi_addon_instance_enabled">true</setting>
    <setting id="m3uPath">nfs://192.168.7.5/kodi/playlists/Network24.m3u</setting>
    <setting id="epgUrl">http://iptv-a.example:8080/xmltv.php?u=x&amp;p=y</setting>
</settings>
"""

PYTHON_ADDON_XML = """<?xml version="1.0" encoding="UTF-8"?>
<addon id="plugin.video.foo" name="Foo" version="1.0.0" provider-name="x">
    <extension point="xbmc.python.pluginsource" library="default.py"/>
</addon>
"""

BINARY_ADDON_XML = """<?xml version="1.0" encoding="UTF-8"?>
<addon id="pvr.iptvsimple" name="PVR IPTV Simple" version="21.8.0" provider-name="x">
    <extension point="kodi.pvrclient"
        library_android="pvr.iptvsimple.so"
        library_darwin_embedded="pvr.iptvsimple.framework"/>
</addon>
"""


def full_backup_members(**overrides):
    """Baseline HOME-anchored (full) backup that passes every check."""
    members = {
        "backup_manifest.json": GOOD_MANIFEST,
        "userdata/guisettings.xml": "<settings/>",
        "userdata/sources.xml": "<sources/>",
        "userdata/Database/Addons33.db": "sqlite",
        "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": INSTANCE_PORTABLE,
        "addons/plugin.video.foo/addon.xml": PYTHON_ADDON_XML,
        "addons/plugin.video.foo/default.py": "pass",
    }
    members.update(overrides)
    return {k: v for k, v in members.items() if v is not None}


def userdata_backup_members(**overrides):
    """Baseline USERDATA-anchored (settings) backup: bare userdata contents."""
    members = {
        "backup_manifest.json": GOOD_MANIFEST,
        "guisettings.xml": "<settings/>",
        "sources.xml": "<sources/>",
        "Database/Addons33.db": "sqlite",
        "addon_data/pvr.iptvsimple/instance-settings-1.xml": INSTANCE_PORTABLE,
    }
    members.update(overrides)
    return {k: v for k, v in members.items() if v is not None}


def make_zip(tmp_path, members, name="backup.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for arc, data in members.items():
            zf.writestr(arc, data)
    return str(path)


def by_check(results):
    return {r["check"]: r for r in results}


def instance_with(**settings):
    rows = "".join(
        '<setting id="%s">%s</setting>' % (k, v) for k, v in settings.items()
    )
    return '<settings version="2">%s</settings>' % rows


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_good_full_backup_passes_everything(tmp_path):
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, full_backup_members()))
    checks = by_check(results)
    assert ok
    assert all(r["verdict"] != "FAIL" for r in results)
    assert checks["anchor"]["verdict"] == "PASS"
    assert "home-anchored" in checks["anchor"]["reason"]
    assert checks["manifest"]["verdict"] == "PASS"
    assert checks["guisettings"]["verdict"] == "PASS"
    assert checks["database"]["verdict"] == "PASS"
    assert checks["iptv"]["verdict"] == "PASS"
    assert checks["portability"]["verdict"] == "PASS"
    assert checks["secrets"]["verdict"] == "PASS"
    assert checks["binary-addons"]["verdict"] == "PASS"


def test_good_userdata_backup_passes(tmp_path):
    results, ok = backup_lint.lint_archive(
        make_zip(tmp_path, userdata_backup_members())
    )
    checks = by_check(results)
    assert ok
    assert checks["anchor"]["verdict"] == "PASS"
    assert "userdata-anchored" in checks["anchor"]["reason"]
    assert checks["iptv"]["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# anchor
# ---------------------------------------------------------------------------


def test_mixed_roots_fail_anchor(tmp_path):
    # A home-anchored archive with bare userdata content at the root: exactly
    # the members the restore's _extract_skip silently drops.
    members = full_backup_members()
    members["guisettings.xml"] = "<settings/>"
    members["addon_data/pvr.iptvsimple/instance-settings-1.xml"] = INSTANCE_PORTABLE
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    checks = by_check(results)
    assert not ok
    assert checks["anchor"]["verdict"] == "FAIL"
    assert "silently skipped" in checks["anchor"]["reason"]
    assert "guisettings.xml" in checks["anchor"]["details"]["strays"]


def test_manifest_root_member_is_not_a_stray(tmp_path):
    results, _ = backup_lint.lint_archive(make_zip(tmp_path, full_backup_members()))
    assert by_check(results)["anchor"]["verdict"] == "PASS"


def test_empty_archive_fails_anchor(tmp_path):
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, {}))
    assert not ok
    assert by_check(results)["anchor"]["verdict"] == "FAIL"
    assert "empty" in by_check(results)["anchor"]["reason"]


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def test_missing_manifest_is_warn_not_fail(tmp_path):
    members = full_backup_members(**{"backup_manifest.json": None})
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    checks = by_check(results)
    assert checks["manifest"]["verdict"] == "WARN"
    assert ok  # a WARN alone never flips the exit code


def test_unparseable_manifest_fails(tmp_path):
    members = full_backup_members(**{"backup_manifest.json": "{not json"})
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert not ok
    assert by_check(results)["manifest"]["verdict"] == "FAIL"


def test_manifest_with_failures_fails(tmp_path):
    bad = json.dumps(
        {
            "created": "2026-07-16",
            "source_os": "darwin_embedded",
            "entries": 3,
            "failed": ["userdata/profiles.xml", "userdata/RssFeeds.xml"],
        }
    )
    members = full_backup_members(**{"backup_manifest.json": bad})
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    checks = by_check(results)
    assert not ok
    assert checks["manifest"]["verdict"] == "FAIL"
    assert "2 capture failure(s)" in checks["manifest"]["reason"]


# ---------------------------------------------------------------------------
# completeness essentials
# ---------------------------------------------------------------------------


def test_missing_guisettings_fails(tmp_path):
    members = full_backup_members(**{"userdata/guisettings.xml": None})
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert not ok
    assert by_check(results)["guisettings"]["verdict"] == "FAIL"


def test_sources_absent_is_reported_not_failed(tmp_path):
    members = full_backup_members(**{"userdata/sources.xml": None})
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    checks = by_check(results)
    assert ok
    assert checks["sources"]["verdict"] == "PASS"
    assert "absent" in checks["sources"]["reason"]


def test_full_backup_without_database_fails(tmp_path):
    members = full_backup_members(**{"userdata/Database/Addons33.db": None})
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert not ok
    assert by_check(results)["database"]["verdict"] == "FAIL"


def test_userdata_backup_without_database_warns_only(tmp_path):
    members = userdata_backup_members(**{"Database/Addons33.db": None})
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert ok
    assert by_check(results)["database"]["verdict"] == "WARN"


# ---------------------------------------------------------------------------
# IPTV capture
# ---------------------------------------------------------------------------


def test_full_backup_without_iptv_fails(tmp_path):
    members = full_backup_members(
        **{"userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": None}
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    checks = by_check(results)
    assert not ok
    assert checks["iptv"]["verdict"] == "FAIL"
    assert "pvr.iptvsimple" in checks["iptv"]["reason"]


def test_userdata_backup_without_iptv_warns(tmp_path):
    members = userdata_backup_members(
        **{"addon_data/pvr.iptvsimple/instance-settings-1.xml": None}
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert ok
    assert by_check(results)["iptv"]["verdict"] == "WARN"


def test_iptv_instance_list_is_reported(tmp_path):
    members = full_backup_members()
    members["userdata/addon_data/pvr.iptvsimple/instance-settings-2.xml"] = (
        instance_with(m3uPath="smb://mini/kodi/sv.m3u")
    )
    members["userdata/addon_data/pvr.iptvsimple/customTVGroups-Network24.xml"] = (
        "<groups/>"
    )
    results, _ = backup_lint.lint_archive(make_zip(tmp_path, members))
    iptv = by_check(results)["iptv"]
    assert iptv["verdict"] == "PASS"
    assert iptv["details"]["instances"] == [
        "instance-settings-1.xml",
        "instance-settings-2.xml",
    ]


# ---------------------------------------------------------------------------
# portability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "nfs://192.168.7.5/kodi/x.m3u",
        "smb://mini/kodi/x.m3u",
        "http://iptv.example/get.php?u=1",
        "https://iptv.example/get.php?u=1",
        "special://userdata/addon_data/pvr.iptvsimple/playlists/x.m3u",
    ],
)
def test_portable_locations_pass(tmp_path, value):
    members = full_backup_members(
        **{
            "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": instance_with(
                m3uPath=value
            )
        }
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert ok
    assert by_check(results)["portability"]["verdict"] == "PASS"


@pytest.mark.parametrize(
    "value",
    [
        "/sdcard/kodi/iptv/Network24.m3u",
        "/private/var/mobile/Containers/Data/Application/X/Library/Caches/Kodi/x.m3u",
        "/storage/emulated/0/_T7B/kodi/iptv/Network24.m3u",
    ],
)
def test_device_absolute_paths_fail_with_value(tmp_path, value):
    members = full_backup_members(
        **{
            "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": instance_with(
                m3uPath=value
            )
        }
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    port = by_check(results)["portability"]
    assert not ok
    assert port["verdict"] == "FAIL"
    assert value in port["reason"]  # the offending value is named
    assert "m3uPath" in port["reason"]


def test_device_absolute_epgurl_also_fails(tmp_path):
    members = full_backup_members(
        **{
            "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": instance_with(
                m3uPath="nfs://192.168.7.5/kodi/x.m3u",
                epgUrl="/sdcard/kodi/epg.xml",
            )
        }
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    port = by_check(results)["portability"]
    assert not ok
    assert port["verdict"] == "FAIL"
    assert "epgUrl" in port["reason"]
    assert "/sdcard/kodi/epg.xml" in port["reason"]


def test_unrecognized_location_form_warns(tmp_path):
    members = full_backup_members(
        **{
            "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": instance_with(
                m3uPath="ftp://weird.example/x.m3u"
            )
        }
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert ok
    assert by_check(results)["portability"]["verdict"] == "WARN"


def test_unparseable_instance_settings_warn(tmp_path):
    members = full_backup_members(
        **{"userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": "<settings"}
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert ok
    assert by_check(results)["portability"]["verdict"] == "WARN"


def test_no_instance_settings_portability_is_na_pass(tmp_path):
    members = full_backup_members(
        **{
            "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": None,
            "userdata/addon_data/pvr.iptvsimple/customTVGroups-X.xml": "<groups/>",
        }
    )
    results, _ = backup_lint.lint_archive(make_zip(tmp_path, members))
    port = by_check(results)["portability"]
    assert port["verdict"] == "PASS"
    assert "no IPTV instance-settings" in port["reason"]


# ---------------------------------------------------------------------------
# secret hygiene
# ---------------------------------------------------------------------------


def test_embedded_ezm_settings_fails(tmp_path):
    members = full_backup_members()
    members["userdata/addon_data/script.ezmaintenanceplusplus/settings.xml"] = (
        "<settings><setting id='dropbox_refresh_token'>tok</setting></settings>"
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    secrets = by_check(results)["secrets"]
    assert not ok
    assert secrets["verdict"] == "FAIL"
    assert "Dropbox token" in secrets["reason"]


def test_per_profile_ezm_settings_also_fails(tmp_path):
    members = userdata_backup_members()
    members["profiles/kid/addon_data/script.ezmaintenanceplusplus/settings.xml"] = (
        "<settings/>"
    )
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert not ok
    assert by_check(results)["secrets"]["verdict"] == "FAIL"


def test_other_addon_settings_are_fine(tmp_path):
    members = full_backup_members()
    members["userdata/addon_data/plugin.video.foo/settings.xml"] = "<settings/>"
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    assert ok
    assert by_check(results)["secrets"]["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# binary add-ons
# ---------------------------------------------------------------------------


def test_binary_addon_xml_markers_warn(tmp_path):
    members = full_backup_members()
    members["addons/pvr.iptvsimple/addon.xml"] = BINARY_ADDON_XML
    results, ok = backup_lint.lint_archive(make_zip(tmp_path, members))
    binaries = by_check(results)["binary-addons"]
    assert ok  # WARN, not FAIL
    assert binaries["verdict"] == "WARN"
    assert "pvr.iptvsimple" in binaries["details"]["binary_addons"]


def test_native_lib_member_warns(tmp_path):
    members = full_backup_members()
    members["addons/inputstream.adaptive/addon.xml"] = PYTHON_ADDON_XML
    members["addons/inputstream.adaptive/inputstream.adaptive.so"] = "\x7fELF"
    results, _ = backup_lint.lint_archive(make_zip(tmp_path, members))
    binaries = by_check(results)["binary-addons"]
    assert binaries["verdict"] == "WARN"
    assert "inputstream.adaptive" in binaries["details"]["binary_addons"]


def test_pure_python_addons_pass(tmp_path):
    results, _ = backup_lint.lint_archive(make_zip(tmp_path, full_backup_members()))
    assert by_check(results)["binary-addons"]["verdict"] == "PASS"


def test_userdata_backup_has_no_addons_to_check(tmp_path):
    results, _ = backup_lint.lint_archive(make_zip(tmp_path, userdata_backup_members()))
    binaries = by_check(results)["binary-addons"]
    assert binaries["verdict"] == "PASS"
    assert "no addons/ tree" in binaries["reason"]


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_exit_zero_on_pass(tmp_path, capsys):
    path = make_zip(tmp_path, full_backup_members())
    assert backup_lint.main([path]) == 0
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out


def test_cli_exit_one_on_fail(tmp_path, capsys):
    members = full_backup_members(**{"userdata/guisettings.xml": None})
    path = make_zip(tmp_path, members)
    assert backup_lint.main([path]) == 1
    out = capsys.readouterr().out
    assert "RESULT: FAIL" in out
    assert "FAIL guisettings" in out.replace("  ", " ")


def test_cli_json_output(tmp_path, capsys):
    path = make_zip(tmp_path, full_backup_members())
    assert backup_lint.main([path, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["archive"] == path
    names = {c["check"] for c in data["checks"]}
    assert {
        "anchor",
        "manifest",
        "guisettings",
        "sources",
        "database",
        "iptv",
        "portability",
        "secrets",
        "binary-addons",
    } <= names
    assert all(c["verdict"] in ("PASS", "WARN", "FAIL") for c in data["checks"])


def test_not_a_zip_fails_cleanly(tmp_path, capsys):
    path = tmp_path / "not_a_zip.zip"
    path.write_text("this is not a zip archive")
    assert backup_lint.main([str(path)]) == 1
    assert "cannot open as a zip" in capsys.readouterr().out


def test_missing_file_fails_cleanly(tmp_path):
    results, ok = backup_lint.lint_archive(str(tmp_path / "nope.zip"))
    assert not ok
    assert results[0]["verdict"] == "FAIL"
