# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## What this repo is

**EZ Maintenance++** (`script.ezmaintenanceplusplus`) is a fork of EZ Maintenance+
(aenema, peno) for the Tony.7.Bones Kodi 21 "Omega" fleet (5 Fire TV boxes + 2 Apple
TVs). This repo (`moquette/ezmaintenanceplusplus`, public) is the **single source of
truth**: the add-on source, its full test suite, and the build/release tooling live
here and only here.

**Distribution stays in the sibling repo** `~/Code/moquette/tony7bones.github.io`
(the Tony.7.Bones Kodi repository, a virtual proxy `repository.tony7bones`): this
repo publishes a GitHub Release asset via `tools/release.sh`, and the proxy carries
only a hosted metadata mirror (`addons/hosted/script.ezmaintenanceplusplus/` -
`addon.xml` + icon + fanart, no source) whose `repository.json` entry points at that
release asset - the exact same "own repo + release asset" pattern already proven for
the skin project, `moquette/estuary7` / `skin.estuary7`.

Until 2026-07-14 this add-on's source was hand-synced between two repos (a copy here,
a copy in the proxy repo), and the copies drifted - the proxy repo's copy had the
full test suite and got the real fixes; this repo's copy went stale for weeks. That
duplication is gone. Fix bugs and add tests **here**. The proxy repo's
`.claude/skills/ezm-backup-doctor/SKILL.md` is the accurate triage/procedure guide
for backup/restore failures and cross-references back to this repo.

## The build/test/release contract

- **Deterministic packaging.** `tools/build.py` (wrapped by `./build.sh`) sorts zip
  members and fixes 1980-01-01 timestamps, same discipline as the proxy repo's
  `generate_repo.py` and the skin repo's `build_skin.py`. `./build.sh --check` builds
  twice and byte-compares.
- **Tests are mandatory before any release.** `/opt/homebrew/bin/python3 -m pytest
tests/ -q` (the system `python3` on this machine is 3.9, too old for this suite).
  `ruff check tests/ tools/` must also be clean.
- **`tools/release.sh` is the only sanctioned release path.** It builds, tags
  `v<version>` anchored to `origin/main` (never local/unpushed work - a release can
  never smuggle out unreviewed changes), publishes the zip as a GitHub Release asset
  via `gh release create`, then verifies the asset is anonymously downloadable and
  its sha256 matches the local build. A release that fails verification is a hard
  failure, not a warning.
- **After releasing here, the proxy repo needs a follow-up release** - bump
  `addons/hosted/script.ezmaintenanceplusplus/addon.xml`'s version to match, then
  `python3 _tools/release.py --proxy` in that repo. A version bumped in `addon.xml`
  here is not live on any box until BOTH of those happen.
- **This add-on's changelog is hand-written, multi-line prose** (`changelog.txt` +
  the `<news>` block in `addon.xml`) - NOT the one-line convention the proxy repo's
  `release.py` automation expects. Never run that automation against this add-on's
  news; it has corrupted the changelog before (~190 lines mangled in one run).

## The tvOS/Apple TV storage rules (read before touching `nsud.py`/`nsub.py`/`boxsetup.py`)

Apple TV shadows certain userdata `.xml` files into NSUserDefaults; a key SHADOWS the
disk file, it does not mirror it, and Kodi never copies a key back to disk. Getting
this wrong has destroyed real user data twice (2026-07-08, 2026-07-14). The
authoritative model (with exact Kodi source citations) lives in the sibling proxy
repo: `tony7bones.github.io/.claude/skills/kodi-storage-map/SKILL.md`. Three
mechanical guards in this repo enforce the lessons - do not remove or route around
them without understanding why they exist:

- `tests/test_no_raw_userdata_writer.py` - a chokepoint lint (AST-based) that fails
  if any function writes a userdata/`addon_data` XML with plain `open()`/
  `xbmcvfs.File()` without calling `nsud.persist_one`.
- `tests/fake_kodi_sandbox_io.py` + `tests/test_tvos_sandbox_io_contract.py` - a
  two-layer tvOS storage fake (NSUserDefaults keys + a real POSIX tree) that can
  represent "key exists, disk file gone" - the shape a plain-dict fake cannot
  express, which is why 33 tests once stayed green through a real bug.
- `tools/verify_device.py` + `tests/test_storage_change_requires_device_verification.py`
  - a machine-generated hardware-verification gate. A change to `nsud.py`/
    `boxsetup.py` without a fresh `verification/<version>.json` artifact (pulled live
    over Kodi's JSON-RPC from a real device) fails the suite. "Fixed in code" is not a
    claim this add-on gets to make unverified.

Two corrected facts, now consistent everywhere in this project - do not let either
regress:

- `xbmcvfs.delete()` **cannot** delete a userdata `*.xml` on tvOS. It drops the
  NSUserDefaults key and reports success; the POSIX file is left on disk, silently.
- Kodi does **not** re-materialize a disk file from its NSUserDefaults mirror. A key
  shadows the disk; nothing ever copies it back.

## House rules (inherited from the fleet's workflow)

- implement -> TEST -> gate (pytest + ruff green) -> adversarial QA -> REAL-DEVICE
  verify (for any `nsud.py`/`boxsetup.py`/storage-adjacent change) -> document ->
  only then commit/release. No "fixed in code" claims without hardware proof.
- No AI attribution anywhere; no em dashes in written deliverables.
- Never edit `addons/script.ezmaintenanceplusplus/` in the proxy repo - that
  directory no longer exists (deleted 2026-07-14) and nothing reads it.
