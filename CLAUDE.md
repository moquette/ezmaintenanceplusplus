# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## MANDATORY: markdown house style

Before writing or editing ANY `.md` file in this tree, follow the
**`markdown-house-style`** skill at
`~/Code/moquette/kodi/.claude/skills/markdown-house-style/SKILL.md`. It is the
single standard for all five checkouts and every agent, with no exceptions.

Non-negotiable summary:

- No em dash, en dash, horizontal bar, robot emoji, or AI attribution anywhere.
  The plain hyphen `-` is always fine.
- Never begin a wrapped line with `+`, `-`, or `*`. CommonMark turns it into a
  list item and splits your paragraph.
- Never let an inline code span cross a line break. It strips the
  list-continuation indent and leaves the next agent editing a stale copy.
- Markdown is deliberately NOT auto-formatted here (removed from
  `~/.claude/hooks/auto-format` on 2026-07-18, because prettier's markdown
  printer relocates content between block containers). Do not add it back.

## READ FIRST: start at `TASKS.md`

**`TASKS.md` is this project's task index.** It lists every open item and points
at the detail docs. Both 2026-07-18 restore defects are now FIXED IN CODE; what
survives them is summarized below.

### The two restore defects - both FIXED, one residue OPEN BY DESIGN

**`docs/restore-defects-2026-07-18.md` is the diagnosis record. It was written
before the fixes and still reads "NOT fixed"; treat it as history, not status.
`TASKS.md` is the status.**

Read this before touching `wiz.py`, `tools.py`, `ui.py`, `_kodisettings.py`, or
`service.py`. Short version:

- **Defect A (skin settings clobbered) is FIXED (`be31322`).** Restore writes
  `addon_data/<skin>/settings.xml`, and `_apply_skin_settings` (`wiz.py:869`) now
  re-applies those values IN MEMORY immediately before the restart, so the
  clean-shutdown flush serializes the archive's values rather than the
  pre-restore ones. It was never tvOS-only.
- **The old `wiz.py:753-764` false docstring NO LONGER EXISTS.** Earlier docs
  told agents to distrust a docstring claiming `Quit` skips Kodi's
  clean-shutdown flush. That text was corrected and the current comment
  (`wiz.py:979-981`) states the truth: `RestartApp` being desktop-only means
  `Quit` does not RELAUNCH, NOT that it skips `CApplication::Stop`. Do not go
  looking for the false version; do not reintroduce it.
- **Defect A3 (`lookandfeel.skin` itself) is OPEN BY DESIGN, not unfinished.** A
  restore that CHANGES the skin reopens on the old one, because Kodi offers no
  way to set the skin live without arming the 10-second keep-skin countdown and
  any non-Yes reverts. 2026.07.19.0 ships DETECT AND REPORT, not a fix. See
  `TASKS.md`; the accepted next-cycle design is to terminate instead of `Quit`.
- **Defect B (post-restore prompt discarded input) is FIXED.** The trigger was
  never in EZM++: `skin.estuary7`'s `Home.xml:9` arms an alarm whose
  skinshortcuts rebuild ends in `ReloadSkin()` and destroys the window stack.
  `_keyboard_result` no longer collapses a non-answer into an answer,
  `prompt_devicename_after_restore` re-presents instead of advancing, and
  `service._wait_skin_settled` waits past the deferred build. EZM++ still sets
  NO timeout anywhere - do not go hunting for one.
- **Test trap (still live):** `Skin.HasSetting(<id>)` over JSON-RPC CREATES the
  setting id in memory (default false) and it persists on flush. `GetInfoBooleans`
  is not a read-only probe for skin settings. A regression test built on it passes
  for the wrong reason.

**Defect A is instance number 4 of a class this project already named.** Read
`repo/docs/playbooks/kodi-settings-clobber.md` (its instance 1 is this exact file
and owner, and the documented fix is BOTH mechanisms, not one) and
`repo/docs/plans/atv-every-boot-settings-reassert.md` (an every-boot re-assert
was REJECTED by unanimous adversarial review on 2026-07-08 - do not re-propose
it; its verdict section holds the corrected fix that became
`nsud.rewrite_userdata_xml`).

**`docs/next-update-candidates.md`** is the forward queue for everything else
investigated but unshipped: the video-cache `readfactor` question (answer: do NOT
raise it, with source proof), a `memorysize` GUI-list hazard, and an unresolved
owner decision about credentials stored in cleartext inside the backup zips.

## What this repo is

**EZ Maintenance++** (`script.ezmaintenanceplusplus`) is a fork of EZ Maintenance+
(aenema, peno) for the Tony.7.Bones Kodi 21 "Omega" fleet (5 Fire TV boxes + 2 Apple
TVs). This repo (`moquette/ezmaintenanceplusplus`, public) is the **single source of
truth**: the add-on source, its full test suite, and the build/release tooling live
here and only here.

**Distribution stays in the sibling repo** (remote `tony7bones/tony7bones.github.io`,
local checkout `~/Code/moquette/kodi/repo`; the standalone
`~/Code/moquette/tony7bones.github.io` path older docs cite DOES NOT EXIST)
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

## The backup/restore contract (owner-decided 2026-07-16)

These are the rules every session must hold the backup/restore/wipe code to
(implementation landing 2026-07-16; treat any older behavior in the tree as a bug,
not a spec):

- **Full means full.** A full backup captures EVERYTHING on both OSes, INCLUDING
  `addon_data/pvr.iptvsimple`. The 2026.07.08.5 "zero IPTV" backup exclusion is
  REVERSED - do not reintroduce it. The only exclusions are the add-on's own
  `settings.xml` (it carries the Dropbox token) and `special://home/temp` at the
  ROOT only.
- **Two-layer tvOS capture, loud failures.** A tvOS backup reads BOTH layers: the
  POSIX walk plus the NSUserDefaults plist capture (`nsub.py`), IPTV included. A
  tvOS capture failure FAILS the backup loudly; a backup never silently omits what
  it could not read.
- **Manifest + truthful reporting.** Every backup embeds `backup_manifest.json`
  (`{"created","source_os","entries","failed":[...]}`). Restore verifies the
  extract against it and reports extracted/skipped/failed truthfully; a partial
  restore is reported as PARTIAL, never "Complete".
- **Instance-settings sweep; one bounded toggle.** Restore sweeps the target's
  STRAY `instance-settings-*.xml` AFTER the extract (files the archive does not
  carry; a cancel can never destroy config the box already had) so pvr.iptvsimple
  state exactly equals the archive (the duplicate-instance brick guard). The ONLY
  sanctioned add-on toggle anywhere is the restore-scoped PVR pause: when the
  archive carries IPTV config and pvr.iptvsimple is enabled, restore disables it
  for the extract window and ALWAYS re-enables it afterward (cancel path
  included; a re-enable failure is reported loudly). Without the pause, the live
  client flushes stale in-memory instance settings over the restored files at
  the next clean shutdown (hardware-proven, kodi-settings-clobber.md). Boot-time
  work is limited to SELF-HEALING an interrupted or superseded restore: resuming a
  restore-paused PVR client, the once-per-version stale-key purge, the stale
  bytecode purge, and the read-only post-restore check. Boot NEVER installs,
  stages, or enables an add-on the box did not already have enabled, and restore
  never installs or stages add-ons.
- **Two-layer wipe.** A wipe on tvOS (One-Tap clean wipe, Fresh Start) clears BOTH
  layers - the POSIX files AND the NSUserDefaults keys - with the same exclusions.
  A POSIX-only wipe leaves stale keys that shadow the restored files; that bug
  class is closed, do not regress it.
- **Stale-key purge semantics.** `nsud.purge_stale_keys` clears the
  vector-everything-era stale keys. It MUST materialize any key-only file to disk
  first - the purge never destroys the only copy of anything. It runs ONLY
  automatically, from three clearers: inside every restore (`wiz.py`, both the
  wipe and merge paths), once per add-on version at boot (`service.py`), and the
  two-layer wipe's own key pass (`onetap.py`). The manual "Purge stale tvOS keys"
  menu action was REMOVED in 2026.07.19.5 - it covered no case the automatic
  clearers miss, and it asked a non-technical owner to self-diagnose an invisible
  symptom. Do not reintroduce a manual entry point.
- **The purge and the duplicate-listing probe must agree.** `purge_stale_keys`
  deliberately KEEPS the skin's dual-layer `script.skinshortcuts/*.DATA.xml`
  sidecars, so `restorecheck.duplicate_listing_hits` must not count them either -
  it IMPORTS `nsud._is_skin_menu_sidecar` rather than re-deriving the rule. Two
  copies of that predicate drifting is what made every tvOS restore end in a
  "needs attention" the owner could do nothing about (atv2, 23 false hits,
  `verification/2026.07.19.4.json`). The exclusion is the `*.DATA.xml` sidecar
  pattern ONLY: `script.skinshortcuts/settings.xml` and every non-sidecar
  duplicate still hit, because a real stale key shadowing a restored file is the
  one thing that probe exists to catch.
- **Verification widened, gate unchanged.** `tools/verify_device.py` gains
  restore_contract checks (IPTV inventory, profile fingerprint, duplicate-listing,
  shadow probe) and a `--diff` mode. Hardware verification is REQUIRED for
  backup/restore/wipe changes (narrowed 2026-07-20 from every release); this
  contract widens what the gate checks when it applies.

The tvOS storage facts above remain true and load-bearing under this contract: a
key SHADOWS the disk file, Kodi never re-materializes a disk file from a key, and
`xbmcvfs.delete()` on tvOS drops only the key while leaving the POSIX file. The
two-layer wipe and the purge exist BECAUSE of those facts.

## House rules (inherited from the fleet's workflow)

- implement -> TEST -> gate (pytest + ruff green) -> commit/release.
- **Independent QA + architecture review is required ONLY for changes to backup,
  restore, or wipe code.** Everything else ships on a green suite. (Narrowed
  2026-07-20 from "every phase, no exceptions".)
- **Device verification is required ONLY for backup/restore/wipe changes.**
  Routine releases do not need a `verification/<version>.json`. (Narrowed
  2026-07-20.)
- **Routine changes get a one-line commit message.** Long-form records
  (acceptance logs, multi-paragraph commits) are for genuine incidents only.
- Approval is needed for DESTRUCTIVE or OUTWARD-FACING actions only: wiping a
  box, restoring onto a box, publishing, pushing. Reading logs, listing files,
  read-only JSON-RPC queries and inspecting archives need no approval. The
  office Fire TV at `192.168.7.162` stays HANDS-OFF for everything, reads
  included.
- Safety core, unchanged: a backup must contain what it claims (one
  archive-contents inspection when backup/restore code changes); CI green before
  deploy; skins install from the Kodi repo, never adb/devicectl push.
- No AI attribution anywhere; no em dashes in written deliverables.
- Never edit `addons/script.ezmaintenanceplusplus/` in the proxy repo - that
  directory no longer exists (deleted 2026-07-14) and nothing reads it.
