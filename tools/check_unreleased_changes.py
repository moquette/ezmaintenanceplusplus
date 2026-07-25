#!/usr/bin/env python3
"""Catch source that changed at an ALREADY-RELEASED version.

The hole this closes, found by the 2026-07-25 pipeline audit: the publish job is
idempotent by tag. If the tag for the current version already exists it prints
"nothing to publish" and exits 0. So a real bug fix committed without bumping
addon.xml builds fine, passes every test, publishes nothing, reaches zero boxes,
and NOTHING GOES RED ANYWHERE. That is the worst possible failure shape, because
every signal says success.

What this does NOT do is force a bump on every commit. Batching several commits
into one later release is the normal workflow here (7b34e76, c5f3a44, e52d170
were all batched into "Release 2026.07.22.0"), and a gate that fought it would
just get bypassed. So the default is a LOUD WARNING, not a failure.

    tools/check_unreleased_changes.py            warn, always exit 0
    tools/check_unreleased_changes.py --strict   exit 1 if there are unreleased changes

The signal is `git diff <tag> HEAD -- <addon dir>`, not a zip comparison: it
needs no network, no build, and it asks about source rather than about build
output. Three states:

  tag missing   the current version is unreleased, the next push publishes it. OK.
  tag, no diff  genuine idempotent no-op, nothing has changed since release. OK.
  tag + diff    source moved at a released version. Bump before this can ship.

Exit codes: 0 ok (or warning), 1 unreleased changes under --strict, 2 the check
could not run (which is never treated as a pass).
"""

import argparse
import os
import re
import subprocess
import sys

ADDON = "script.ezmaintenanceplusplus"

# Only paths that end up in the zip. A docs or CI edit at a released version is
# not an unreleased change, and flagging it would train people to ignore this.
SOURCE_PATHS = (ADDON,)


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, cwd=None):
    """Run git, returning (exit_code, stdout). Never raises on a non-zero exit."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd or repo_root(),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout.strip()


def read_version(addon_xml=None):
    """Pull the version attribute off the <addon> element."""
    path = addon_xml or os.path.join(repo_root(), ADDON, "addon.xml")
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "<addon " in line:
                match = re.search(r'version="([^"]+)"', line)
                if match:
                    return match.group(1)
    return None


def tag_exists(tag):
    code, out = git("tag", "--list", tag)
    return code == 0 and out != ""


def changed_since(tag, paths=SOURCE_PATHS):
    """Files under `paths` that differ between `tag` and HEAD."""
    code, out = git("diff", "--name-only", tag, "HEAD", "--", *paths)
    if code != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def classify(version, has_tag, changed):
    """Pure decision, so the states are testable without a git repo.

    Returns (state, message) where state is one of: unreleased, clean, dirty.
    """
    tag = "v" + version
    if not has_tag:
        return (
            "unreleased",
            f"{version} has no {tag} tag yet: the next push to main publishes it.",
        )
    if not changed:
        return (
            "clean",
            f"{version} is released and source is unchanged since {tag}.",
        )
    listed = "\n".join(f"      {f}" for f in changed[:20])
    more = f"\n      ... and {len(changed) - 20} more" if len(changed) > 20 else ""
    return (
        "dirty",
        f"{len(changed)} file(s) under {ADDON}/ changed since {tag}, but the\n"
        f"    version is still {version}. Kodi upgrades by version number only, so\n"
        f"    these changes reach NO box until addon.xml is bumped, and the publish\n"
        f"    job will report success while doing nothing.\n{listed}{more}",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on unreleased changes instead of warning",
    )
    args = parser.parse_args(argv)

    version = read_version()
    if not version:
        print("check-unreleased: FATAL: no version found in addon.xml", file=sys.stderr)
        return 2

    has_tag = tag_exists("v" + version)
    changed = changed_since("v" + version) if has_tag else []
    if changed is None:
        # The tag exists but is not reachable, usually a shallow clone. Refuse to
        # call that a pass: an unverifiable gate is not a green one.
        print(
            f"check-unreleased: FATAL: cannot diff against v{version}.\n"
            "    Fetch tags and full history (actions/checkout needs fetch-depth: 0).",
            file=sys.stderr,
        )
        return 2

    state, message = classify(version, has_tag, changed)
    if state == "dirty":
        label = "FAIL" if args.strict else "WARNING"
        print(f"check-unreleased: {label}: {message}")
        return 1 if args.strict else 0

    print(f"check-unreleased: OK: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
