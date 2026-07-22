"""CHOKEPOINT LINT: no ListItem carries a property only OUR skin can read.

WHY THIS EXISTS
---------------
2026-07-22. The Backup/Restore menu shipped its rows with
`setProperty("ezm.footer", <path>)`, and skin.estuary7 1.0.77 shipped a patched
select dialog that rendered it. On stock Estuary - the skin EZ Maintenance++'s
own Fresh Start REQUIRES, because it is the only one that survives the wipe -
the paths were simply invisible. No error, no blank line, no clue. One feature
needed two artifacts, released from two repos, to be visible at all, and the
400KB add-on always reaches a box long before the 21MB skin.

The owner's ruling: "ESTUARY DOES NOT HAVE A BACKUP FEATURE NOR SHOULD IT BE
CONTROLLING EZM. There should be no co-dependency between addons."

WHY A LINT AND NOT A TEST
-------------------------
A menu-level test only covers the menu someone remembered to test. This is an
AST check over the whole add-on: any ListItem property key that Kodi core does
not define is a private contract with some skin, and a private contract with a
skin is the defect above. The sibling chokepoint is
tests/test_no_raw_userdata_writer.py; same shape, same reasoning.

WHAT THIS DOES NOT DO
---------------------
It resolves literal `.setProperty("key", ...)` calls on ListItem-shaped
receivers. It cannot follow a key built at runtime or handed through a helper in
another module. It is a guardrail against the accident that actually happened,
not a security boundary. Window properties are a different mechanism entirely
(the add-on talks to ITSELF across processes with those) and are out of scope:
this file lints ListItem only.
"""

from __future__ import annotations

import ast
import pathlib

ADDON = pathlib.Path(__file__).resolve().parents[1] / "script.ezmaintenanceplusplus"

# Keys Kodi core itself defines for a ListItem. Anything outside this set is a
# contract with a particular skin's XML.
#
# Fanart_Image is core: Kodi maps it to the item's fanart in plugin listings, and
# default.py has used it on every menu row since the fork. It is not a skin
# invention, which is exactly the distinction this lint draws.
CORE_LISTITEM_PROPERTIES = {
    "fanart_image",
    "startoffset",
    "resumetime",
    "totaltime",
    "isplayable",
    "inputstream",
    "specialsort",
}


def _window_names(tree):
    """Local names bound to a Window(...), e.g. `win = xbmcgui.Window(10000)`.

    Both spellings occur in this add-on and only the direct-call one is obvious;
    missing the bound-variable form made this lint's first run flag
    maintenance.py's own boot-time property as a skin contract, which it is not."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        f = node.value.func
        if getattr(f, "attr", getattr(f, "id", "")) != "Window":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _listitem_property_keys(tree):
    """[(lineno, key)] for every literal ListItem .setProperty(...) call.

    Window properties are excluded: they are how this add-on's own processes talk
    to each other (service -> plugin), they are never read by a skin, and the
    boundary this file polices is add-on -> skin."""
    windows = _window_names(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "setProperty":
            continue
        recv = func.value
        if isinstance(recv, ast.Call):
            rf = recv.func
            if getattr(rf, "attr", getattr(rf, "id", "")) == "Window":
                continue
        if isinstance(recv, ast.Name) and recv.id in windows:
            continue
        if isinstance(recv, ast.Attribute) and recv.attr in ("Window", "window"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            key = node.args[0].value
            if isinstance(key, str):
                out.append((node.lineno, key))
    return out


def test_no_listitem_carries_a_skin_private_property():
    """A ListItem property Kodi core does not define can only be rendered by a
    skin we also control. That is the co-dependency this project has ruled out:
    the add-on's UX must work on stock Estuary, on our skin, and on anyone
    else's, with no version of any skin required."""
    offenders = []
    skipped = []
    for path in sorted(ADDON.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            # A file this cannot parse drops silently OUT of the chokepoint, which
            # is how a lint quietly stops covering the thing it was written for.
            # Collected and asserted below rather than swallowed.
            skipped.append(path.name)
            continue
        for lineno, key in _listitem_property_keys(tree):
            if key.lower() not in CORE_LISTITEM_PROPERTIES:
                offenders.append((path.name, lineno, key))

    assert not skipped, (
        "these files could not be parsed, so they are NOT covered by this "
        "chokepoint: %s" % ", ".join(skipped)
    )
    assert not offenders, (
        "These ListItem properties are not defined by Kodi core, so only a skin "
        "that knows this add-on exists can render them:\n"
        + "\n".join("  %s:%d  setProperty(%r, ...)" % o for o in offenders)
        + "\n\nFold the text into the ROW LABEL instead - every skin draws "
        "ListItem.Label. See _menu_rows in default.py.\n"
        "Do NOT reach for Dialog.select(useDetails=True) and label2: it was "
        "tried on the bench on 2026-07-22 and rejected, because the detailed "
        "view reserves a thumbnail column and Kodi fills an artless row with "
        "DefaultAddonMore.png - a column of + glyphs down the menu - and giving "
        "the rows art to hide that is decoration nobody asked for."
    )


def test_the_lint_can_actually_fail():
    """Guard the lint itself against the exact code that shipped the defect.

    A chokepoint that cannot fail is decoration. This feeds it the real
    2026.07.22.0 line and requires a hit - and requires the core-key path to stay
    silent, so the lint is not simply flagging everything."""
    bad = ast.parse(
        'item = xbmcgui.ListItem("Backup")\nitem.setProperty("ezm.footer", p)\n'
    )
    keys = [k for _, k in _listitem_property_keys(bad)]
    assert keys == ["ezm.footer"], keys
    assert keys[0].lower() not in CORE_LISTITEM_PROPERTIES

    ok = ast.parse('li.setProperty("Fanart_Image", art)\n')
    assert [k.lower() for _, k in _listitem_property_keys(ok)] == ["fanart_image"]
    assert "fanart_image" in CORE_LISTITEM_PROPERTIES

    # A Window property is the add-on talking to itself; it must NOT be flagged,
    # in EITHER spelling. Both occur in this add-on.
    win = ast.parse('xbmcgui.Window(10000).setProperty("ezm_restore_verdict", "1")\n')
    assert _listitem_property_keys(win) == []

    bound = ast.parse(
        "win = xbmcgui.Window(10000)\n"
        'win.setProperty("ezmaintenance.nextMaintenanceTime", str(t1))\n'
    )
    assert _listitem_property_keys(bound) == []
