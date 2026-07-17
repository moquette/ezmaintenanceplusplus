"""Contract tests for fake_kodi_storage.py (the two-layer tvOS userdata storage fake).

Every documented tvOS semantic gets a test: eligibility (WantsFile), key-shadows-disk
reads, "key exists, disk file gone", whole-key replace per write (never chunk-append),
delete-drops-only-the-key returning True regardless, the POSIX layer's blindness to keys,
and the REAL gzip-value binary plist mirror that nsub's plist reader decodes unmodified.
Plus the android contract: thin POSIX passthrough, no key store, nonexistent plist_path.

These test the FAKE itself, so the fakes downstream suites build on (the waiting
cross-OS tests) sit on proven ground - a fake with wrong semantics silently green-lights
the exact bug class it exists to catch.
"""

from __future__ import annotations

import gzip
import importlib
import os
import plistlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from fake_kodi_storage import FakeKodiStorage, make_modules  # noqa: E402

ADDON_MODULES = (
    Path(__file__).parent.parent
    / "script.ezmaintenanceplusplus"
    / "resources"
    / "lib"
    / "modules"
)


@pytest.fixture
def tvos(tmp_path):
    return FakeKodiStorage(tmp_path, platform="tvos")


@pytest.fixture
def android(tmp_path):
    return FakeKodiStorage(tmp_path, platform="android")


def _real(store, rel):
    return os.path.join(store.userdata, rel.replace("/", os.sep))


# --------------------------------------------------------------------------- #
# tvOS vectoring: an eligible xbmcvfs write goes to NSUserDefaults ONLY.
# --------------------------------------------------------------------------- #
def test_tvos_vfs_write_creates_key_only_disk_never_touched(tvos):
    _xbmc, vfs = make_modules(tvos)
    f = vfs.File("special://home/userdata/guisettings.xml", "w")
    assert f.write(b"<settings/>") is True
    f.close()

    assert tvos.state("guisettings.xml") == "key-only"
    # Plain POSIX sees ONLY the disk layer: nothing there.
    assert not os.path.isfile(_real(tvos, "guisettings.xml"))
    with pytest.raises(FileNotFoundError):
        open(_real(tvos, "guisettings.xml"), "rb")
    # ...while the VFS reads the key back fine.
    assert bytes(vfs.File("special://home/userdata/guisettings.xml").read()) == (
        b"<settings/>"
    )


def test_tvos_whole_key_replace_never_chunk_append(tvos):
    # SetKeyData REPLACES the key on every call: a chunked write loop must leave only
    # the LAST chunk, exactly how a real Apple TV truncates a chunk-copied XML.
    _xbmc, vfs = make_modules(tvos)
    with vfs.File("special://home/userdata/sources.xml", "w") as f:
        f.write(b"<first-chunk>")
        f.write(b"<last-chunk/>")

    assert bytes(tvos.vfs_read("special://home/userdata/sources.xml")) == (
        b"<last-chunk/>"
    )


# --------------------------------------------------------------------------- #
# The shadow: reads and exists check the KEY FIRST, disk only as fallback.
# --------------------------------------------------------------------------- #
def test_tvos_stale_key_shadows_newer_disk_content(tvos):
    tvos.seed_disk("guisettings.xml", b"<restored-newer/>")
    tvos.seed_key("guisettings.xml", b"<stale-key/>")
    assert tvos.state("guisettings.xml") == "both"

    _xbmc, vfs = make_modules(tvos)
    # The VFS serves the STALE key - this is why a file-only restore "reverts".
    assert bytes(vfs.File("special://home/userdata/guisettings.xml").read()) == (
        b"<stale-key/>"
    )
    # Plain open() sees the newer disk bytes - the two layers DISAGREE.
    with open(_real(tvos, "guisettings.xml"), "rb") as fh:
        assert fh.read() == b"<restored-newer/>"


def test_tvos_key_exists_disk_file_gone(tvos):
    # The post-posix-drop state a plain-dict fake cannot express.
    tvos.seed_key("profiles.xml", b"<profiles/>")

    _xbmc, vfs = make_modules(tvos)
    assert tvos.state("profiles.xml") == "key-only"
    assert vfs.exists("special://home/userdata/profiles.xml") is True
    assert bytes(vfs.File("special://home/userdata/profiles.xml").read()) == (
        b"<profiles/>"
    )
    assert not os.path.exists(_real(tvos, "profiles.xml"))


def test_tvos_disk_only_userdata_xml_reads_through_vfs_fallback(tvos):
    # No key -> CTVOSFile falls back to CPosixFile: a disk-only userdata xml reads fine.
    # (The foreign-LOCAL-file empty-read quirk is fake_kodi_sandbox_io's territory, not
    # a userdata behavior.)
    tvos.seed_disk("sources.xml", b"<sources/>")
    assert tvos.state("sources.xml") == "disk-only"
    assert bytes(tvos.vfs_read("special://home/userdata/sources.xml")) == b"<sources/>"
    assert tvos.vfs_exists("special://home/userdata/sources.xml") is True


def test_state_absent(tvos):
    assert tvos.state("never-written.xml") == "absent"


# --------------------------------------------------------------------------- #
# Bug 4: xbmcvfs.delete drops ONLY the key and returns True regardless.
# --------------------------------------------------------------------------- #
def test_tvos_delete_drops_only_key_leaves_posix_file(tvos):
    tvos.seed_disk("RssFeeds.xml", b"<rss/>")
    tvos.seed_key("RssFeeds.xml", b"<rss/>")

    _xbmc, vfs = make_modules(tvos)
    assert vfs.delete("special://home/userdata/RssFeeds.xml") is True
    # The key is gone; the POSIX file is left on disk, silently.
    assert tvos.state("RssFeeds.xml") == "disk-only"
    with open(_real(tvos, "RssFeeds.xml"), "rb") as fh:
        assert fh.read() == b"<rss/>"


def test_tvos_delete_returns_true_even_with_no_key_and_no_file(tvos):
    # DeleteKey returns synchronize() == YES whether or not a key existed: the boolean
    # is true even when nothing happened. Never trust it.
    _xbmc, vfs = make_modules(tvos)
    assert vfs.delete("special://home/userdata/ghost.xml") is True
    assert tvos.state("ghost.xml") == "absent"

    tvos.seed_disk("keyboard.xml", b"<k/>")
    assert vfs.delete("special://home/userdata/keyboard.xml") is True  # "succeeds"...
    assert tvos.state("keyboard.xml") == "disk-only"  # ...but the file is still there


# --------------------------------------------------------------------------- #
# Eligibility boundaries (WantsFile): exactly what vectors and what stays POSIX.
# --------------------------------------------------------------------------- #
def test_tvos_eligibility_any_depth_and_case_insensitive_xml(tvos):
    _xbmc, vfs = make_modules(tvos)
    deep = "special://home/userdata/addon_data/plugin.video.x/settings.xml"
    with vfs.File(deep, "w") as f:
        f.write(b"<s/>")
    assert tvos.state("addon_data/plugin.video.x/settings.xml") == "key-only"

    upper = "special://home/userdata/FAVOURITES.XML"
    with vfs.File(upper, "w") as f:
        f.write(b"<f/>")
    assert tvos.state("FAVOURITES.XML") == "key-only"


def test_tvos_non_xml_under_userdata_is_plain_posix(tvos):
    _xbmc, vfs = make_modules(tvos)
    with vfs.File("special://home/userdata/Database/Textures13.db", "w") as f:
        f.write(b"sqlite")
    assert tvos.state("Database/Textures13.db") == "disk-only"
    assert tvos.keys == {}


def test_tvos_siriremote_controller_xml_is_excluded_from_vectoring(tvos):
    # WantsFile excludes customcontroller.SiriRemote* - CPosixFile serves it, so an
    # xbmcvfs write is a plain POSIX write and NO key is ever created.
    _xbmc, vfs = make_modules(tvos)
    p = "special://home/userdata/keymaps/customcontroller.SiriRemote.xml"
    with vfs.File(p, "w") as f:
        f.write(b"<keymap/>")
    assert tvos.state("keymaps/customcontroller.SiriRemote.xml") == "disk-only"
    assert tvos.keys == {}
    # And delete on it is a REAL posix delete (the fallback IS reachable here).
    assert vfs.delete(p) is True
    assert tvos.state("keymaps/customcontroller.SiriRemote.xml") == "absent"


def test_tvos_xml_outside_userdata_is_plain_posix(tvos):
    _xbmc, vfs = make_modules(tvos)
    with vfs.File("special://temp/scratch.xml", "w") as f:
        f.write(b"<t/>")
    assert tvos.keys == {}
    real = os.path.join(tvos.home, "temp", "scratch.xml")
    with open(real, "rb") as fh:
        assert fh.read() == b"<t/>"


def test_tvos_profile_and_userdata_specials_map_to_userdata(tvos):
    _xbmc, vfs = make_modules(tvos)
    with vfs.File("special://profile/guisettings.xml", "w") as f:
        f.write(b"<p/>")
    assert tvos.state("guisettings.xml") == "key-only"
    assert bytes(vfs.File("special://userdata/guisettings.xml").read()) == b"<p/>"


# --------------------------------------------------------------------------- #
# The plist mirror: a REAL gzip-value binary plist, in sync on every mutation,
# decodable by nsub's reader unmodified.
# --------------------------------------------------------------------------- #
def test_plist_is_real_gzip_value_binary_plist(tvos):
    tvos.seed_key("guisettings.xml", b"<gui/>")
    path = tvos.plist_path()
    assert os.path.isfile(path)
    with open(path, "rb") as fh:
        data = plistlib.load(fh)
    assert gzip.decompress(data["/userdata/guisettings.xml"]) == b"<gui/>"
    assert data["UserdataMigrated"] is True  # Kodi's bookkeeping key is present


def test_plist_stays_in_sync_on_every_mutation(tvos):
    _xbmc, vfs = make_modules(tvos)

    def _plist_keys():
        with open(tvos.plist_path(), "rb") as fh:
            return {k for k in plistlib.load(fh) if k.startswith("/userdata/")}

    assert _plist_keys() == set()
    with vfs.File("special://home/userdata/sources.xml", "w") as f:
        f.write(b"<s/>")
    assert _plist_keys() == {"/userdata/sources.xml"}
    tvos.seed_key("profiles.xml", b"<p/>")
    assert _plist_keys() == {"/userdata/sources.xml", "/userdata/profiles.xml"}
    vfs.delete("special://home/userdata/sources.xml")
    assert _plist_keys() == {"/userdata/profiles.xml"}


def test_nsub_plist_reader_captures_key_only_file_from_this_fake(
    tvos, monkeypatch, tmp_path
):
    # End-to-end proof: the REAL nsub module, pointed at this fake via translatePath,
    # finds the plist by content and captures a key-only file into a backup zip.
    tvos.seed_key("guisettings.xml", b"<gui/>")
    tvos.seed_key("addon_data/pvr.iptvsimple/instance-settings-1.xml", b"<iptv/>")

    _xbmc, xbmcvfs = make_modules(tvos)
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)
    monkeypatch.syspath_prepend(str(ADDON_MODULES))
    monkeypatch.delitem(sys.modules, "nsub", raising=False)
    nsub = importlib.import_module("nsub")

    out = tmp_path / "out.zip"
    with zipfile.ZipFile(str(out), "w") as zf:
        added, _skipped, failed = nsub.capture_nsud_userdata(
            zf, tvos.home, already_arcs=set()
        )
    assert added == 2 and failed == 0
    with zipfile.ZipFile(str(out)) as zf:
        assert zf.read("userdata/guisettings.xml") == b"<gui/>"
        assert (
            zf.read("userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml")
            == b"<iptv/>"
        )


# --------------------------------------------------------------------------- #
# android: a thin POSIX passthrough - no keys, no shadow, no plist.
# --------------------------------------------------------------------------- #
def test_android_vfs_write_is_posix_passthrough(android):
    _xbmc, vfs = make_modules(android)
    with vfs.File("special://home/userdata/guisettings.xml", "w") as f:
        f.write(b"<settings/>")

    assert android.state("guisettings.xml") == "disk-only"
    assert android.keys == {}
    with open(_real(android, "guisettings.xml"), "rb") as fh:
        assert fh.read() == b"<settings/>"


def test_android_cross_layer_reads_agree(android):
    # A plain-written file reads identically through the VFS: no shadow to disagree.
    android.seed_disk("sources.xml", b"<sources/>")
    _xbmc, vfs = make_modules(android)
    assert bytes(vfs.File("special://home/userdata/sources.xml").read()) == (
        b"<sources/>"
    )
    assert vfs.exists("special://home/userdata/sources.xml") is True


def test_android_delete_is_a_real_posix_delete(android):
    android.seed_disk("guisettings.xml", b"<x/>")
    _xbmc, vfs = make_modules(android)
    assert vfs.delete("special://home/userdata/guisettings.xml") is True
    assert android.state("guisettings.xml") == "absent"
    # Honest POSIX semantics: a second delete finds nothing and says so.
    assert vfs.delete("special://home/userdata/guisettings.xml") is False


def test_android_plist_path_never_exists(android):
    android.seed_disk("guisettings.xml", b"<x/>")
    assert not os.path.exists(android.plist_path())


def test_android_seed_key_raises(android):
    with pytest.raises(RuntimeError):
        android.seed_key("guisettings.xml", b"<x/>")


def test_android_nsub_capture_is_a_noop(android, monkeypatch, tmp_path):
    # Same nsub run as the tvOS integration test: on android there is no plist, so the
    # capture is (0, 0, 0) - the fake reproduces why Fire TV was never affected.
    android.seed_disk("guisettings.xml", b"<x/>")
    _xbmc, xbmcvfs = make_modules(android)
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)
    monkeypatch.syspath_prepend(str(ADDON_MODULES))
    monkeypatch.delitem(sys.modules, "nsub", raising=False)
    nsub = importlib.import_module("nsub")

    out = tmp_path / "out.zip"
    with zipfile.ZipFile(str(out), "w") as zf:
        assert nsub.capture_nsud_userdata(zf, android.home, already_arcs=set()) == (
            0,
            0,
            0,
        )


# --------------------------------------------------------------------------- #
# Construction + platform reporting.
# --------------------------------------------------------------------------- #
def test_unknown_platform_rejected(tmp_path):
    with pytest.raises(ValueError):
        FakeKodiStorage(tmp_path, platform="webos")


def test_xbmc_reports_the_right_platform(tvos, android):
    xbmc_tv, _ = make_modules(tvos)
    xbmc_and, _ = make_modules(android)
    assert xbmc_tv.getCondVisibility("System.Platform.TVOS") is True
    assert xbmc_tv.getCondVisibility("System.Platform.Android") is False
    assert xbmc_and.getCondVisibility("System.Platform.TVOS") is False
    assert xbmc_and.getCondVisibility("System.Platform.Android") is True
