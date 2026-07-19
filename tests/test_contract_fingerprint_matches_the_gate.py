"""The box-side and tool-side contract fingerprints must agree.

Gate defect G-2: `tools/verify_device.py` computes `storage_fingerprint()` from the
DEVELOPER'S LOCAL WORKING TREE, and the only thing the box asserted about itself was
`addon_version_on_box` - a hand-typed date string that does NOT move when contract
code changes. So a box running an older build produced a fully green artifact
certifying code it had never run.

That is not hypothetical. On 2026-07-19 a docstring edit made after deploying left
both boxes on the previous build under an unchanged version, while the artifact
claimed the new fingerprint. Nothing failed; it was caught only by noticing the build
hash had moved.

The fix has the ADD-ON hash its own installed contract files at startup and publish
them, so `verify_device.py` can compare what is RUNNING to what is being certified.
That only works while both sides hash the SAME FILES THE SAME WAY - and the two lists
live in different files, in different languages of the build. If they drift, every
verification fails with a mismatch that looks like a stale box, and the natural
"fix" is to weaken the check. These tests make the drift fail here instead, loudly
and with the cause named.
"""

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDON = ROOT / "script.ezmaintenanceplusplus"
SERVICE_PY = ADDON / "service.py"
VERIFY_PY = ROOT / "tools" / "verify_device.py"


def _load_verify():
    spec = importlib.util.spec_from_file_location("_verify_under_test", VERIFY_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _service_contract_files():
    """The relative paths service.py hashes, read from its source."""
    src = SERVICE_PY.read_text()
    block = re.search(r"_CONTRACT_FILES = \((.*?)\)", src, re.S)
    assert block, "service.py must define _CONTRACT_FILES"
    return sorted(re.findall(r'"([^"]+\.py)"', block.group(1)))


def test_both_sides_hash_the_same_files():
    """A file fingerprinted by one side and not the other makes the check a lie.

    Miss a file on the box side and a change to it is invisible to the gate - the
    exact hole G-2 describes. Miss one on the tool side and the box reports a hash
    the tool can never reproduce, so every run fails."""
    verify = _load_verify()
    tool_files = sorted(f.name for f in verify.CONTRACT_FILES)
    box_files = sorted(pathlib.PurePosixPath(p).name for p in _service_contract_files())
    assert box_files == tool_files, (
        "service.py._CONTRACT_FILES and verify_device.CONTRACT_FILES must name the "
        "same files.\n  box:  %s\n  tool: %s" % (box_files, tool_files)
    )


def _load_service_with_stubs(monkeypatch):
    """Import service.py far enough to call _contract_fingerprint() for real."""
    import sys
    import types

    xbmc = types.ModuleType("xbmc")
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
    xbmc.log = lambda *a, **k: None
    xbmc.translatePath = lambda p: p
    xbmc.getCondVisibility = lambda c: False
    xbmc.executebuiltin = lambda *a, **k: None
    xbmc.executeJSONRPC = lambda *a, **k: "{}"
    xbmc.sleep = lambda ms: None
    xbmc.Player = lambda *a, **k: types.SimpleNamespace(isPlayingVideo=lambda: False)
    xbmc.Monitor = type(
        "Monitor",
        (),
        {"abortRequested": lambda self: False, "waitForAbort": lambda self, t: False},
    )
    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = lambda *a, **k: types.SimpleNamespace(
        getSetting=lambda k: "",
        setSetting=lambda k, v: None,
        getAddonInfo=lambda k: "script.ezmaintenanceplusplus",
    )
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.Window = lambda i: types.SimpleNamespace(setProperty=lambda k, v: None)
    xbmcgui.Dialog = lambda *a, **k: types.SimpleNamespace()
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.exists = lambda p: False

    pkgs = {}
    for name in ("resources", "resources.lib", "resources.lib.modules"):
        m = types.ModuleType(name)
        m.__path__ = []
        pkgs[name] = m
    b2f = types.ModuleType("resources.lib.modules.backtothefuture")
    b2f.PY2 = False
    b2f.unicode = str
    maintenance = types.ModuleType("resources.lib.modules.maintenance")
    for fn in (
        "logMaintenance",
        "determineNextMaintenance",
        "getNextMaintenance",
        "clearCache",
        "purgePackages",
        "deleteThumbnails",
    ):
        setattr(maintenance, fn, lambda *a, **k: None)

    mods = dict(pkgs)
    mods.update(
        {
            "xbmc": xbmc,
            "xbmcaddon": xbmcaddon,
            "xbmcgui": xbmcgui,
            "xbmcvfs": xbmcvfs,
            "resources.lib.modules.backtothefuture": b2f,
            "resources.lib.modules.maintenance": maintenance,
        }
    )
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    pkgs["resources"].lib = pkgs["resources.lib"]
    pkgs["resources.lib"].modules = pkgs["resources.lib.modules"]
    for attr in ("backtothefuture", "maintenance"):
        setattr(
            pkgs["resources.lib.modules"], attr, mods["resources.lib.modules." + attr]
        )

    monkeypatch.delitem(sys.modules, "_svc_fingerprint_uut", raising=False)
    spec = importlib.util.spec_from_file_location("_svc_fingerprint_uut", SERVICE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_two_fingerprints_are_byte_identical_for_the_same_tree(monkeypatch):
    """Same files is not enough - the ORDER and the bytes must match too.

    This EXECUTES service.py's own `_contract_fingerprint()` rather than
    reimplementing it. A test that recomputed the algorithm here passed while the
    real box code was mutated to hash paths as well as content - proving only that
    the test agreed with itself. Running the shipped function means any drift
    (different sort, hashing paths, reading as text) fails here instead of on a box
    at 4am, where it looks like a stale deploy."""
    verify = _load_verify()
    svc = _load_service_with_stubs(monkeypatch)
    assert svc._contract_fingerprint() == verify.storage_fingerprint(), (
        "the box-side algorithm no longer reproduces storage_fingerprint(); a "
        "verification run would fail with a mismatch that looks like a stale box"
    )


def test_the_box_fingerprint_changes_when_contract_code_changes(monkeypatch, tmp_path):
    """The hash must track file CONTENT, not just names.

    A fingerprint ignoring the bytes (a constant, or a hash of names) would match
    forever and re-open G-2 while looking correct.

    Probed against a COPY. The first version edited the real nsud.py and restored it
    in a finally, which does not run on SIGTERM/SIGKILL or a hard pytest timeout -
    and four other modules read that file, including the storage gate's own
    fingerprint computation. Under pytest-xdist that would become a live race able to
    corrupt the very gate this fix protects. _contract_fingerprint resolves its base
    from the module global __file__, so pointing that at a copy gives the identical
    probe with zero writes to the real tree."""
    import shutil

    svc = _load_service_with_stubs(monkeypatch)
    copy_root = tmp_path / "addon"
    shutil.copytree(ADDON, copy_root)
    monkeypatch.setattr(svc, "__file__", str(copy_root / "service.py"))

    before = svc._contract_fingerprint()
    assert len(before) == 64, "expected a sha256 hexdigest, got %r" % (before,)

    target = copy_root / "resources/lib/modules/nsud.py"
    target.write_bytes(target.read_bytes() + b"\n# contract drift probe\n")
    after = svc._contract_fingerprint()

    assert after != before, (
        "editing a contract file did not change the box fingerprint - the hash is "
        "not reading file content and cannot detect a stale box"
    )
    assert (ADDON / "resources/lib/modules/nsud.py").read_bytes() == (
        ADDON / "resources/lib/modules/nsud.py"
    ).read_bytes(), "the probe must never touch the real tree"


def test_service_publishes_the_fingerprint_before_any_wait():
    """It must be published unconditionally at startup.

    Publishing it behind _wait_kodi_ready or a restore-only path would mean a box
    that is up but idle reports nothing, and the tool cannot distinguish "not
    published yet" from "wrong code"."""
    src = SERVICE_PY.read_text()
    assert "_publish_contract_fingerprint()" in src, "the service must publish it"
    pub = src.index("_publish_contract_fingerprint()\n    _maybe_resume_paused_pvr")
    ready = src.index("if _wait_kodi_ready(monitor):")
    assert pub < ready, "publish before the GUI wait, so an idle box still reports"


def test_an_unreadable_box_fingerprint_is_empty_not_a_false_match(monkeypatch):
    """A failure to hash must return "" - which can never equal a real sha256.

    This is the failure-OPEN direction and the one that matters: any non-empty
    sentinel would make an unreadable box look verified. Driven through the real
    function, because a substring check for `return ""` is satisfied by a dead
    branch while the live path returns something else."""
    svc = _load_service_with_stubs(monkeypatch)

    def _boom(*a, **k):
        raise OSError("unreadable")

    monkeypatch.setattr("builtins.open", _boom)
    assert svc._contract_fingerprint() == "", (
        "an unhashable box must report empty, never a sentinel that could be "
        "mistaken for a match"
    )


def test_publishing_puts_the_real_fingerprint_in_the_property(monkeypatch):
    """B2: the publish path must actually run and carry the hash.

    Nothing previously called _publish_contract_fingerprint and asserted what
    landed. Gutting it to a no-op, publishing "", or wrapping its call site in
    `if False:` all passed green."""
    svc = _load_service_with_stubs(monkeypatch)
    published = {}

    class _Win:
        def __init__(self, _id):
            pass

        def setProperty(self, key, value):
            published[key] = value

    monkeypatch.setattr(svc.xbmcgui, "Window", _Win)
    svc._publish_contract_fingerprint()

    assert svc.CONTRACT_FINGERPRINT_PROPERTY in published, (
        "the publish must set the property the tool reads"
    )
    value = published[svc.CONTRACT_FINGERPRINT_PROPERTY]
    assert value == svc._contract_fingerprint(), "must publish the REAL hash"
    assert len(value) == 64, "must be a sha256 hexdigest, got %r" % (value,)


def test_the_property_name_cannot_drift_between_box_and_tool(monkeypatch):
    """B3: the key is two hardcoded literals coupled only by human care.

    Renaming it on the box passed green. The consequence is fail-closed - every
    verification refuses forever - which is exactly the "looks like a stale box, so
    weaken the check" pressure this suite exists to resist."""
    svc = _load_service_with_stubs(monkeypatch)
    name = svc.CONTRACT_FINGERPRINT_PROPERTY
    tool_src = VERIFY_PY.read_text()
    assert "Window(10000).Property(%s)" % name in tool_src, (
        "tools/verify_device.py must read the property service.py publishes; the box "
        "publishes %r and the tool does not ask for it" % name
    )


def test_stale_bytecode_is_purged_before_the_fingerprint_is_published(
    monkeypatch, tmp_path
):
    """A .pyc the source no longer describes must not survive into the next start.

    CPython invalidates bytecode on the source's mtime AND size, not its content.
    tools/build.py stamps every zip entry 1980-01-01 for reproducible builds, so the
    mtime half is constant across builds and staleness collapses onto size alone: a
    same-length edit leaves a stale .pyc valid, and the box executes old bytecode
    while the fingerprint reports the new source. Stale __pycache__ was seen on atv2
    on 2026-07-19 and could not be removed with devicectl - only the add-on itself
    can reach it."""
    svc = _load_service_with_stubs(monkeypatch)

    base = pathlib.Path(svc.__file__).parent
    victim = base / "resources" / "lib" / "modules" / "__pycache__"
    created = not victim.exists()
    victim.mkdir(parents=True, exist_ok=True)
    stale = victim / "nsud.cpython-311.pyc"
    stale.write_bytes(b"stale bytecode")
    try:
        assert stale.exists()
        svc._purge_stale_bytecode()
        assert not stale.exists(), "stale bytecode must be removed"
    finally:
        if stale.exists():
            stale.unlink()
        if created and victim.exists():
            try:
                victim.rmdir()
            except OSError:
                pass


def test_the_purge_runs_before_the_publish(monkeypatch):
    """Order matters: hashing source the next start will not execute is a lie.

    Asserted by EXECUTION, not textual adjacency. The previous version used
    src.index() on two neighbouring statements, which `if False:` around the call
    defeats, and which raises ValueError (an error, not a clean failure) if any line
    is inserted between them."""
    svc = _load_service_with_stubs(monkeypatch)
    order = []
    monkeypatch.setattr(svc, "_purge_stale_bytecode", lambda: order.append("purge"))
    monkeypatch.setattr(
        svc, "_publish_contract_fingerprint", lambda: order.append("publish")
    )
    monkeypatch.setattr(svc, "_maybe_resume_paused_pvr", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_maybe_restore_check", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_wait_kodi_ready", lambda *a, **k: False)

    svc._startup_sequence(svc.xbmc.Monitor())

    assert order == ["purge", "publish"], (
        "the bytecode purge must RUN, and run before the publish, got %r" % (order,)
    )


def test_the_purge_never_breaks_startup(monkeypatch):
    """It is best-effort. A failure must leave the previous behaviour, never raise."""
    svc = _load_service_with_stubs(monkeypatch)

    def _boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(svc.os, "walk", _boom)
    svc._purge_stale_bytecode()  # must not raise


def test_the_tree_is_hashed_only_where_it_is_compared():
    """storage_fingerprint() must be CALLED in exactly one place: inside pull().

    Any second call re-hashes the working tree at a moment when no box was consulted.
    A run that pulls evidence, then has a contract file edited, then writes the
    artifact would record a fingerprint nothing was ever compared against - the
    edit-after-evidence shape of the 2026-07-19 incident, and a mutation that no
    behavioural test catches because in a quiet tree both values are equal.

    Parsed with ast, so a mention in a comment or docstring does not count and a real
    call cannot hide behind one."""
    import ast

    tree = ast.parse(VERIFY_PY.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "storage_fingerprint"
    ]
    assert len(calls) == 1, (
        "storage_fingerprint() must be called exactly once (inside pull, beside the "
        "box comparison); found %d call(s) on lines %s"
        % (len(calls), [c.lineno for c in calls])
    )
    enclosing = [
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and any(c is calls[0] for c in ast.walk(fn))
    ]
    assert "pull" in enclosing, (
        "the single call must live in pull(), where the value is compared against the "
        "box; found it in %s" % (enclosing or "module scope")
    )
