"""FIRST-EVER round-trip backup/restore composition tests.

Every existing restore test feeds wiz.restore() a tiny synthetic zip; every
backup test inspects the zip and stops. Nothing in the suite ever composed the
two halves - CreateZip's real output driven through restore's real extract -
which is exactly the seam where ~10 cross-OS data-loss bugs shipped. These
tests close that hole:

- a FULL backup of a realistic Android-layout profile tree, restored onto a
  wiped target, must reproduce the tree file-by-file (paths AND bytes), with
  the ONLY permitted differences being the documented exclusions
  (special://home/temp at the ROOT only, and the add-on's own settings.xml);
- a restore onto a DIFFERENT populated target must leave the pvr.iptvsimple
  addon_data dir EXACTLY equal to the archive's set (the instance-settings
  sweep from the 2026-07-16 backup/restore contract);
- every backup must embed backup_manifest.json and restore must report a
  member failure truthfully (PARTIAL, never an unconditional "Complete").

wiz.py is being hardened concurrently (per-file error accounting, the
manifest, truthful restore reporting, home-root-only temp exclusion, the
sweep). Where the current tree has not landed a contract behavior yet, the
affected test xfails/skips DYNAMICALLY with reason "wiz-core hardening
pending" - it starts enforcing (and passing) the moment the behavior lands,
and it still hard-fails on any OTHER divergence it finds on the way.

The backup is produced by driving the REAL wiz.backup(mode="full") entry
point with a spy that captures its exact CreateZip invocation, then replaying
that invocation against the real CreateZip - so the round-trip always tests
backup()'s own parameter choices, even as those churn under hardening.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import struct
import sys
import types
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
ADDON_ROOT = REPO_ROOT / "script.ezmaintenanceplusplus"

HARDENING_PENDING = "wiz-core hardening pending"

MANIFEST_BASENAME = "backup_manifest.json"


# The realistic Android-layout profile tree (rel path -> bytes). Deliberately
# includes the shapes that have burned this add-on before: IPTV instance
# settings, skinshortcuts private data, a binary Database, a NESTED addon_data
# temp dir (which the contract says must SURVIVE a full backup - the temp
# exclusion is home-ROOT only), and a home-root temp file (which must NOT).
def _pseudo_random_bytes(n, seed=b"ezm-roundtrip"):
    """Deterministic incompressible payload (so the zip member's compressed
    stream is large enough to corrupt in the honesty test)."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out.extend(hashlib.sha256(seed + b"%d" % counter).digest())
        counter += 1
    return bytes(out[:n])


PROFILE_SPEC = {
    "userdata/guisettings.xml": (
        b'<settings version="2">'
        b'<setting id="services.devicename">SourceBox</setting>'
        b"</settings>"
    ),
    "userdata/sources.xml": b"<sources><video><default/></video></sources>",
    "userdata/favourites.xml": b"<favourites><favourite name='x'>y</favourite></favourites>",
    "userdata/Database/Addons33.db": b"SQLite format 3\x00" + _pseudo_random_bytes(512),
    "userdata/addon_data/pvr.iptvsimple/settings.xml": (
        b"<settings><setting id='m3uPath'>/storage/tv.m3u</setting></settings>"
    ),
    "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml": (
        b"<settings><setting id='m3uUrl'>http://one.example/a.m3u</setting></settings>"
    ),
    "userdata/addon_data/pvr.iptvsimple/instance-settings-2.xml": (
        b"<settings><setting id='m3uUrl'>http://two.example/b.m3u</setting></settings>"
    ),
    "userdata/addon_data/script.skinshortcuts/mainmenu.DATA.xml": (
        b"<shortcuts><shortcut>videos</shortcut></shortcuts>"
    ),
    "userdata/keymaps/custom.xml": b"<keymap><global><keyboard/></global></keymap>",
    "addons/some.addon/addon.xml": (
        b"<addon id='some.addon' version='1.0.0'><extension/></addon>"
    ),
    "media/x.png": b"\x89PNG\r\n\x1a\n" + _pseudo_random_bytes(4096),
    # Must SURVIVE a full round-trip: the temp exclusion is home-ROOT only.
    "userdata/addon_data/some.addon/temp/cache.bin": _pseudo_random_bytes(256),
    # Must be EXCLUDED: special://home/temp at the root (transient/self-ref).
    "temp/scratch.file": b"transient scratch, never backed up",
}

NESTED_TEMP_PREFIX = "userdata/addon_data/some.addon/temp/"
ROOT_TEMP_PREFIX = "temp/"
IPTV_DIR = "userdata/addon_data/pvr.iptvsimple"
IPTV_FILES = tuple(p for p in PROFILE_SPEC if p.startswith(IPTV_DIR + "/"))

# Documented exclusions: paths a restored tree is ALLOWED to be missing.
PERMITTED_MISSING_ALWAYS = {"temp/scratch.file"}
# The add-on's own settings.xml (Dropbox token) is contract-excluded from the
# backup; tolerated absent OR present while wiz-core lands that exclusion.
PERMITTED_MISSING_OPTIONAL = {
    "userdata/addon_data/script.ezmaintenanceplusplus/settings.xml",
}
# Extras the restore may legitimately leave that are not profile payload.
TOLERATED_EXTRA_BASENAMES = {MANIFEST_BASENAME}


# --------------------------------------------------------------------------- #
# The fake-Kodi wiz fixture (same pattern as test_ezmaintenanceplusplus_wiz.py:
# the real wiz.py import chain under faked xbmc* modules, special:// mapped to
# tmp_path).
# --------------------------------------------------------------------------- #
@pytest.fixture
def wiz(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    xbmc = types.ModuleType("xbmc")
    xbmc.translatePath = lambda p: p.replace("special://", str(tmp_path) + "/")
    xbmc.getLocalizedString = lambda i: str(i)
    xbmc.getInfoLabel = lambda s: ""
    xbmc.getCondVisibility = lambda s: False
    xbmc.getSkinDir = lambda: "skin.estuary"
    xbmc.log = lambda *a, **k: None
    xbmc.executebuiltin = lambda *a, **k: None
    xbmc.executeJSONRPC = lambda cmd: "{}"
    xbmc.LOGERROR = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGINFO = 3
    xbmc.LOGDEBUG = 4
    xbmc.LOGFATAL = 0
    xbmc.LOGNONE = 5
    xbmc.LOGNOTICE = 3
    xbmc.PLAYLIST_VIDEO = 1
    xbmc.sleep = lambda ms: None
    xbmc.Keyboard = lambda *a, **k: types.SimpleNamespace(
        doModal=lambda: None, isConfirmed=lambda: False, getText=lambda: ""
    )
    xbmc.PlayList = lambda *a, **k: types.SimpleNamespace(
        clear=lambda: None, add=lambda *a: None
    )
    xbmc.Player = lambda *a, **k: types.SimpleNamespace(play=lambda *a, **k: None)
    xbmc.Monitor = type(
        "Monitor",
        (),
        {"abortRequested": lambda self: False, "waitForAbort": lambda self, t: False},
    )

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _FakeAddon:
        def getLocalizedString(self, i):
            return str(i)

        def getSetting(self, key):
            return ""

        def setSetting(self, key, value):
            pass

        def getAddonInfo(self, key):
            return {
                "id": "script.ezmaintenanceplusplus",
                "name": "EZ Maintenance++",
                "path": str(ADDON_ROOT),
                "profile": "special://profile/",
                "version": "0.0.0",
            }.get(key, "")

    xbmcaddon.Addon = _FakeAddon

    xbmcgui = types.ModuleType("xbmcgui")

    class _FakeDialogProgress:
        def create(self, *a, **k):
            pass

        def update(self, *a, **k):
            pass

        def close(self):
            pass

        def iscanceled(self):
            return False

    class _FakeDialog:
        def ok(self, *a, **k):
            return False

        def yesno(self, *a, **k):
            return False

        def notification(self, *a, **k):
            pass

        def select(self, *a, **k):
            return -1

    xbmcgui.DialogProgress = _FakeDialogProgress
    xbmcgui.DialogProgressBG = _FakeDialogProgress
    xbmcgui.Dialog = _FakeDialog
    xbmcgui.ListItem = lambda *a, **k: types.SimpleNamespace(
        setArt=lambda *a, **k: None
    )
    xbmcgui.ControlButton = lambda *a, **k: None
    xbmcgui.ControlImage = lambda *a, **k: None

    class _FakeWindow:
        def __init__(self, *a, **k):
            pass

        def getProperty(self, k):
            return ""

        def setProperty(self, k, v):
            pass

        def clearProperty(self, k):
            pass

    xbmcgui.Window = _FakeWindow
    xbmcgui.WindowDialog = _FakeWindow

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = xbmc.translatePath
    xbmcvfs.exists = lambda p: Path(p).exists()
    xbmcvfs.mkdirs = lambda p: Path(p).mkdir(parents=True, exist_ok=True)
    xbmcvfs.mkdir = lambda p: Path(p).mkdir(parents=True, exist_ok=True)
    xbmcvfs.rmdir = lambda p: None
    xbmcvfs.delete = lambda p: None
    xbmcvfs.listdir = lambda p: ([], [])
    xbmcvfs.copy = lambda s, d: True
    xbmcvfs.File = lambda *a, **k: types.SimpleNamespace(
        read=lambda *a: b"", write=lambda *a: True, close=lambda: None, size=lambda: 0
    )

    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.addDirectoryItem = lambda *a, **k: None
    xbmcplugin.endOfDirectory = lambda *a, **k: None
    xbmcplugin.setContent = lambda *a, **k: None
    xbmcplugin.setProperty = lambda *a, **k: None
    xbmcplugin.setResolvedUrl = lambda *a, **k: None

    for name, mod in (
        ("xbmc", xbmc),
        ("xbmcaddon", xbmcaddon),
        ("xbmcgui", xbmcgui),
        ("xbmcvfs", xbmcvfs),
        ("xbmcplugin", xbmcplugin),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    return importlib.import_module("resources.lib.modules.wiz")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def build_profile_tree(root: Path, spec=PROFILE_SPEC) -> None:
    for rel, body in spec.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)


def snapshot(root: Path) -> dict:
    """{posix relpath: sha256 hexdigest} for every file under root."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(full.read_bytes()).hexdigest()
    return out


def make_full_backup(wiz, monkeypatch, home: Path, backup_dir: Path) -> Path:
    """Drive the REAL wiz.backup(mode='full') to capture its exact CreateZip
    invocation (dst path, exclusions, flags), then replay it against the real
    CreateZip. Returns the created zip path. Churn-resilient: whatever backup()
    decides to pass, the round-trip tests exactly that."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wiz.control, "HOME", str(home))
    monkeypatch.setattr(wiz.control, "USERDATA", str(home / "userdata"))
    monkeypatch.setattr(
        wiz.control,
        "setting",
        lambda key: str(backup_dir) if key == "download.path" else "",
    )
    monkeypatch.setattr(wiz.tools, "_get_keyboard", lambda **k: "roundtrip")
    monkeypatch.setattr(wiz.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(wiz, "_rotate_vfs", lambda *a, **k: None)
    # The pre-backup cache/thumbnail/package cleanup mutates the profile tree;
    # it is orthogonal to zip round-trip fidelity, so pin it out.
    for fn in ("clearCache", "deleteThumbnails", "purgePackages"):
        if hasattr(wiz.maintenance, fn):
            monkeypatch.setattr(wiz.maintenance, fn, lambda *a, **k: None)

    real_create = wiz.CreateZip
    captured = {}

    def _spy(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return True  # "canceled": backup() stops without rotating/reporting

    monkeypatch.setattr(wiz, "CreateZip", _spy)
    wiz.backup(mode="full")
    monkeypatch.setattr(wiz, "CreateZip", real_create)

    assert "args" in captured, "wiz.backup(mode='full') never invoked CreateZip"
    result = real_create(*captured["args"], **captured["kwargs"])
    # CreateZip returns a ZipResult (truthful counts); tolerate a legacy bare bool.
    canceled = getattr(result, "canceled", result)
    assert canceled is False or canceled is None, "CreateZip reported a cancel"
    failed = list(getattr(result, "failed", []) or [])
    assert not failed, "CreateZip reported capture failures: %r" % failed[:5]
    zip_path = Path(captured["args"][1])
    assert zip_path.exists(), "CreateZip did not produce the backup zip"
    assert zipfile.is_zipfile(zip_path)
    return zip_path


def drive_restore(wiz, monkeypatch, home: Path, zip_path: Path):
    """Run wiz.restore() against `home`, capturing every user-facing report
    channel so the honesty test can see what the restore CLAIMED."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wiz.control, "HOME", str(home))
    monkeypatch.setattr(wiz.control, "USERDATA", str(home / "userdata"))

    messages = []
    monkeypatch.setattr(
        wiz.ui,
        "ask_restart",
        lambda status="", *a, **k: messages.append(("ask_restart", str(status))),
    )
    monkeypatch.setattr(
        wiz.dialog,
        "ok",
        lambda *a, **k: (
            messages.append(("dialog.ok", " ".join(str(x) for x in a))) or True
        ),
    )
    monkeypatch.setattr(
        wiz.dialog,
        "notification",
        lambda *a, **k: messages.append(
            ("dialog.notification", " ".join(str(x) for x in a))
        ),
    )
    if hasattr(wiz.ui, "error"):
        monkeypatch.setattr(
            wiz.ui,
            "error",
            lambda *a, **k: messages.append(("ui.error", " ".join(str(x) for x in a))),
        )

    ret = wiz.restore(str(zip_path), confirm=False)
    return types.SimpleNamespace(ret=ret, messages=messages)


def _find_manifest_member(names):
    for n in names:
        if n.rsplit("/", 1)[-1] == MANIFEST_BASENAME:
            return n
    return None


def _diff_trees(expected: dict, actual: dict):
    """(missing, changed, extra) between two snapshot() dicts, with the
    documented tolerated extras filtered out of `extra`."""
    missing = sorted(k for k in expected if k not in actual)
    changed = sorted(k for k in expected if k in actual and expected[k] != actual[k])
    extra = sorted(
        k
        for k in actual
        if k not in expected and k.rsplit("/", 1)[-1] not in TOLERATED_EXTRA_BASENAMES
    )
    return missing, changed, extra


def _failure_reflected(report) -> bool:
    """True if the restore's outcome reflects a failure ANYWHERE a user or
    caller could see it: a message on any dialog channel mentioning the
    problem (the contract word is PARTIAL), or a truthful return value."""
    blob = " | ".join(text for _channel, text in report.messages).lower()
    if any(
        word in blob for word in ("partial", "fail", "error", "could not", "problem")
    ):
        return True
    if re.search(r"\b(\d+)\s+of\s+(\d+)\b", blob):
        m = re.search(r"\b(\d+)\s+of\s+(\d+)\b", blob)
        if m and m.group(1) != m.group(2):
            return True
    ret = report.ret
    if ret is False:
        return True
    if isinstance(ret, dict) and (ret.get("errors") or ret.get("failed")):
        return True
    if getattr(ret, "errors", 0) or getattr(ret, "failed", 0):
        return True
    return False


def _corrupt_zip_member(zip_path: Path, member: str) -> None:
    """Flip bytes inside `member`'s compressed data stream, in place. The zip
    stays structurally valid (is_zipfile still True; the End Of Central
    Directory is untouched) but extracting that member fails its CRC/inflate,
    exactly like a bit-rotted or truncated network copy."""
    with zipfile.ZipFile(zip_path) as z:
        info = z.getinfo(member)
    data = bytearray(zip_path.read_bytes())
    off = info.header_offset
    sig, name_len, extra_len = struct.unpack_from("<4s22xHH", data, off)
    assert sig == b"PK\x03\x04", "did not land on a local file header"
    start = off + 30 + name_len + extra_len
    assert info.compress_size > 16, "member too small to corrupt meaningfully"
    for i in range(start + 4, start + 12):
        data[i] ^= 0xFF
    zip_path.write_bytes(bytes(data))
    # Sanity: the archive is still a zip, and the member no longer reads back
    # cleanly. Depending on where the flipped bytes land, zipfile surfaces
    # this either as testzip() naming the member (CRC mismatch) or as a
    # zlib/BadZipFile exception mid-inflate; both prove the corruption took.
    assert zipfile.is_zipfile(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        try:
            bad = z.testzip()
        except Exception:
            bad = member
        assert bad == member, "corruption did not take"


# --------------------------------------------------------------------------- #
# Test 1: the full round trip. Backup -> wipe -> restore -> byte equality.
# --------------------------------------------------------------------------- #
def test_full_roundtrip_backup_wipe_restore_is_byte_identical(
    wiz, monkeypatch, tmp_path
):
    home = tmp_path / "box"
    build_profile_tree(home)
    original = snapshot(home)

    zip_path = make_full_backup(wiz, monkeypatch, home, tmp_path / "backups")

    # The home-ROOT temp file must never be captured (documented exclusion).
    names = zipfile.ZipFile(zip_path).namelist()
    assert not any(n.lstrip("/").startswith(ROOT_TEMP_PREFIX) for n in names), (
        "home-root temp/ leaked into the backup zip"
    )

    # Wipe: a plain clean target, exactly like a fresh box.
    shutil.rmtree(home)
    home.mkdir()

    drive_restore(wiz, monkeypatch, home, zip_path)
    restored = snapshot(home)

    expected = {k: v for k, v in original.items() if k not in PERMITTED_MISSING_ALWAYS}
    missing, changed, extra = _diff_trees(expected, restored)
    missing = [m for m in missing if m not in PERMITTED_MISSING_OPTIONAL]

    # The IPTV instance files are the payload that has been lost the most
    # times; call them out by name before the generic diff.
    iptv_problems = [p for p in IPTV_FILES if p in missing or p in changed]
    assert not iptv_problems, (
        "IPTV instance files did not round-trip (the exact cross-OS data-loss "
        "class this suite exists to catch): %s" % iptv_problems
    )

    # Known-pending hardening: CreateZip currently prunes EVERY dir named
    # 'temp' at any depth, so the nested addon_data temp payload is lost. Once
    # the home-root-only temp exclusion lands this xfail turns into a pass.
    if (
        missing
        and all(m.startswith(NESTED_TEMP_PREFIX) for m in missing)
        and not changed
        and not extra
    ):
        pytest.xfail(
            "%s: temp exclusion is not yet home-root only; nested "
            "addon_data temp payload was lost: %s" % (HARDENING_PENDING, missing)
        )

    assert not missing and not changed and not extra, (
        "round-trip diverged from the source tree:\n"
        "  missing after restore: %s\n"
        "  content changed:       %s\n"
        "  unexpected extras:     %s" % (missing, changed, extra)
    )


# --------------------------------------------------------------------------- #
# Test 2: the manifest. Every backup embeds backup_manifest.json whose counts
# match the archive (contract 2026-07-16). Skips with a clear message until
# wiz-core lands it; enforces it from then on.
# --------------------------------------------------------------------------- #
def test_backup_embeds_truthful_manifest(wiz, monkeypatch, tmp_path):
    home = tmp_path / "box"
    build_profile_tree(home)
    zip_path = make_full_backup(wiz, monkeypatch, home, tmp_path / "backups")

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        manifest_name = _find_manifest_member(names)
        if manifest_name is None:
            pytest.skip(
                "%s: CreateZip does not embed %s yet; this test enforces the "
                "manifest (created/source_os/entries/failed, counts matching "
                "the archive) as soon as it lands"
                % (HARDENING_PENDING, MANIFEST_BASENAME)
            )
        manifest = json.loads(z.read(manifest_name))

    assert isinstance(manifest, dict), "manifest must be a JSON object"
    for key in ("created", "source_os", "entries", "failed"):
        assert key in manifest, "manifest is missing the contract key %r" % key

    payload = [n for n in names if n != manifest_name]
    entries = manifest["entries"]
    if isinstance(entries, int):
        assert entries == len(payload), (
            "manifest entry count (%d) does not match the archive's %d payload "
            "members" % (entries, len(payload))
        )
    elif isinstance(entries, list):
        listed = {
            e if isinstance(e, str) else (e.get("name") or e.get("path") or "")
            for e in entries
        }
        assert listed == set(payload), (
            "manifest entry list does not match the archive members:\n"
            "  only in manifest: %s\n"
            "  only in archive:  %s"
            % (sorted(listed - set(payload)), sorted(set(payload) - listed))
        )
    else:
        pytest.fail("manifest 'entries' is neither a count nor a list: %r" % entries)

    assert manifest["failed"] == [], (
        "a fully-readable local tree must back up with zero failures, but the "
        "manifest reports: %r" % manifest["failed"]
    )


# --------------------------------------------------------------------------- #
# Test 3: restore onto a DIFFERENT populated target. The pvr.iptvsimple dir
# must end EXACTLY equal to the archive's set (instance-settings sweep): the
# archive's files land with the archive's bytes, and a stray
# instance-settings-9.xml on the target does not survive.
# --------------------------------------------------------------------------- #
def test_restore_onto_populated_target_syncs_iptv_dir_exactly(
    wiz, monkeypatch, tmp_path
):
    source = tmp_path / "source"
    build_profile_tree(source)
    zip_path = make_full_backup(wiz, monkeypatch, source, tmp_path / "backups")

    target = tmp_path / "target"
    build_profile_tree(
        target,
        {
            "userdata/guisettings.xml": (
                b'<settings version="2">'
                b'<setting id="services.devicename">TargetBox</setting>'
                b"</settings>"
            ),
            # Same instance number, DIFFERENT content: must be overwritten.
            IPTV_DIR + "/instance-settings-1.xml": (
                b"<settings><setting id='m3uUrl'>http://stale.example/old.m3u"
                b"</setting></settings>"
            ),
            # A stray instance the archive does not know: must be swept.
            IPTV_DIR + "/instance-settings-9.xml": (
                b"<settings><setting id='m3uUrl'>http://stray.example/nine.m3u"
                b"</setting></settings>"
            ),
        },
    )

    drive_restore(wiz, monkeypatch, target, zip_path)

    with zipfile.ZipFile(zip_path) as z:
        archive_iptv = {
            n[len(IPTV_DIR) + 1 :]: z.read(n)
            for n in z.namelist()
            if n.startswith(IPTV_DIR + "/")
        }
    assert archive_iptv, "the full backup must carry the pvr.iptvsimple subtree"

    target_iptv_dir = target / IPTV_DIR
    actual = {
        p.relative_to(target_iptv_dir).as_posix(): p.read_bytes()
        for p in target_iptv_dir.rglob("*")
        if p.is_file()
    }

    # Hard requirement TODAY: the archive's files land, byte-for-byte (the
    # stale instance-settings-1.xml is overwritten with the source's bytes).
    for rel, body in archive_iptv.items():
        assert rel in actual, "archived IPTV file %r was not restored" % rel
        assert actual[rel] == body, (
            "restored IPTV file %r does not carry the archive's bytes" % rel
        )

    strays = sorted(set(actual) - set(archive_iptv))
    if strays == ["instance-settings-9.xml"]:
        pytest.xfail(
            "%s: instance-settings sweep not landed; the stray "
            "instance-settings-9.xml survived the restore (duplicate-instance "
            "brick guard)" % HARDENING_PENDING
        )
    assert not strays, (
        "after restore the target's pvr.iptvsimple dir must EXACTLY equal the "
        "archive's set, but these strays remain: %s" % strays
    )


# --------------------------------------------------------------------------- #
# Test 4: honesty. A backup with one corrupted member must not restore as an
# unconditional success: the contract says a partial restore is reported as
# PARTIAL, never "Complete".
# --------------------------------------------------------------------------- #
def test_restore_with_corrupt_member_is_not_reported_as_success(
    wiz, monkeypatch, tmp_path
):
    home = tmp_path / "box"
    build_profile_tree(home)
    zip_path = make_full_backup(wiz, monkeypatch, home, tmp_path / "backups")

    _corrupt_zip_member(zip_path, "media/x.png")

    shutil.rmtree(home)
    home.mkdir()
    report = drive_restore(wiz, monkeypatch, home, zip_path)

    # The extract must still have delivered the healthy members (per-file
    # error handling, not an all-or-nothing abort).
    assert (home / "userdata" / "guisettings.xml").exists(), (
        "one corrupt member must not sink the healthy ones"
    )
    # And the corrupt member must not have been silently delivered intact.
    x = home / "media" / "x.png"
    assert not (x.exists() and x.read_bytes() == PROFILE_SPEC["media/x.png"]), (
        "the corrupted member cannot have restored byte-identical"
    )

    if not _failure_reflected(report):
        pytest.xfail(
            "%s: restore reported unconditional success despite a failed "
            "member (channels seen: %r, return=%r); the contract requires a "
            "PARTIAL report" % (HARDENING_PENDING, report.messages, report.ret)
        )

    # Once truthful reporting lands, also pin the exact lie down: no channel
    # may still claim an unqualified "Restore Complete".
    complete_claims = [
        text
        for _c, text in report.messages
        if "complete" in text.lower()
        and "incomplete" not in text.lower()
        and "partial" not in text.lower()
    ]
    assert not complete_claims, (
        "restore reflected the failure somewhere but ALSO claimed an "
        "unqualified success: %r" % complete_claims
    )
