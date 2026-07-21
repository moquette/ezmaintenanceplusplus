"""Chokepoint lint: no caller may run the wipe engine without a keep-list.

Same shape and same reason as `test_no_raw_userdata_writer.py`: the rule is easy to
state, invisible in review, and the failure is silent until hardware. Rather than
pin the two call sites that exist today, this walks the AST and fails on any call to
`onetap._wipe(...)` that does not pass `keep_files` - positionally or by keyword.

Why the keep-list is load-bearing, 2026-07-21:

* `onetap._wipe` walks `special://home` and `os.remove`s everything not excluded,
  and Kodi holds a persistent CDatabase connection on every `userdata/Database/*.db`.
  Unlinking one under a live Kodi leaves it writing to an unlinked inode: the next
  write fails SQLITE_READONLY_DBMOVED, the rest fail SQLITE_MISUSE, and on Android
  the storm aborts the process. A SINGLE unlinked `Textures13.db` is what killed the
  office Fire TV that day.
* `wiz.py`'s restore wipe was calling `_wipe` with NO keep-list at all, so it also
  unlinked `Addons*.db` - the one database whose loss brings EZ Maintenance++ back
  DISABLED, which is the entire reason `keep_addon_db()` exists - and then kept Kodi
  alive for the whole zip extract. `default.py`'s Fresh Start passed the keep-list;
  the two call sites had silently diverged.

`_wipe_excludes()` now also carries "Database", which is the broader guard. This lint
covers the case that guard cannot: a future caller building its own exclude set.
"""

from __future__ import annotations

import ast
from pathlib import Path

ADDON_ROOT = Path(__file__).parent.parent / "script.ezmaintenanceplusplus"


def _wipe_calls(tree):
    """Every Call node that targets `_wipe` as an attribute (onetap._wipe, mod._wipe)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "_wipe":
            yield node


def test_every_wipe_call_passes_a_keep_list():
    offenders = []
    seen = 0
    for path in sorted(ADDON_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _wipe_calls(tree):
            seen += 1
            has_keyword = any(kw.arg == "keep_files" for kw in call.keywords)
            # _wipe(home, excludes, keep_files, ...) - the third positional.
            has_positional = len(call.args) >= 3
            if not (has_keyword or has_positional):
                offenders.append("%s:%d" % (path.relative_to(ADDON_ROOT), call.lineno))

    # Scope guard: if the engine is ever renamed this lint would pass by finding
    # nothing at all, which is exactly the false green it exists to prevent.
    assert seen >= 2, (
        "expected at least the Fresh Start and restore call sites; found %d. Did "
        "_wipe get renamed? Update this lint rather than letting it pass vacuously."
        % seen
    )
    assert offenders == [], (
        "these calls run the wipe engine with no keep-list, so they will unlink "
        "databases Kodi holds open and can abort the process: %s" % ", ".join(offenders)
    )


def test_restore_wipe_keeps_the_live_databases():
    """CALL-SITE gate. The unit tests for keep_live_databases() cannot see this wiring.

    QA mutation, 2026-07-21: reverting wiz.py's restore wipe to a bare _wipe_excludes()
    keep-list left the whole suite GREEN at the exact baseline, because every test that
    covers this behaviour passes the keep-list in by hand. The restore path is the one
    that CANNOT terminate - it holds Kodi up for the entire zip extract - so losing this
    wiring silently reinstates the SIGABRT for the longest possible window.
    """
    src = (ADDON_ROOT / "resources" / "lib" / "modules" / "wiz.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    calls = [c for c in _wipe_calls(tree)]
    assert len(calls) == 1, "expected exactly one restore wipe call site, got %d" % len(
        calls
    )
    keep_arg = calls[0].args[2] if len(calls[0].args) >= 3 else None
    assert keep_arg is not None, "restore wipe passes no keep-list at all"
    names = {
        n.func.attr
        for n in ast.walk(keep_arg)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "keep_live_databases" in names, (
        "restore's wipe must preserve userdata/Database - Kodi holds every one of those "
        "files open and stays up for the whole extract. Found: %s" % sorted(names)
    )
    assert "keep_addon_db" in names, (
        "restore's wipe must also keep Addons*.db, or this add-on comes back DISABLED. "
        "Found: %s" % sorted(names)
    )


def test_backup_walk_skips_the_texture_cache():
    """CALL-SITE gate. Deleting the _is_regenerable_cache_arc() call from CreateZip's
    walk also left the suite green at baseline: the predicate had a unit test, the
    wiring had nothing."""
    src = (ADDON_ROOT / "resources" / "lib" / "modules" / "wiz.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_is_regenerable_cache_arc" in called, (
        "CreateZip no longer consults _is_regenerable_cache_arc, so every backup "
        "archive carries Textures13.db again"
    )
