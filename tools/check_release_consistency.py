#!/usr/bin/env python3
"""Release-existence / version-regression gate for EZ Maintenance++ (GAP 2).

WHY THIS EXISTS
---------------
The Tony.7.Bones proxy repo (tony7bones.github.io) has `_tools/check_consistency.py`,
which fails CI if addon.xml's version, the released zip, and the git tag disagree - and
it caught a real bumped-but-unreleased slip in that repo. This repo (the single source
of truth for EZ Maintenance++ since 2026-07-14) had no equivalent: a bumped-but-never-
released `script.ezmaintenanceplusplus/addon.xml` could sit on `main` indefinitely with
no signal, and the proxy's hand-synced hosted metadata mirror would have nothing to
catch it either.

DESIGN CONSTRAINT (do not "fix" this by reverting it)
------------------------------------------------------
`tools/release.sh` anchors its tag to `origin/main`, and the release flow is:

    1. bump script.ezmaintenanceplusplus/addon.xml, commit, push to main
    2. run tools/release.sh, which builds, tags v<version>, publishes the GitHub
       Release asset, and verifies the asset is live and byte-correct

So `main` LEGITIMATELY carries an addon.xml version with no matching release for the
entire window between steps 1 and 2 - that can be minutes or days. A naive "addon.xml
version must have a matching release" check would false-fail on every push in that
window, which is exactly the kind of gate this project's house rules forbid ("if a gate
you add would red CI on the current clean tree, it's mis-designed").

So this gate only fails on a REGRESSION or a real CONTRADICTION - never merely because
addon.xml is ahead of the latest release (that is the normal, legitimate pending state):

  - REGRESSION: addon.xml's version is LOWER than the latest published release's
    version. main must never carry a version older than something already shipped.
  - CONTRADICTION: a release/tag matching addon.xml's OWN version exists, but it has
    no asset, or none of its assets match this repo's published naming convention
    (<addon-id>-<version>.zip - see tools/build.py / tools/release.sh). A tag that
    exists without a working asset is a broken release, not a pending one.

NETWORK POSTURE
----------------
The real check queries this repo's own GitHub Releases (public, read-only,
unauthenticated works, but an Actions GITHUB_TOKEN is used when present to avoid the
public rate limit). A network failure here is NOT treated as a gate failure - it
prints a warning and exits 0. This is deliberate: GitHub API reachability is not this
project's regression to detect, and a transient network blip must never turn a clean
tree's CI red. `check()` below is pure (no I/O) precisely so the pytest suite can
exercise every regression/contradiction/pending scenario fully mocked, with no live
dependency at all (see tests/test_release_consistency_gate.py).

Usage:
    python3 tools/check_release_consistency.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ADDON_ID = "script.ezmaintenanceplusplus"
ADDON_XML = os.path.join(ROOT, ADDON_ID, "addon.xml")
REPO = "moquette/ezmaintenanceplusplus"


def read_addon_version(path: str = ADDON_XML) -> str:
    with open(path, encoding="utf-8") as f:
        xml = f.read()
    m = re.search(r'<addon\b[^>]*\bversion="([^"]+)"', xml)
    if not m:
        raise SystemExit(f"FATAL: could not read version from {path}")
    return m.group(1)


def parse_version(v: str) -> tuple:
    """Parse a dot-separated version into a comparable tuple of ints.

    This repo's scheme is date-stamped (YYYY.MM.DD.N), not the proxy's single-digit
    MAJOR.MINOR.PATCH - plain integer-tuple comparison is exactly right for it.
    """
    return tuple(int(p) for p in v.strip().split("."))


def expected_asset_name(version: str) -> str:
    return f"{ADDON_ID}-{version}.zip"


def check(addon_version: str, releases: list) -> tuple:
    """Pure logic, no I/O. Returns (ok, problems).

    `releases` is a list of GitHub API release objects (dicts with at least
    "tag_name" and "assets": [{"name": ...}, ...]) - the exact shape returned by
    GET /repos/{repo}/releases, so a hand-built list in a test is indistinguishable
    from the real API's response.
    """
    problems = []
    addon_v = parse_version(addon_version)

    published = []
    for r in releases:
        tag = r.get("tag_name") or ""
        if not tag.startswith("v"):
            continue
        try:
            v = parse_version(tag[1:])
        except ValueError:
            continue
        published.append((v, tag, r))

    if published:
        latest_v, latest_tag, _ = max(published, key=lambda t: t[0])
        if addon_v < latest_v:
            problems.append(
                "REGRESSION: script.ezmaintenanceplusplus/addon.xml version "
                f"{addon_version} is behind the latest published release "
                f"{latest_tag} - main must never carry a version older than "
                "something already released."
            )

    own_tag = f"v{addon_version}"
    own_release = next((r for _, t, r in published if t == own_tag), None)
    if own_release is not None:
        expected = expected_asset_name(addon_version)
        asset_names = {a.get("name") for a in own_release.get("assets", [])}
        if expected not in asset_names:
            problems.append(
                f"CONTRADICTION: release {own_tag} exists but is missing its "
                f"expected asset {expected} (assets found: "
                f"{sorted(n for n in asset_names if n)}) - this is a broken "
                "release, not a pending one."
            )

    return (not problems, problems)


def fetch_releases(repo: str = REPO) -> list:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=100",
        headers={"Accept": "application/vnd.github+json"},
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    addon_version = read_addon_version()
    try:
        releases = fetch_releases()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(
            f"WARNING: could not reach/parse the GitHub Releases API ({exc}) - "
            "skipping the release-existence gate for this run (network "
            "reachability is not this gate's regression to detect).",
            file=sys.stderr,
        )
        return 0

    ok, problems = check(addon_version, releases)
    print("release-existence gate:")
    print(f"  addon.xml version : {addon_version}")
    print(f"  published releases: {[r.get('tag_name') for r in releases]}")
    if ok:
        print("OK - no version regression or release contradiction")
        return 0
    print("FAIL:")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
