# EZ Maintenance++ - RESUME HERE (handoff, 2026-06-30)

Paste this (or point an agent at it) to pick up where we left off.

**REPO STATUS NOTE (2026-07-14):** everything below this line is a historical
handoff snapshot from 2026-06-30 and is stale on version numbers, test counts,
and the "deployed copy" workflow it describes - do not trust those specifics.
What changed 2026-07-14: this repo (`moquette/ezmaintenanceplusplus`, now
PUBLIC) is the ONE home for EZ Maintenance++ - the add-on source, its full
test suite (`tests/`, includes the tvOS storage-contract hardware-verification
gate), and the build/release tooling (`build.sh`, `tools/release.sh`). It used
to be hand-synced with a second copy of the source living in
`tony7bones.github.io/addons/script.ezmaintenanceplusplus/`, which drifted
(tests only existed proxy-side; fixes were made proxy-side without syncing
back here). That duplication is gone: `tony7bones.github.io` now carries only
a metadata pointer at `addons/hosted/script.ezmaintenanceplusplus/` (addon.xml

- icon + fanart, no source) and serves the zip from this repo's GitHub
  Releases - the same pattern already proven for `skin.estuary7`/`moquette/estuary7`.
  See the "Repo / build / test" section below for the current (accurate) workflow.

## TL;DR

EZ Maintenance++ (`script.ezmaintenanceplusplus`) is the all-in-one "Swiss Army
Knife" Kodi 21 Omega backup util: ONE tool, three destinations **Local | Network
(SMB/NFS) | Dropbox**. Built, QA-hardened, and **live-proven end-to-end on Apple TV
(tvOS) and Fire TV**. As of **2026.06.30.14**: PKCE sign-in (no app secret), on-device
QR, resilient resumable uploads, real progress bars, and a fully working **restore** (the
big saga - fixed a backup that included a partial copy of itself under `temp/`). It also has
**One-Tap Restore** (pin a specific backup as a golden snapshot; one tap = verify -> wipe ->
restore via the proven restore path) as a menu item with **10 slots** and per-pin
Restore/Rename/Verify/Change/Remove, a **hardened Fresh Start** (clean Kodi canvas that keeps
EZM++ enabled), and a corrected **cache buffer** tool (sets Kodi's live `filecache.memorysize`
via JSON-RPC, since Omega ignores advancedsettings.xml). Current **2026.06.30.27**, **95 tests
green.** **SHIPPED + LIVE in the Tony.7.Bones Kodi
repo** (`tony7bones.github.io`, proxy v2.2.2). Part of the **Tony.7.Bones "++" suite** -
"take a proven app and strengthen it" - alongside **Estuary MOD V2++** and **Tony.7.Bones
Setup**. See `docs/one-tap-restore.md` for the One-Tap design + as-built notes.

## Recently fixed

- **Advanced Settings (Buffer Size)** - properly FIXED in **2026.06.30.26** after a two-agent
  research pass (Kodi Omega source + v21 wiki). Real root cause: on **Kodi 21 Omega the
  `advancedsettings.xml` `<cache>` tags are DEPRECATED and IGNORED** - the cache moved to the
  GUI setting `filecache.memorysize` (in MB). So EVERY prior version (which wrote
  advancedsettings.xml, including the .21 "rework") had **no effect** - that is why it "never
  survived a reboot" and threw Kodi's reconcile prompt. `.26` sets `filecache.memorysize` via
  **JSON-RPC** (applies live, no restart), recommends a **stable** value from **total RAM** (the
  old `free/3` used `System.FreeMemory` = `Total - Used`, which over-reports and drifts upward
  every boot - the 172->193->241 the owner saw), warns on huge values, shows the current value,
  and adds **Reset to Kodi default (20 MB)**. Also learned: the "3x RAM" rule is folklore -
  Omega allocates ~1x `memorysize` per stream (a single ring buffer, 75% forward / 25% back).

## Repo / build / test (current, 2026-07-14)

- Repo: `~/Code/moquette/ezmaintenanceplusplus` (its OWN git repo, PUBLIC on GitHub -
  required so a Kodi box can anonymously download a release asset), branch `main`.
  This is the ONLY place the add-on source is edited - see the repo-status note at
  the top of this file.
- Add-on dir: `script.ezmaintenanceplusplus/`. Version is whatever `addon.xml` says
  (date-stamped `YYYY.MM.DD.N` scheme; check the file, do not trust a number in this
  handoff doc).
- Build: `./build.sh` -> `dist/script.ezmaintenanceplusplus-<version>.zip`, a
  DETERMINISTIC zip (sorted members, fixed 1980-01-01 timestamps - same discipline as
  `moquette/estuary7`'s `tools/build_skin.py`). `./build.sh --check` builds twice and
  byte-compares. The Dropbox `client_id` is hardcoded in `dropbox_remote.py` (public
  under PKCE - no `_appauth.py` file, no secret to inject at build time).
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
- Tests: `cd ~/Code/moquette/ezmaintenanceplusplus && /opt/homebrew/bin/python3 -m
pytest tests/ -q` (system `python3` on this machine is 3.9, too old for this suite).
  `tests/` holds the FULL authoritative suite (migrated 2026-07-14 from the proxy
  repo, where it had lived exclusively - the source of the drift this migration
  fixed), plus the pre-existing `test_dropbox_remote.py`/`test_kodisettings.py`
  local to this repo. Includes the tvOS storage-contract hardware-verification gate
  (`test_storage_change_requires_device_verification.py` + `tools/verify_device.py` +
  `verification/*.json`) - a change to `nsud.py`/`boxsetup.py` requires a fresh
  two-class (`tvos`+`android`) device run before it ships; see that test's docstring.
- Dropbox creds: built-in App-folder app `tony-7-backup`; the PKCE `client_id` is
  public-by-design and lives directly in `dropbox_remote.py` (`APP_KEY`). The vault
  entries `DROPBOX_APP_KEY`/`DROPBOX_APP_SECRET` (VAULT.md §23) are a legacy/unused
  leftover from an earlier (pre-PKCE) auth design - the running code does not read
  them.

## What WORKS (proven)

- **Local backup + restore**: live-proven on the box (24 MB zip, 139 files, byte-identical round-trip).
- **Dropbox one-tap sign-in** (built-in key): owner signed in; refresh token loaded into the add-on's settings on the Bedroom box.
- **Dropbox round-trip**: live-proven (upload/list/download/delete via the exact add-on calls; owner visually confirmed `EZMpp_live_proof.txt` in `/Apps/tony-7-backup/`).
- **Full USERDATA backup -> Dropbox: SUCCEEDED** - `EZMpp_proof_userdata_202606261521.zip` (25 MB) is in `/Apps/tony-7-backup/`.
- **48 unit tests green.** QA (2 agents) found + fixed 6 bugs, incl. two data-loss HIGHs: (1) a failed VFS/SMB copy reported success then rotation deleted the prior backup; (2) keep-N rotation sorted by filename not timestamp (could delete the newest). Both fixed + proven on-box.

## RESOLVED - large-upload fix (2026-06-30)

**The chunked Dropbox upload is hardened and PROVEN on device.**
`resources/lib/modules/dropbox_remote.py` now uses **8 MiB chunks**
(`CHUNK = 8 * 1024 * 1024`, a 4 MiB multiple), a `(10, 180)` timeout, **per-chunk
retry with exponential backoff** (on timeout / network error / 5xx), and
**resume-from-offset** (re-reads the chunk from disk on a timeout; honors Dropbox's
`incorrect_offset` `correct_offset`), bounded by `MAX_TRIES = 5`. The whole-op retry
remains as a final backstop. Version bumped to **2026.06.30.0**; **53** unit tests
green (added: timeout-resume, incorrect_offset resync, bounded give-up, 5xx backoff).

**Live-proven on the Apple TV (2026-06-30):** a **full** backup
(`kodi_backup_202606300850.zip`, **130.62 MB**) staged then uploaded clean over Apple
TV wifi and landed in `/Apps/tony-7-backup/` - the exact >100 MB case that used to
fail 3x with `write operation timed out`. Delivered to the Apple TV for install via an
NFS export from the dev Mac (Kodi "install from an HTTP source" crashed on tvOS; NFS
was clean). The baked `_appauth.py` (vault `DROPBOX_APP_KEY`/`_SECRET`) was recreated
before building so one-tap sign-in worked.

### Follow-up polish (in progress)

- **Real upload progress bar** - the upload was a blocking call that left Kodi's
  progress bar frozen at 100%. Now wired to report bytes-sent vs total per chunk.
- **QR sign-in** - the authorize step made the user transcribe a long OAuth URL; now
  shows a scannable QR (falls back to the URL dialog if QR generation fails).

## Box / device state (Bedroom Fire TV)

- `192.168.7.84:5555` (adb). Kodi data `/sdcard/Android/data/org.xbmc.kodi/files/.kodi`. JSON-RPC `http://192.168.7.84:8080/jsonrpc` (kodi:kodi). `GUI.ExecuteBuiltin` NOT available (use `Addons.ExecuteAddon`/`SetAddonEnabled`; restart Kodi to register new add-ons; foreground with `am start -n org.xbmc.kodi/.Splash`). The box's Kodi can WEDGE after repeated force-stop/relaunch cycles - reboot to recover; do not hammer restarts.
- Current state: EZM++ 2026.06.26.2 installed, `broken:false`, **SIGNED IN** (refresh token in its `settings.xml`), `destination=2` (Dropbox), `backup.keep=3`. The shipped `default.py` was restored (a test agent's temp `fullbackup`/`fullrestore` routes were removed - verified 0 left). Dropbox `/Apps/tony-7-backup/` holds `EZMpp_live_proof.txt` (134B) + `EZMpp_proof_userdata_...zip` (25 MB). **Owner reports the TV is back to normal.**

## Process notes / lessons (for resume)

- Do NOT background a long on-device test the owner is watching live - drive it INLINE and narrate. (A silent background agent looped a failing 135 MB upload 3x with no feedback - bad UX; the owner had to be my eyes.)
- Verify against GROUND TRUTH (the Dropbox listing) before declaring pass/fail. (I called it "failing" off a partial log tail; the folder showed the 25 MB userdata backup had actually succeeded.)
- Owner constraints: standard solutions only, NO server hacks/workarounds; lean/elegant; UX must beat xbmcbackup; collaborative round-table over solo cowboy work.
