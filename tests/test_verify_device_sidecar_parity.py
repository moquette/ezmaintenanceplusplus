# -*- coding: utf-8 -*-
"""verify_device's sidecar rule must agree with nsud's, case for case.

WHY A COPY EXISTS AT ALL
------------------------
`nsud._is_skin_menu_sidecar` is the single definition, and
`restorecheck.duplicate_listing_hits` IMPORTS it. `tools/verify_device.py`
cannot: it runs on a workstation, and nsud imports `xbmcvfs` at module scope,
which only exists inside Kodi. So the tool carries a mirror.

A mirror is exactly how this defect class starts. The add-on's own probe and the
purge disagreed about what a sidecar was, so the purge kept 23 keys while the
probe reported them as "restored file(s) shadowed by a stale key" - and atv2 ended
every restore telling the owner her box needed attention when it did not
(verification/2026.07.19.4.json: clean_single_layer false, 23 *.DATA.xml names,
while the single-layer Fire TV was clean).

This test makes the mirror safe: nsud is imported under a fake xbmcvfs and the two
predicates are compared over a table that includes every boundary either one could
get wrong. If someone edits one rule, this fails.

THE BOUNDARY IS THE POINT: `script.skinshortcuts/settings.xml` is
Kodi-framework-owned and MUST still count as a duplicate; a directory-wide
suppression would blind the only check that detects real two-layer shadowing.
"""

import importlib
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = ROOT / "script.ezmaintenanceplusplus" / "resources" / "lib" / "modules"
TOOLS = ROOT / "tools"

# Every case either implementation could plausibly disagree on.
CASES = [
    # the skin's deliberate sidecars - master profile
    "addon_data/script.skinshortcuts/mainmenu.DATA.xml",
    "addon_data/script.skinshortcuts/1.DATA.xml",
    "addon_data/script.skinshortcuts/movies-0.DATA.xml",
    # case-insensitivity of the suffix
    "addon_data/script.skinshortcuts/mainmenu.data.xml",
    "addon_data/script.skinshortcuts/MAINMENU.DATA.XML",
    # per-profile form
    "profiles/kids/addon_data/script.skinshortcuts/mainmenu.DATA.xml",
    "profiles/Guest User/addon_data/script.skinshortcuts/tv.DATA.xml",
    # NOT sidecars: framework-owned files in the same directory
    "addon_data/script.skinshortcuts/settings.xml",
    "addon_data/script.skinshortcuts/skin.estuary7.properties",
    "addon_data/script.skinshortcuts/skin.estuary7.hash",
    # NOT sidecars: right suffix, wrong owner
    "addon_data/plugin.video.example/mainmenu.DATA.xml",
    "addon_data/pvr.iptvsimple/instance-settings-1.xml",
    "addon_data/script.skinshortcutsEVIL/mainmenu.DATA.xml",
    # NOT sidecars: top-level userdata
    "guisettings.xml",
    "sources.xml",
    # degenerate input
    "",
    "/",
    "addon_data/script.skinshortcuts/",
    # leading-slash and backslash normalisation
    "/addon_data/script.skinshortcuts/mainmenu.DATA.xml",
    "addon_data\\script.skinshortcuts\\mainmenu.DATA.xml",
    # a profile path that is too short to be the per-profile form
    "profiles/mainmenu.DATA.xml",
]


@pytest.fixture(scope="module")
def preds():
    """nsud's predicate (under a fake xbmcvfs) and verify_device's mirror."""
    saved = {k: sys.modules.get(k) for k in ("xbmc", "xbmcvfs")}
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p
    xbmc = types.ModuleType("xbmc")
    xbmc.log = lambda *a, **k: None
    xbmc.LOGINFO = 1
    xbmc.getCondVisibility = lambda cond: False
    sys.modules["xbmcvfs"] = xbmcvfs
    sys.modules["xbmc"] = xbmc
    sys.path.insert(0, str(MODULES))
    sys.path.insert(0, str(TOOLS))
    try:
        nsud = importlib.import_module("nsud")
        verify_device = importlib.import_module("verify_device")
        yield nsud._is_skin_menu_sidecar, verify_device.is_skin_menu_sidecar
    finally:
        for p in (str(MODULES), str(TOOLS)):
            if p in sys.path:
                sys.path.remove(p)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.mark.parametrize("rel", CASES)
def test_verify_device_mirror_agrees_with_nsud(preds, rel):
    nsud_pred, tool_pred = preds
    assert tool_pred(rel) == nsud_pred(rel), (
        "verify_device.is_skin_menu_sidecar disagrees with "
        "nsud._is_skin_menu_sidecar on %r - the mirror has drifted" % rel
    )


def test_the_deliberate_sidecars_are_excluded(preds):
    """Sanity: the table above is not vacuously all-False."""
    _nsud_pred, tool_pred = preds
    assert tool_pred("addon_data/script.skinshortcuts/mainmenu.DATA.xml") is True


def test_settings_xml_is_never_treated_as_a_sidecar(preds):
    """The load-bearing boundary. A directory-wide suppression would pass every
    other case here and blind the probe to a real shadow."""
    _nsud_pred, tool_pred = preds
    assert tool_pred("addon_data/script.skinshortcuts/settings.xml") is False


def test_find_duplicates_excludes_sidecars_only_in_that_directory(preds):
    """The consumer: find_duplicates must drop the skin's sidecars and keep
    everything else, including settings.xml in the very same listing."""
    verify_device = sys.modules["verify_device"]
    ss = "special://profile/addon_data/script.skinshortcuts/"
    names = ["mainmenu.DATA.xml"] * 2 + ["settings.xml"] * 2 + ["tv.DATA.xml"] * 2
    assert verify_device.find_duplicates(names, ss) == ["settings.xml"]
    # With no directory context the raw behaviour is unchanged.
    assert verify_device.find_duplicates(names) == [
        "mainmenu.DATA.xml",
        "settings.xml",
        "tv.DATA.xml",
    ]
    # A .DATA.xml duplicated under a DIFFERENT add-on is a real split.
    other = "special://profile/addon_data/plugin.video.example/"
    assert verify_device.find_duplicates(["mainmenu.DATA.xml"] * 2, other) == [
        "mainmenu.DATA.xml"
    ]
