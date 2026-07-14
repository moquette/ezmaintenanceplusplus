"""Regression guard for the EZ Maintenance++ blank-native-settings bug.

Kodi resolves every settings.xml label/heading/option as a NUMERIC localized
string id (via g_localizeStrings); a plain-text value resolves to empty, so the
native add-on Settings dialog renders every label blank. The add-on shipped with
plain-text labels and no language file for months, misdiagnosed in-code as an
"unfixable Kodi engine bug" - it was not (a control add-on renders fine on the
same box). The fix: numeric ids + a resources/language strings.po.

This test mechanically prevents recurrence #6: it fails the build if any label is
plain-text again, if any id lacks a strings.po entry, if an id is outside the
add-on private band, or if the hidden One-Tap pin storage is lost.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ADDON_ROOT = REPO_ROOT / "script.ezmaintenanceplusplus"
SETTINGS_XML = ADDON_ROOT / "resources" / "settings.xml"
STRINGS_PO = (
    ADDON_ROOT / "resources" / "language" / "resource.language.en_gb" / "strings.po"
)
ADDON_XML = ADDON_ROOT / "addon.xml"

# Kodi maps 30000-30999 (and 32000-32999) to the add-on's OWN strings.po. An id
# below 30000 resolves to a Kodi CORE string and renders real-but-WRONG text -
# a worse failure than blank because it looks filled in. Pin the band.
ID_MIN, ID_MAX = 30000, 30999


def _root():
    return ET.parse(SETTINGS_XML).getroot()


def _label_refs():
    """Every settings.xml value Kodi resolves as a string id: category/group/
    setting `label`, `<option label=...>`, and control `<heading>`."""
    root = _root()
    refs = []  # (kind, raw_value)
    for tag in ("category", "group", "setting"):
        for el in root.iter(tag):
            lbl = el.get("label")
            if lbl is not None:
                refs.append((f"{tag}:{el.get('id')}", lbl))
    for opt in root.iter("option"):
        lbl = opt.get("label")
        if lbl is not None:
            refs.append(("option", lbl))
    for hd in root.iter("heading"):
        if hd.text and hd.text.strip():
            refs.append(("heading", hd.text.strip()))
    return refs


def _po_ids():
    """{id: msgid} parsed from strings.po; also returns the raw msgctxt list to
    detect duplicates."""
    text = STRINGS_PO.read_text(encoding="utf-8")
    entries = {}
    ctxts = []
    # msgctxt "#30000"\nmsgid "Foo"
    for m in re.finditer(r'msgctxt\s+"#(\d+)"\s*\nmsgid\s+"((?:[^"\\]|\\.)*)"', text):
        cid = int(m.group(1))
        ctxts.append(cid)
        entries[cid] = m.group(2)
    return entries, ctxts


def test_no_plaintext_labels_remain():
    """Every label/heading/option must be a NUMERIC id - a plain-text value is
    exactly the original bug (renders blank in the native dialog)."""
    offenders = [
        (kind, val) for kind, val in _label_refs() if not re.fullmatch(r"\d+", val)
    ]
    assert not offenders, f"plain-text (non-id) labels still present: {offenders}"


def test_every_settings_id_has_a_strings_entry():
    """A settings.xml id with no strings.po entry renders blank - the dangling-id
    guard."""
    po, _ = _po_ids()
    used = {int(v) for _, v in _label_refs() if re.fullmatch(r"\d+", v)}
    missing = sorted(i for i in used if i not in po)
    assert not missing, f"settings.xml ids missing from strings.po: {missing}"


def test_string_ids_in_addon_private_band():
    po, _ = _po_ids()
    used = {int(v) for _, v in _label_refs() if re.fullmatch(r"\d+", v)}
    out_of_band = sorted(i for i in (used | set(po)) if not (ID_MIN <= i <= ID_MAX))
    assert not out_of_band, (
        f"ids outside {ID_MIN}-{ID_MAX} (collide with core): {out_of_band}"
    )


def test_no_duplicate_or_empty_strings():
    po, ctxts = _po_ids()
    dupes = sorted({c for c in ctxts if ctxts.count(c) > 1})
    assert not dupes, f"duplicate msgctxt ids in strings.po: {dupes}"
    empty = sorted(cid for cid, msg in po.items() if not msg.strip())
    assert not empty, f"ids with empty msgid (still render blank): {empty}"


def test_onetap_category_removed_but_pins_preserved():
    """The empty One-Tap Restore tab is gone, but all 50 hidden pin storage keys
    must survive (removing them breaks onetap.py save/load silently)."""
    root = _root()
    cats = [c.get("id") for c in root.iter("category")]
    assert "onetap_restore" not in cats, (
        f"empty onetap_restore tab still present: {cats}"
    )

    pins = [
        s
        for s in root.iter("setting")
        if re.fullmatch(r"pin\d+_(name|kind|src|type|meta)", s.get("id", ""))
    ]
    assert len(pins) == 50, f"expected 50 pin storage keys, found {len(pins)}"
    # Every pin must stay hidden - a dropped <visible>false</visible> = 50 blank rows.
    for s in pins:
        vis = s.find("visible")
        assert vis is not None and vis.text == "false", (
            f"pin {s.get('id')} is not visible=false (would render a blank row)"
        )


def test_addon_language_declared():
    """The empty <language></language> was part of the root cause; ship it set."""
    root = ET.parse(ADDON_XML).getroot()
    langs = [e.text for e in root.iter("language")]
    assert langs and all(t and t.strip() for t in langs), f"<language> empty: {langs}"
