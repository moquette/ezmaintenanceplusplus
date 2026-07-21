"""One-Tap Restore was removed in v2026.07.21.x; only its wipe engine remains in
onetap.py. These gates assert the user-facing feature is fully gone and cannot be
reached, while the engine symbols Fresh Start / wiz / restorecheck depend on stay
exported."""

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDON = ROOT / "script.ezmaintenanceplusplus"
DEFAULT_PY = ADDON / "default.py"
ONETAP_PY = ADDON / "resources/lib/modules/onetap.py"
SETTINGS_XML = ADDON / "resources/settings.xml"

# The removed feature's surface: the public entry points AND their private plumbing, so
# a PARTIAL resurrection (helpers back without the menu) is caught too.
_FEATURE_SYMBOLS = (
    # public
    "menu",
    "pick",
    "apply",
    "verify",
    "verify_pin",
    "get_pin",
    "save_pin",
    "clear_pin",
    "all_pins",
    "is_set",
    "label_for",
    "rename",
    "remove",
    # private plumbing
    "_get",
    "_set",
    "fmt_size",
    "basename",
    "_size_from_meta",
    "_pick_vfs",
    "_pick_dropbox",
    "_ask_type",
    "_keep_or",
    "_signed_in",
    "_setting_global",
    "_verify_vfs",
    "_verify_dropbox",
    "_stage",
    "_cleanup",
    "_pin_actions",
)

# The wipe engine that outlived the feature (used by default.py FRESHSTART, wiz.py,
# restorecheck.py). These MUST remain.
_ENGINE_SYMBOLS = (
    "_wipe",
    "_wipe_excludes",
    "_wipe_nsud_keys",
    "_is_tvos",
    "_nsud_userdata_rels",
    "keep_addon_db",
    "keep_source_files",
    "repository_addon_names",
    "infer_type",
)


def _addon_py_files():
    return [p for p in ADDON.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_onetap_restore_menu_tile():
    assert "One-Tap Restore" not in DEFAULT_PY.read_text(), (
        "the One-Tap Restore main-menu tile is still present"
    )


def test_no_onetap_menu_or_dispatch():
    src = DEFAULT_PY.read_text()
    assert "onetap_menu" not in src, "the onetap_menu route is still wired"
    assert '_script_arg == "onetap"' not in src, "the onetap RunScript dispatch remains"


def test_no_feature_symbols_referenced_anywhere():
    pat = re.compile(r"\bonetap\.(%s)\b" % "|".join(_FEATURE_SYMBOLS))
    offenders = []
    for p in _addon_py_files():
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if pat.search(line):
                offenders.append((p.name, i, line.strip()))
    assert not offenders, f"references to removed One-Tap feature: {offenders}"


def test_onetap_module_defines_no_feature_functions():
    defined = {
        n.name
        for n in ast.walk(ast.parse(ONETAP_PY.read_text()))
        if isinstance(n, ast.FunctionDef)
    }
    leaked = sorted(set(_FEATURE_SYMBOLS) & defined)
    assert not leaked, f"onetap.py still defines removed feature functions: {leaked}"


def test_onetap_still_exports_the_wipe_engine():
    defined = {
        n.name
        for n in ast.walk(ast.parse(ONETAP_PY.read_text()))
        if isinstance(n, ast.FunctionDef)
    }
    missing = [s for s in _ENGINE_SYMBOLS if s not in defined]
    assert not missing, f"wipe engine lost symbols: {missing}"


def test_no_pin_settings_remain():
    assert not re.search(r'id="pin\d+_', SETTINGS_XML.read_text()), (
        "One-Tap pin storage keys still present in settings.xml"
    )
