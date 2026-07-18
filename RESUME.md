# EZ Maintenance++ - repo / build / process notes

This file was originally a 2026-06-30 handoff snapshot. It was trimmed on
2026-07-18: every point-in-time claim (version numbers, test counts, box state,
"what works" proof logs) was stale or wrong, and the backup/restore contract it
carried was a second copy of the one in `CLAUDE.md` - exactly the drift this
repo consolidated to eliminate. What remains below is the material that is
still true and is not recorded anywhere else.

**The backup/restore contract lives in `CLAUDE.md`.** It is the single copy.
Do not restate it here.

## PICK UP HERE (2026-07-18)

**Start at `TASKS.md`** - the project task index (created 2026-07-18; it did not
exist before, which is how doctrine got missed).

Two OPEN restore defects, diagnosed but NOT fixed, no code changed, no build cut:
**`docs/restore-defects-2026-07-18.md`**. Defect A (restore loses skin settings
to Kodi's clean-shutdown flush) has a confirmed, empirically reproduced root
cause. Defect B (post-restore device-name prompt discards typed input) has a
proven mechanism but an unidentified trigger. The fix plan in that document is
PROPOSED and still needs QA + architect approval before any code is written.
`CLAUDE.md` carries the short version at the top.

## Repo / build / test

- Repo: its OWN git repo (remote `moquette/ezmaintenanceplusplus`, PUBLIC on GitHub -
  required so a Kodi box can anonymously download a release asset), branch `main`.
  This is the ONLY place the add-on source is edited.
  **LOCAL CHECKOUT: `~/Code/moquette/kodi/ezmpp`.** The standalone path
  `~/Code/moquette/ezmaintenanceplusplus` that older docs cite DOES NOT EXIST
  (verified 2026-07-18); the sibling repos live at `~/Code/moquette/kodi/repo`
  (tony7bones.github.io) and `~/Code/moquette/kodi/estuary7`.
- Add-on dir: `script.ezmaintenanceplusplus/`. Version is whatever `addon.xml` says
  (date-stamped `YYYY.MM.DD.N` scheme; check the file, do not trust a number written
  down in any doc, including this one).
- Build: `./build.sh` -> `dist/script.ezmaintenanceplusplus-<version>.zip`, a
  DETERMINISTIC zip (sorted members, fixed 1980-01-01 timestamps - same discipline as
  `moquette/estuary7`'s `tools/build_skin.py`). `./build.sh --check` builds twice and
  byte-compares.
- Release: `tools/release.sh` builds, tags `v<version>`, publishes the zip as a
  GitHub Release asset on `moquette/ezmaintenanceplusplus` via `gh release create`,
  then verifies the asset is anonymously downloadable and its sha256 matches the
  local build (refuses to leave a broken release in place). `tools/release.sh
  --dry-run` shows the plan without tagging/releasing.
- Distribution: `tony7bones.github.io` carries only a metadata pointer at
  `addons/hosted/script.ezmaintenanceplusplus/` (addon.xml + icon.png + fanart.jpg,
  hand-synced to the released version - same pattern as `addons/hosted/skin.estuary7/`)
  and its `repository.json` entry's `assets.zip` points at this repo's release asset
  URL. After cutting a release here, bump that hosted `addon.xml`'s version in
  `tony7bones.github.io` and ship it via `python3 _tools/release.py --proxy` (it is a
  proxy-config change, not a first-party add-on source change - `repository.json` is
  bundled inside the `repository.tony7bones` add-on's own zip).
- Tests: `cd ~/Code/moquette/kodi/ezmpp && /opt/homebrew/bin/python3 -m
  pytest tests/ -q` (system `python3` on this machine is 3.9, too old for this suite).
  `ruff check tests/ tools/` must also be clean. Includes the tvOS storage-contract
  hardware-verification gate (`test_storage_change_requires_device_verification.py` +
  `tools/verify_device.py` + `verification/*.json`) - a change to `nsud.py`/`boxsetup.py`
  requires a fresh two-class (`tvos`+`android`) device run before it ships; see that
  test's docstring.

## Device verification credentials

`tools/verify_device.py` takes the box's JSON-RPC user, password, host and port
from the environment (`KODI_JSONRPC_USER`, `KODI_JSONRPC_PASSWORD`,
`KODI_JSONRPC_HOST`, `KODI_JSONRPC_PORT`). Nothing device-specific is baked into
the source and there is no fallback default: this repo is public, so a credential
committed here is a published credential. See the "Running a device verification"
section in `README.md`.

## Dropbox credentials - do not re-add a secret

Built-in App-folder app `tony-7-backup`. The PKCE `client_id` is public-by-design
and lives directly in `dropbox_remote.py` (`APP_KEY`); there is no app secret and
no `_appauth.py` file to inject at build time. The vault entries
`DROPBOX_APP_KEY`/`DROPBOX_APP_SECRET` (VAULT.md §23) are a legacy/unused leftover
from an earlier (pre-PKCE) auth design - the running code does not read them.
Earlier revisions of this doc claimed a baked `_appauth.py` had to be recreated
before building. That is wrong and was already contradicted elsewhere in the same
file; do not act on it.

## Process notes / lessons

- Do NOT background a long on-device test the owner is watching live - drive it INLINE
  and narrate. (A silent background agent looped a failing 135 MB upload 3x with no
  feedback - bad UX; the owner had to be my eyes.)
- Verify against GROUND TRUTH (the Dropbox listing) before declaring pass/fail. (I
  called it "failing" off a partial log tail; the folder showed the 25 MB userdata
  backup had actually succeeded.)
- Owner constraints: standard solutions only, NO server hacks/workarounds; lean/elegant;
  UX must beat xbmcbackup; collaborative round-table over solo cowboy work.
