"""Archive-anchor edge-layout contract tests for wiz.py's restore().

THE CONTRACT (owner-decided 2026-07-16, see CLAUDE.md "The backup/restore
contract"): no archive member may be silently discarded by a restore. Members
that cannot be mapped to a destination must be surfaced in the restore result
or user-facing report as skipped-with-reason, and counted; a partial restore is
reported as PARTIAL, never "Complete". Well-formed archives (pure home-anchored,
pure userdata-anchored) must map every member.

Verified defect these tests pin (investigation GAP): _archive_anchor()
classifies an archive as "home" if ANY member's first segment is userdata or
addons; on a home anchor _extract_skip() silently drops every member whose
first segment is not in _HOME_ALLOWED_TOP. A mixed/legacy archive (addons/...
members plus BARE guisettings.xml and addon_data/... at the archive root, no
userdata/ prefix) is anchored to home and its bare userdata content is silently
discarded, while ui.ask_restart() tells the user "Restore Complete: N items"
with N counting the dropped members. Silent discard of profile data is the bug
class this file exists to kill.

Tests the current wiz.py cannot pass are marked xfail with the exact missing
behavior. Assertions are written to the contract and must never be weakened to
pass; when the wiz-core hardening lands, the xfail markers come off.

The fixture below stubs the Kodi runtime the same way
tests/test_ezmaintenanceplusplus_wiz.py does (fake xbmc*/xbmcaddon/xbmcgui/
xbmcvfs/xbmcplugin modules, real wiz.py imported underneath), and builds real
zips with zipfile in tmp_path.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
import zipfile as _zip
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
ADDON_ROOT = REPO_ROOT / "script.ezmaintenanceplusplus"


# --------------------------------------------------------------------------- #
# Harness: fake Kodi modules, real wiz.py (same pattern as
# test_ezmaintenanceplusplus_wiz.py's `wiz` fixture)
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


def _make_valid_zip(path, files):
    with _zip.ZipFile(path, "w") as z:
        for name, body in files:
            z.writestr(name, body)
    return path


def _prep_restore(wiz, monkeypatch, tmp_path):
    """control.HOME + control.USERDATA as real tmp dirs; capture every surface a
    restore can report through (return value, ask_restart status, dialog calls)."""
    home = tmp_path / "home"
    (home / "userdata").mkdir(parents=True)
    monkeypatch.setattr(wiz.control, "HOME", str(home))
    monkeypatch.setattr(wiz.control, "USERDATA", str(home / "userdata"))

    rep = types.SimpleNamespace(
        restart_statuses=[],  # ui.ask_restart(status, ...) strings
        ok_calls=[],  # dialog.ok(...) arg tuples
        notifications=[],  # dialog.notification(...) arg tuples
        result=None,  # restore() return value, if the hardened API has one
    )

    def _ask_restart(status="", heading=None, **k):
        rep.restart_statuses.append(str(status))

    monkeypatch.setattr(wiz.ui, "ask_restart", _ask_restart)

    class _RecordingDialog:
        def ok(self, *a, **k):
            rep.ok_calls.append(a)
            return True

        def yesno(self, *a, **k):
            return True

        def notification(self, *a, **k):
            rep.notifications.append(a)

        def select(self, *a, **k):
            return -1

        def textviewer(self, *a, **k):
            rep.ok_calls.append(a)

    monkeypatch.setattr(wiz, "dialog", _RecordingDialog())
    return home, rep


def _report_text(rep):
    """Every user-facing string the restore produced, flattened for matching."""
    parts = list(rep.restart_statuses)
    for call in rep.ok_calls + rep.notifications:
        parts.extend(str(a) for a in call)
    if rep.result is not None:
        parts.append(repr(rep.result))
    return " | ".join(parts).lower()


def _result_skip_count(result):
    """Best-effort skipped/unmapped count from a structured restore result, for
    whatever shape the hardened reporting API exposes. None if not exposed."""
    if result is None:
        return None
    candidates = {}
    if isinstance(result, dict):
        candidates = result
    else:
        for key in (
            "skipped",
            "skipped_members",
            "skipped_count",
            "unmapped",
            "unmapped_members",
            "failed",
        ):
            if hasattr(result, key):
                candidates[key] = getattr(result, key)
    for key in (
        "skipped",
        "skipped_members",
        "skipped_count",
        "unmapped",
        "unmapped_members",
        "failed",
    ):
        val = candidates.get(key)
        if val is None:
            continue
        if isinstance(val, int):
            return val
        try:
            return len(val)
        except TypeError:
            continue
    return None


def _skips_surfaced(rep, minimum):
    """True when the restore surfaced skipped/unmapped members to the user AND
    counted them: a structured result with a skipped count >= minimum, or a
    user-facing report that names the skip (skipped/unmapped/not restored/
    partial) rather than claiming a clean Complete."""
    count = _result_skip_count(rep.result)
    if count is not None:
        # A structured result is authoritative: trust its count both ways
        # (text-scanning its repr would trip on the KEY NAMES, e.g. 'skipped': 0).
        return count >= minimum
    text = _report_text(rep)
    return any(
        marker in text
        for marker in ("skip", "unmapped", "not restored", "partial", "could not")
    )


def _claims_unqualified_success(rep):
    """True when the only story told to the user is a clean completion."""
    text = _report_text(rep)
    if not text:
        return False
    complete = "restore complete" in text
    qualified = any(
        marker in text
        for marker in (
            "skip",
            "unmapped",
            "not restored",
            "partial",
            "failed",
            "could not",
            "missing",
        )
    )
    return complete and not qualified


def _found_under(root, rel_suffix):
    """True if some file under root ends with the given relative path."""
    suffix = Path(rel_suffix).parts
    for p in root.rglob(suffix[-1]):
        if p.is_file() and tuple(p.parts[-len(suffix) :]) == suffix:
            return True
    return False


# --------------------------------------------------------------------------- #
# (a) Pure userdata-anchored archive: every member maps under userdata/.
# --------------------------------------------------------------------------- #
def test_pure_userdata_zip_restores_every_member_under_userdata(
    wiz, monkeypatch, tmp_path
):
    home, rep = _prep_restore(wiz, monkeypatch, tmp_path)
    members = [
        ("guisettings.xml", "<settings/>"),
        ("addon_data/pvr.iptvsimple/instance-settings-1.xml", "<i/>"),
        ("sources.xml", "<sources/>"),
        ("keymaps/keyboard.xml", "<k/>"),
    ]
    src = _make_valid_zip(tmp_path / "kodi_settings_202607161200.zip", members)

    rep.result = wiz.restore(str(src), confirm=False)

    for name, _body in members:
        assert (home / "userdata" / name).is_file(), (
            "well-formed userdata-anchored archive: member %r must be restored "
            "under userdata/" % name
        )
    # Nothing may scatter into the HOME root (the historical brick bug).
    assert not (home / "guisettings.xml").exists()
    assert not (home / "addon_data").exists()
    # A fully-mapped archive must not be reported as partial/skipped.
    assert not _skips_surfaced(rep, 1), (
        "a fully-mapped archive must not report skipped members; report was: %r"
        % _report_text(rep)
    )


# --------------------------------------------------------------------------- #
# (b) Pure home-anchored archive: every member maps under home.
# --------------------------------------------------------------------------- #
def test_pure_home_zip_restores_every_member_under_home(wiz, monkeypatch, tmp_path):
    home, rep = _prep_restore(wiz, monkeypatch, tmp_path)
    members = [
        ("userdata/guisettings.xml", "<settings/>"),
        ("userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml", "<i/>"),
        ("addons/plugin.x/addon.xml", "<a/>"),
        ("media/Splash.png", "png"),
        ("system/settings/settings.xml", "<s/>"),
    ]
    src = _make_valid_zip(tmp_path / "kodi_backup_202607161200.zip", members)

    rep.result = wiz.restore(str(src), confirm=False)

    for name, _body in members:
        assert (home / name).is_file(), (
            "well-formed home-anchored archive: member %r must be restored "
            "under home" % name
        )
    assert not _skips_surfaced(rep, 1), (
        "a fully-mapped archive must not report skipped members; report was: %r"
        % _report_text(rep)
    )


# --------------------------------------------------------------------------- #
# (c) MIXED/legacy archive: addons/ member plus BARE userdata members at the
# archive root. The bare members must be either restored correctly (under
# userdata/) or surfaced as skipped-with-reason and counted. The old behavior
# (bare members silently dropped, "Restore Complete: 3 items" counting them)
# must fail here.
# --------------------------------------------------------------------------- #
# Landed 2026-07-16: unmapped members are surfaced in the report (never a
# silent drop with an unqualified Complete); this test now ENFORCES it.
def test_mixed_zip_bare_userdata_members_not_silently_discarded(
    wiz, monkeypatch, tmp_path
):
    home, rep = _prep_restore(wiz, monkeypatch, tmp_path)
    bare_members = [
        ("guisettings.xml", "<settings/>"),
        ("addon_data/pvr.iptvsimple/instance-settings-1.xml", "<i/>"),
    ]
    src = _make_valid_zip(
        tmp_path / "kodi_backup_legacy_mixed.zip",
        [("addons/foo/addon.xml", "<a/>")] + bare_members,
    )

    rep.result = wiz.restore(str(src), confirm=False)

    for name, _body in bare_members:
        restored = (home / "userdata" / name).is_file()
        # Landing at the HOME root is the scatter/brick bug, never "restored".
        surfaced = _skips_surfaced(rep, len(bare_members))
        assert restored or surfaced, (
            "CONTRACT VIOLATION: bare member %r was silently discarded - it is "
            "not under userdata/ and the restore report does not surface it as "
            "skipped; user saw: %r" % (name, _report_text(rep))
        )
    # Whatever happened to the bare members, the user must never be told an
    # unqualified 'Restore Complete' while members were dropped.
    dropped = [
        name for name, _body in bare_members if not (home / "userdata" / name).is_file()
    ]
    if dropped:
        assert not _claims_unqualified_success(rep), (
            "CONTRACT VIOLATION: members %r were dropped but the user was told "
            "an unqualified success: %r" % (dropped, _report_text(rep))
        )


# --------------------------------------------------------------------------- #
# (d) Archive with only unknown roots: members must not silently vanish, and
# the restore must not claim success while discarding them.
# --------------------------------------------------------------------------- #
def test_unknown_roots_zip_members_do_not_silently_vanish(wiz, monkeypatch, tmp_path):
    home, rep = _prep_restore(wiz, monkeypatch, tmp_path)
    members = [
        ("foo/bar.txt", "payload"),
        ("stray.bin", "payload2"),
    ]
    src = _make_valid_zip(tmp_path / "kodi_backup_unknown_roots.zip", members)

    rep.result = wiz.restore(str(src), confirm=False)

    vanished = [name for name, _body in members if not _found_under(home, name)]
    if vanished:
        # Discarding is only acceptable when surfaced-with-count, or when the
        # restore refuses the archive outright instead of claiming success.
        assert _skips_surfaced(rep, len(vanished)) or not _claims_unqualified_success(
            rep
        ), (
            "CONTRACT VIOLATION: unknown-root members %r vanished while the "
            "user was told an unqualified success: %r" % (vanished, _report_text(rep))
        )


# --------------------------------------------------------------------------- #
# (e) backup_manifest.json reconciliation: when the manifest promises more
# entries than extraction produced, the report must reflect the mismatch
# (PARTIAL / missing), never a clean Complete.
# --------------------------------------------------------------------------- #
# Landed 2026-07-16: restore() reconciles the archive against its manifest
# (count OR name-list shape) and reports missing entries; this test ENFORCES it.
def test_manifest_entries_exceeding_extraction_are_reported(wiz, monkeypatch, tmp_path):
    home, rep = _prep_restore(wiz, monkeypatch, tmp_path)
    real_members = [
        ("userdata/guisettings.xml", "<settings/>"),
        ("addons/plugin.x/addon.xml", "<a/>"),
    ]
    manifest = {
        "created": "2026-07-16T12:00:00Z",
        "source_os": "tvos",
        "entries": [name for name, _b in real_members]
        + [
            # Promised by the manifest, absent from the archive: the tvOS
            # capture failure shape (settings that never made it into the zip).
            "userdata/sources.xml",
            "userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml",
        ],
        "failed": [],
    }
    src = _make_valid_zip(
        tmp_path / "kodi_backup_short_of_manifest.zip",
        real_members + [("backup_manifest.json", json.dumps(manifest))],
    )

    rep.result = wiz.restore(str(src), confirm=False)

    text = _report_text(rep)
    structured = rep.result is not None and _result_skip_count(rep.result) is not None
    mismatch_surfaced = structured or any(
        marker in text for marker in ("manifest", "missing", "partial", "mismatch")
    )
    assert mismatch_surfaced, (
        "CONTRACT VIOLATION: the manifest promises 4 entries, the archive holds "
        "2, and the restore reported no mismatch; user saw: %r" % text
    )
    assert not _claims_unqualified_success(rep), (
        "CONTRACT VIOLATION: a manifest-short restore must be reported as "
        "PARTIAL, never an unqualified 'Restore Complete'; user saw: %r" % text
    )


# --------------------------------------------------------------------------- #
# (f) ROUND-TRIP SYMMETRY: backup refuses to EMBED EZM's own settings.xml
# (_is_secret_arc), so the extract must refuse to WRITE one. Every archive built
# before 2026-07-16, and any archive from another box or an older build, still
# carries that member; restoring one handed THIS box the SOURCE box's
# download/restore paths and its dropbox_refresh_token - exactly the leak the
# backup-side exclusion exists to prevent. An exclusion enforced on one side of a
# round trip is not an exclusion.
# --------------------------------------------------------------------------- #
def test_restore_never_writes_ezm_own_settings_over_this_box(
    wiz, monkeypatch, tmp_path
):
    home, rep = _prep_restore(wiz, monkeypatch, tmp_path)
    secret_rel = "userdata/addon_data/script.ezmaintenanceplusplus/settings.xml"

    # This box's OWN settings, as they exist before the restore.
    mine = home / secret_rel
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text("<settings><mine/></settings>")

    src = _make_valid_zip(
        tmp_path / "kodi_backup_202607161200.zip",
        [
            ("userdata/guisettings.xml", "<settings/>"),
            (secret_rel, "<settings><FOREIGN_BOX_TOKEN/></settings>"),
        ],
    )

    rep.result = wiz.restore(str(src), confirm=False)

    assert mine.read_text() == "<settings><mine/></settings>", (
        "CONTRACT VIOLATION: restore overwrote this box's own EZM settings.xml "
        "with the archive's copy, importing the source box's paths and token"
    )
    assert "FOREIGN_BOX_TOKEN" not in mine.read_text()
    # Real content still lands.
    assert (home / "userdata" / "guisettings.xml").is_file()
    # The skip is POLICY, not unplaceable content: it must not be reported as a
    # failed/unmapped member, or a clean restore screams at the owner.
    assert not _skips_surfaced(rep, 1), (
        "a deliberate secret-skip must not be reported as a skipped member; "
        "report was: %r" % _report_text(rep)
    )


# --------------------------------------------------------------------------- #
# (g) SELF-DOWNGRADE: a restore must never write EZM's own add-on tree. The
# extract would replace the RUNNING add-on with whatever version the archive
# carries, so restoring last week's backup silently boots the older EZ
# Maintenance++ and undoes every fix shipped since. The installed copy comes
# from the Kodi repo; there is nothing to recover from the zip.
# --------------------------------------------------------------------------- #
def test_restore_never_overwrites_the_running_addon(wiz, monkeypatch, tmp_path):
    home, rep = _prep_restore(wiz, monkeypatch, tmp_path)
    self_rel = "addons/script.ezmaintenanceplusplus/addon.xml"

    installed = home / self_rel
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text('<addon version="2026.07.21.4"/>')

    src = _make_valid_zip(
        tmp_path / "kodi_backup_202607161200.zip",
        [
            ("userdata/guisettings.xml", "<settings/>"),
            (self_rel, '<addon version="2026.07.01.1"/>'),
            ("addons/plugin.other/addon.xml", "<a/>"),
        ],
    )

    rep.result = wiz.restore(str(src), confirm=False)

    assert '2026.07.21.4' in installed.read_text(), (
        "CONTRACT VIOLATION: restore downgraded the running add-on to the "
        "archived copy; the box would boot the older EZM++ after restart"
    )
    # Other add-ons and userdata are unaffected by the self-skip.
    assert (home / "addons/plugin.other/addon.xml").is_file()
    assert (home / "userdata" / "guisettings.xml").is_file()
    assert not _skips_surfaced(rep, 1), (
        "a deliberate self-skip must not be reported as a skipped member; "
        "report was: %r" % _report_text(rep)
    )
