"""GAP 2: version-regression / release-existence gate (no live GitHub API dependency).

Exercises tools/check_release_consistency.check(), which is pure (no I/O) precisely so
this suite never talks to GitHub - every scenario below is a hand-built releases list,
in exactly the shape GET /repos/{repo}/releases returns (a list of dicts with
"tag_name" and "assets": [{"name": ...}, ...]).

The critical scenario is test_addon_ahead_of_latest_release_is_not_a_failure: the
release flow is "bump addon.xml + push, THEN run tools/release.sh to tag + publish",
so main legitimately carries an unreleased bump for that whole window. A gate that
failed on that alone would red every push in that window - which is exactly the
"naive gate" an architect flagged and this design deliberately avoids.
"""

import importlib.util
import pathlib
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "check_release_consistency.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_release_consistency", MODULE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


def _release(version, asset_ok=True):
    tag = f"v{version}"
    assets = (
        [{"name": f"script.ezmaintenanceplusplus-{version}.zip"}] if asset_ok else []
    )
    return {"tag_name": tag, "assets": assets}


def test_addon_ahead_of_latest_release_is_not_a_failure(mod):
    """The legitimate pending window: bumped + pushed, release.sh not run yet."""
    releases = [_release("2026.07.10.0")]
    ok, problems = mod.check("2026.07.14.1", releases)
    assert ok
    assert problems == []


def test_no_releases_at_all_is_not_a_failure(mod):
    """Nothing published yet (e.g. a brand-new repo) is not a regression."""
    ok, problems = mod.check("2026.07.14.1", [])
    assert ok
    assert problems == []


def test_own_version_released_with_matching_asset_is_clean(mod):
    releases = [_release("2026.07.14.1", asset_ok=True)]
    ok, problems = mod.check("2026.07.14.1", releases)
    assert ok
    assert problems == []


def test_older_releases_alongside_are_fine(mod):
    """A whole release history behind addon.xml must never trip the gate - only the
    single latest published version matters for the regression check."""
    releases = [
        _release("2026.07.01.0"),
        _release("2026.07.10.0"),
        _release("2026.07.13.2"),
    ]
    ok, problems = mod.check("2026.07.14.1", releases)
    assert ok
    assert problems == []


def test_regression_addon_behind_latest_release_fails(mod):
    releases = [_release("2026.07.14.5")]
    ok, problems = mod.check("2026.07.14.1", releases)
    assert not ok
    assert any("REGRESSION" in p for p in problems)


def test_contradiction_tag_exists_without_matching_asset_fails(mod):
    releases = [_release("2026.07.14.1", asset_ok=False)]
    ok, problems = mod.check("2026.07.14.1", releases)
    assert not ok
    assert any("CONTRADICTION" in p for p in problems)


def test_contradiction_tag_exists_with_wrong_asset_name_fails(mod):
    releases = [{"tag_name": "v2026.07.14.1", "assets": [{"name": "wrong-name.zip"}]}]
    ok, problems = mod.check("2026.07.14.1", releases)
    assert not ok
    assert any("CONTRADICTION" in p for p in problems)


def test_non_version_tags_are_ignored(mod):
    """A tag that isn't this repo's v<version> scheme (e.g. a stray experiment tag)
    must not be treated as a release to compare against."""
    releases = [{"tag_name": "some-random-tag", "assets": []}]
    ok, problems = mod.check("2026.07.14.1", releases)
    assert ok
    assert problems == []


def test_current_tree_is_actually_clean(mod):
    """Mirrors the real, currently-published state (addon.xml's own version, released
    with its expected asset) - not a live call, just the same shape a real GitHub API
    response has for today's actual state. Pins that the gate is green on this tree."""
    version = mod.read_addon_version()
    releases = [_release(version, asset_ok=True)]
    ok, problems = mod.check(version, releases)
    assert ok
    assert problems == []


def test_main_has_teeth_fails_on_contradiction_then_passes_once_fixed(mod, monkeypatch):
    """End-to-end: main() must exit 1 when the (mocked) API reflects a broken release,
    and exit 0 once the same tag carries the expected asset - proves this gate can
    actually fail, not just report."""
    monkeypatch.setattr(
        mod, "read_addon_version", lambda path=mod.ADDON_XML: "2026.07.14.1"
    )

    monkeypatch.setattr(
        mod,
        "fetch_releases",
        lambda repo=mod.REPO: [_release("2026.07.14.1", asset_ok=False)],
    )
    assert mod.main() == 1

    monkeypatch.setattr(
        mod,
        "fetch_releases",
        lambda repo=mod.REPO: [_release("2026.07.14.1", asset_ok=True)],
    )
    assert mod.main() == 0


def test_main_does_not_fail_on_network_error(mod, monkeypatch):
    """A network blip must never red CI - this gate detects regressions/contradictions
    in release state, not GitHub API reachability."""

    def _boom(repo=mod.REPO):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(mod, "fetch_releases", _boom)
    assert mod.main() == 0


def test_main_does_not_fail_on_malformed_api_response(mod, monkeypatch):
    """A non-JSON-list response (e.g. a GitHub API error body) must also degrade to a
    warning, never a false-positive gate failure."""

    def _bad(repo=mod.REPO):
        raise ValueError("not a list")

    monkeypatch.setattr(mod, "fetch_releases", _bad)
    assert mod.main() == 0
