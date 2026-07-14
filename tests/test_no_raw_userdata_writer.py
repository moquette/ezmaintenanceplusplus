"""CHOKEPOINT LINT: nobody writes a Kodi-read userdata XML behind nsud's back.

WHY THIS EXISTS
---------------
2026-07-14. An EZ Maintenance++ restore destroyed the owner's customized Apple TV menu.
The root cause was a storage rule violated in one function. We fixed that function, wrote
a two-layer tvOS fake, and added regression tests.

Then an adversarial review found the SAME CLASS OF BUG, still shipping, in a function
nobody had thought to test: `boxsetup._write_weather_settings` wrote
`addon_data/weather.multi/settings.xml` with a plain `open(..., "w")` and never called
`nsud.persist_one` - while its sibling `_add_sources`, ninety lines earlier in the SAME
FILE, did exactly that and carried a comment explaining why. There was no test file for
`boxsetup.py` at all.

That is the lesson: **a test only protects the code someone remembered to test.** A lint
protects the code nobody thought about. This file is the chokepoint.

THE RULE
--------
On Apple TV, Kodi reads certain userdata XML through its OWN VFS, which checks
NSUserDefaults BEFORE the disk file (CTVOSFile::Exists/Open, TVOSFile.cpp:70-122). A key
SHADOWS the disk. So a plain POSIX write to such a file can be silently invisible to Kodi
forever - the write "succeeds", the setting never applies, and no error is raised.

`nsud.persist_one()` is the ONLY sanctioned way to land such a write. It decides what may
be vectored (`_should_vector`), writes THROUGH the VFS, reads back to confirm, and only
then drops the redundant POSIX copy. It is a no-op on Fire TV / desktop.

=> Any function that writes a userdata/addon_data XML MUST route through nsud.

WHAT THIS DOES NOT DO
---------------------
This is an AST check, not a proof. It resolves `open(...)`/`xbmcvfs.File(...)` writes and
looks for an nsud call in the same function. It can be defeated by enough indirection
(handing the path to a helper in another module, rebinding `open`, building the mode
string at runtime). It is a guardrail against the accident that actually happened twice,
not a security boundary. If you find yourself routing around it, that IS the review.
"""

import ast
import pathlib

import pytest

ADDON = pathlib.Path(__file__).resolve().parents[1] / "script.ezmaintenanceplusplus"

# nsud.py IS the chokepoint - it is the one module allowed to do raw I/O here.
CHOKEPOINT = {"nsud.py"}

# Signals that a function is dealing with a file Kodi reads through its VFS.
USERDATA_HINTS = (
    "special://profile",
    "special://masterprofile",
    "special://userdata",
    "userdata",
    "addon_data",
)

# The sanctioned exits.
NSUD_CALLS = {"persist_one", "rewrite_userdata_xml"}

# Deliberate exemptions. Each MUST carry a reason. Do not add to this list to silence a
# finding - a finding here means a file Kodi reads may be silently shadowed on Apple TV.
ALLOWLIST = {
    # wiz.FIX_SPECIAL rewrites absolute paths embedded INSIDE userdata xml content before
    # a backup, gated behind the legacy BackupFixSpecialHome setting (default off). It is
    # a pre-backup content rewrite of files it does not own, not a settings write, and it
    # runs on the local box's own copies. Flagged 2026-07-14 as a genuine latent tvOS
    # shadow hazard (a stale key would hide the "fixed" bytes from Kodi). Left as-is
    # because the setting is off on the whole fleet and the correct fix is to retire
    # FIX_SPECIAL entirely; tracked, not silently blessed.
    ("wiz.py", "FIX_SPECIAL"),
}


def _py_files():
    return sorted(
        p
        for p in ADDON.rglob("*.py")
        if p.name not in CHOKEPOINT and "packages" not in p.parts
    )


def _is_write_call(node):
    """open(..., 'w'|'wb') or xbmcvfs.File(..., 'w')."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = (
        fn.id
        if isinstance(fn, ast.Name)
        else (fn.attr if isinstance(fn, ast.Attribute) else "")
    )
    if name not in ("open", "File"):
        return False
    for arg in list(node.args[1:]) + [
        k.value for k in node.keywords if k.arg == "mode"
    ]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if "w" in arg.value or "a" in arg.value:
                return True
    return False


def _mentions_userdata(fnode):
    for n in ast.walk(fnode):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            low = n.value.lower()
            if any(h in low for h in USERDATA_HINTS):
                return True
        # a call to a *_settings_path()/_weather_settings_path() style helper
        if isinstance(n, ast.Call):
            fn = n.func
            nm = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if nm.endswith("_path") and (
                "settings" in nm or "userdata" in nm or "profile" in nm
            ):
                return True
    return False


def _calls_nsud(fnode):
    for n in ast.walk(fnode):
        if isinstance(n, ast.Call):
            fn = n.func
            nm = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if nm in NSUD_CALLS:
                return True
    return False


def _offenders():
    bad = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fnode in ast.walk(tree):
            if not isinstance(fnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (path.name, fnode.name) in ALLOWLIST:
                continue
            writes = any(_is_write_call(n) for n in ast.walk(fnode))
            if writes and _mentions_userdata(fnode) and not _calls_nsud(fnode):
                bad.append((path.name, fnode.name, fnode.lineno))
    return bad


def test_no_module_writes_userdata_xml_behind_nsud():
    """A raw write to a Kodi-read userdata file, with no nsud.persist_one, is a bug.

    This exact check, had it existed on 2026-07-13, would have failed on
    boxsetup._write_weather_settings before it ever reached a box.
    """
    offenders = _offenders()
    assert not offenders, (
        "These functions write a userdata/addon_data file with plain POSIX/VFS I/O and "
        "never route through nsud. On Apple TV a stale NSUserDefaults key SHADOWS the "
        "disk file, so Kodi may never see these bytes and no error is raised:\n"
        + "\n".join("  %s::%s (line %d)" % o for o in offenders)
        + "\n\nFix: call nsud.persist_one('<userdata-relative path>', log=...) after the "
        "write, as boxsetup.add_media_sources does. If the file is an add-on's PRIVATE data "
        "that only IT reads with plain open(), persist_one already leaves it on disk - "
        "call it anyway and let it decide."
    )


def test_the_known_good_pattern_is_present():
    """Guard the lint itself: add_media_sources is the reference implementation.

    If someone removes its nsud.persist_one call, the lint above must catch it. If this
    test ever fails, the lint's detection is broken, not boxsetup.
    """
    src = (ADDON / "resources/lib/modules/boxsetup.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("add_media_sources", "_write_weather_settings"):
        assert name in fns, "%s vanished - update this lint" % name
        assert _is_write_call_in(fns[name]), (
            "%s no longer writes - update this lint" % name
        )
        assert _calls_nsud(fns[name]), (
            "%s writes a userdata file but no longer calls nsud - this is the 2026-07-14 "
            "bug class returning" % name
        )


def _is_write_call_in(fnode):
    return any(_is_write_call(n) for n in ast.walk(fnode))


@pytest.mark.parametrize("mod,fn", sorted(ALLOWLIST))
def test_allowlist_entries_still_exist(mod, fn):
    """A stale allowlist entry silently widens the hole. Fail when the code is gone."""
    hits = [p for p in ADDON.rglob(mod)]
    assert hits, "allowlisted module %s no longer exists - drop it from ALLOWLIST" % mod
    tree = ast.parse(hits[0].read_text(encoding="utf-8"))
    names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert fn in names, (
        "%s::%s is allowlisted but no longer exists - drop it from ALLOWLIST"
        % (mod, fn)
    )
