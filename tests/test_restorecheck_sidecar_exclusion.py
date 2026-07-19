# -*- coding: utf-8 -*-
"""restorecheck.duplicate_listing_hits must not read the skin's DELIBERATE
dual-layering as a shadowing defect.

THE DEFECT THESE PIN (reproduced 2026-07-19 from verification/2026.07.19.4.json):
skin.estuary7 1.0.66+ deliberately dual-layers every skinshortcuts *.DATA.xml on
tvOS - syncMenu re-registers a byte-identical NSUserDefaults key for each menu
file on every Home load, as the durability sidecar that lets the owner's custom
menu survive a Library/Caches purge. CTVOSDirectory::GetDirectory
(TVOSDirectory.cpp:48-106) lists the POSIX names and then appends every key
basename WITHOUT dedupe, so each sidecar is listed TWICE.

`nsud.purge_stale_keys` already understands this (`_is_skin_menu_sidecar`, and
the atv2 2026-07-17 "infinite war" it was written to end). `duplicate_listing_hits`
did NOT: it counted the same 23 deliberate duplicates as
"restored file(s) shadowed by a stale key", which `verify_restored_state` raises
as ATTENTION, which `wiz.py:797` reports as
"Restore Problem - this box needs attention", which triggers the auto-fix retry
at wiz.py:1704 (a wasted second extract) and then reports the problem anyway.
The owner opens the add-on as instructed and finds nothing that can act on it -
the purge correctly KEEPS all 23. Cry-wolf instance four.

The hardware evidence: atv2 recorded duplicate_listing/clean = false with 23
*.DATA.xml names; the Fire TV (single-layer) recorded clean = true.

THE BOUNDARY, which is the whole point:
  * *.DATA.xml under script.skinshortcuts (master AND profile-prefixed) = the
    skin's live sidecar. NOT a hit.
  * anything else duplicated across the two layers - including
    script.skinshortcuts/settings.xml, which is Kodi-framework-owned, and
    pvr.iptvsimple instance settings - IS a real shadow and MUST still hit.
A directory-wide `script.skinshortcuts/` suppression would satisfy the first
bullet and blind the probe to the second. test_settings_xml_* and
test_iptv_* exist to fail that shortcut.

The sidecar predicate is IMPORTED from nsud, never re-derived here: two
divergent copies of "what is a sidecar" is the exact bug class that produced
this defect (the purge knew, the probe did not).
"""

import importlib
import pathlib
import sys
import types

import pytest

ADDON_ROOT = (
    pathlib.Path(__file__).resolve().parent.parent / "script.ezmaintenanceplusplus"
)
MODULES = ADDON_ROOT / "resources" / "lib" / "modules"

SS_DIR = "special://profile/addon_data/script.skinshortcuts/"
IPTV_DIR = "special://profile/addon_data/pvr.iptvsimple/"
AD_DIR = "special://profile/addon_data/"

# The exact 23 names verification/2026.07.19.4.json recorded as duplicated on
# atv2. Hard-coded from the artifact so this test is anchored to observed
# hardware state, not to a shape someone imagined.
ATV2_SIDECARS = [
    "1.DATA.xml",
    "10134.DATA.xml",
    "15016.DATA.xml",
    "19020.DATA.xml",
    "19021.DATA.xml",
    "24001.DATA.xml",
    "427.DATA.xml",
    "8-0.DATA.xml",
    "8.DATA.xml",
    "mainmenu.DATA.xml",
    "movies-0.DATA.xml",
    "movies-1.DATA.xml",
    "movies.DATA.xml",
    "music-0.DATA.xml",
    "music-1.DATA.xml",
    "music.DATA.xml",
    "musicvideos.DATA.xml",
    "pictures.DATA.xml",
    "programs.DATA.xml",
    "radio.DATA.xml",
    "tvshows.DATA.xml",
    "tv.DATA.xml",
    "videos.DATA.xml",
]


@pytest.fixture()
def rc(monkeypatch):
    """restorecheck with fake xbmc/xbmcvfs, and nsud importable (the sidecar
    predicate must come from there)."""
    monkeypatch.syspath_prepend(str(ADDON_ROOT))
    monkeypatch.syspath_prepend(str(MODULES))
    for name in list(sys.modules):
        if name == "resources" or name.startswith("resources."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    logs = []
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 1
    xbmc.log = lambda msg, level=1: logs.append(msg)
    xbmc.getCondVisibility = lambda cond: "TVOS" in cond  # tvOS: the two-layer box
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs._listings = {}
    xbmcvfs.listdir = lambda d: xbmcvfs._listings.get(d, ([], []))
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.delete = lambda p: True
    monkeypatch.setitem(sys.modules, "xbmc", xbmc)
    monkeypatch.setitem(sys.modules, "xbmcvfs", xbmcvfs)
    monkeypatch.delitem(sys.modules, "nsud", raising=False)
    sys.modules.pop("resources.lib.modules.restorecheck", None)
    mod = importlib.reload(
        importlib.import_module("resources.lib.modules.restorecheck")
    )
    mod._test_logs = logs
    mod._test_xbmcvfs = xbmcvfs
    yield mod
    sys.modules.pop("resources.lib.modules.restorecheck", None)


def _dual_layer(rc, directory, names):
    """Seed CTVOSDirectory's no-dedupe merge: each name listed twice (the POSIX
    entry plus the appended NSUserDefaults key basename)."""
    rc._test_xbmcvfs._listings[directory] = ([], list(names) + list(names))


def _single_layer(rc, directory, names):
    rc._test_xbmcvfs._listings[directory] = ([], list(names))


# --------------------------------------------------------------------------- #
# The atv2 state: the skin's deliberate sidecars are NOT a defect.
# --------------------------------------------------------------------------- #
def test_atv2_sidecar_dual_layer_state_produces_zero_hits(rc):
    """The exact hardware state from verification/2026.07.19.4.json. Every one
    of the 23 is the skin's own durability sidecar, maintained live by syncMenu.
    Zero hits, therefore zero ATTENTION, therefore no false 'Restore Problem'
    and no wasted auto-fix retry."""
    _dual_layer(rc, SS_DIR, ATV2_SIDECARS)
    _single_layer(rc, AD_DIR, ["script.skinshortcuts", "pvr.iptvsimple"])
    _single_layer(rc, IPTV_DIR, ["settings.xml", "instance-settings-1.xml"])

    assert rc.duplicate_listing_hits() == [], (
        "the skin's deliberate dual-layering is not a shadowing defect"
    )


def test_atv2_state_verifies_clean_end_to_end(rc):
    """The composed verdict, not just the probe: this state must reach the
    owner as verified-clean, never as 'needs attention - open EZ Maintenance++'."""
    _dual_layer(rc, SS_DIR, ATV2_SIDECARS)
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, IPTV_DIR, [])

    attention, _detail = rc.verify_restored_state(leftovers=[], names=[], anchor="home")

    assert attention == [], attention


def test_profile_prefixed_sidecar_is_also_excluded(rc):
    """QA finding F1 parity: a secondary profile's menu sidecar is just as
    skin-maintained as the master profile's. The probe must agree with
    _is_skin_menu_sidecar, which already handles profiles/<name>/."""
    profile_dir = "special://profile/addon_data/script.skinshortcuts/"
    _dual_layer(rc, profile_dir, ["mainmenu.DATA.xml"])
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, IPTV_DIR, [])
    # and the predicate itself, on the profile-prefixed rel form
    from nsud import _is_skin_menu_sidecar

    assert _is_skin_menu_sidecar(
        "profiles/Kids/addon_data/script.skinshortcuts/mainmenu.DATA.xml"
    )
    assert rc.duplicate_listing_hits() == []


# --------------------------------------------------------------------------- #
# The boundary: everything that is NOT a sidecar must STILL be reported.
# A directory-wide script.skinshortcuts/ suppression passes the tests above and
# FAILS these two. That is their only job.
# --------------------------------------------------------------------------- #
def test_skinshortcuts_settings_xml_duplicate_still_hits(rc):
    """settings.xml under script.skinshortcuts is Kodi-framework-owned and
    in-scope for vectoring (_should_vector keeps it, and
    test_skinshortcuts_settings_xml_is_still_in_scope_not_a_sidecar pins that
    for the purge). Duplicated across layers it is a REAL shadow. The sidecar
    rule is a *.DATA.xml suffix rule, never a directory rule."""
    _dual_layer(rc, SS_DIR, ["settings.xml"])
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, IPTV_DIR, [])

    assert rc.duplicate_listing_hits() == [SS_DIR + "settings.xml"], (
        "a directory-wide script.skinshortcuts exclusion would blind this"
    )


def test_iptv_instance_settings_duplicate_still_hits(rc):
    """The genuine shadowing class the probe exists for: a restored IPTV
    instance file shadowed by a stale key. Must never be suppressed."""
    _dual_layer(rc, IPTV_DIR, ["instance-settings-2.xml"])
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, SS_DIR, [])

    assert rc.duplicate_listing_hits() == [IPTV_DIR + "instance-settings-2.xml"]


def test_mixed_state_reports_only_the_real_shadow(rc):
    """The realistic post-restore tvOS box: 23 deliberate sidecars plus ONE
    genuine IPTV shadow. Exactly one hit, and it is the IPTV file."""
    _dual_layer(rc, SS_DIR, ATV2_SIDECARS)
    _dual_layer(rc, IPTV_DIR, ["instance-settings-2.xml"])
    _single_layer(rc, AD_DIR, [])

    hits = rc.duplicate_listing_hits()

    assert hits == [IPTV_DIR + "instance-settings-2.xml"], hits


def test_non_data_xml_in_skinshortcuts_dir_still_hits(rc):
    """Belt and braces on the suffix rule: a non-.DATA.xml file duplicated in
    the skinshortcuts dir is not a sidecar and must be reported."""
    _dual_layer(rc, SS_DIR, ["mainmenu.properties"])
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, IPTV_DIR, [])

    assert rc.duplicate_listing_hits() == [SS_DIR + "mainmenu.properties"]


# --------------------------------------------------------------------------- #
# The shared predicate: restorecheck must IMPORT the rule, not restate it.
# Two divergent copies is what produced this defect in the first place.
# --------------------------------------------------------------------------- #
def test_sidecar_rule_is_imported_from_nsud_not_redefined(rc):
    """restorecheck must not carry its own sidecar definition. If it grows one,
    it will drift from nsud's - which is precisely how the purge came to keep
    the 23 keys while the probe kept alarming about them."""
    src = (MODULES / "restorecheck.py").read_text()
    assert "_is_skin_menu_sidecar" in src, (
        "restorecheck must reference nsud's sidecar predicate"
    )
    assert "def _is_skin_menu_sidecar" not in src, (
        "restorecheck must IMPORT the predicate from nsud, never re-derive it"
    )
    assert ".DATA.xml" not in src or "nsud" in src, (
        "no independently restated sidecar suffix rule"
    )


def test_predicate_IS_nsud_s_function_object(rc):
    """Identity, not just equivalence.

    The source grep above is satisfied by any re-derivation that merely AVOIDS
    the name `def _is_skin_menu_sidecar` (a lambda, a comprehension, an inlined
    suffix test), and such a copy passes every behavioural case below by
    coincidence while being free to drift on the next edit. Pin the object."""
    # Import by the SAME route restorecheck uses. `from nsud import ...` loads a
    # SECOND, distinct module object (MODULES is on sys.path as well as
    # ADDON_ROOT), whose functions would never be `is`-identical - a test
    # artifact, not a product difference.
    from resources.lib.modules import nsud

    assert rc._sidecar_predicate() is nsud._is_skin_menu_sidecar, (
        "restorecheck must hand back nsud's own function, not a look-alike"
    )


def test_data_xml_outside_skinshortcuts_still_hits(rc):
    """A `*.DATA.xml` under a DIFFERENT add-on is NOT the skin's sidecar.

    This is the behavioural teeth behind the import rule. nsud's predicate
    requires the `script.skinshortcuts/` prefix; any suffix-only re-derivation
    ("endswith .data.xml") answers True here and would silently suppress a real
    two-layer shadow belonging to some other add-on. Only the imported
    predicate gets this right, so this test fails for a re-derived copy even
    when the source grep cannot see it."""
    other = "special://profile/addon_data/plugin.video.example/"
    _dual_layer(rc, other, ["mainmenu.DATA.xml"])
    hits = rc.duplicate_listing_hits(dirs=(other,))
    assert hits == [other + "mainmenu.DATA.xml"], (
        "a .DATA.xml outside script.skinshortcuts is a real shadow and must hit"
    )


def test_probe_survives_nsud_import_failure(rc, monkeypatch):
    """restorecheck is documented to never raise. If nsud cannot be imported the
    probe must not explode into the restore path - a diagnostic that cannot run
    is not a restore failure."""
    _dual_layer(rc, SS_DIR, ATV2_SIDECARS)
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, IPTV_DIR, [])
    monkeypatch.setitem(sys.modules, "resources.lib.modules.nsud", None)

    attention, _detail = rc.verify_restored_state(leftovers=[], names=[], anchor="home")

    assert isinstance(attention, list)


def test_unavailable_predicate_fails_OPEN_not_closed(rc, monkeypatch):
    """THE DIRECTION OF THE DEGRADATION, which is the whole safety property.

    When the sidecar predicate cannot be obtained the probe must report EVERY
    duplicate (fail OPEN: noisy but blind to nothing), never suppress every
    duplicate (fail CLOSED: quiet and blind to real shadowing).

    Caught a live gap in this file's first draft: swapping
    ``is_sidecar is not None and ...`` for ``is_sidecar is None or ...`` inverts
    the degradation - if the nsud import ever breaks, the probe silently reports
    clean forever and the one defect it exists to catch goes undetected on every
    box. Nine tests passed through that mutation. This is the one that does not.
    """
    monkeypatch.setattr(rc, "_sidecar_predicate", lambda: None)
    _dual_layer(rc, SS_DIR, ["mainmenu.DATA.xml"])
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, IPTV_DIR, [])

    assert rc.duplicate_listing_hits() == [SS_DIR + "mainmenu.DATA.xml"], (
        "with no predicate the probe must over-report, never under-report"
    )


def test_unavailable_predicate_still_reports_a_real_shadow(rc, monkeypatch):
    """The consequence that actually matters: a genuine IPTV shadow must survive
    a predicate outage. Fail-closed would swallow it."""
    monkeypatch.setattr(rc, "_sidecar_predicate", lambda: None)
    _dual_layer(rc, IPTV_DIR, ["instance-settings-2.xml"])
    _single_layer(rc, AD_DIR, [])
    _single_layer(rc, SS_DIR, [])

    assert rc.duplicate_listing_hits() == [IPTV_DIR + "instance-settings-2.xml"]
