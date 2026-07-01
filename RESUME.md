# EZ Maintenance++ - RESUME HERE (handoff, 2026-06-30)

Paste this (or point an agent at it) to pick up where we left off.

## TL;DR

EZ Maintenance++ (`script.ezmaintenanceplusplus`) is the all-in-one "Swiss Army
Knife" Kodi 21 Omega backup util: ONE tool, three destinations **Local | Network
(SMB/NFS) | Dropbox**. Built, QA-hardened, and **live-proven end-to-end on Apple TV
(tvOS) and Fire TV**. As of **2026.06.30.14**: PKCE sign-in (no app secret), on-device
QR, resilient resumable uploads, real progress bars, and a fully working **restore** (the
big saga - fixed a backup that included a partial copy of itself under `temp/`). As of
**2026.06.30.20** it also has **One-Tap Restore** (pin a specific backup as a golden
snapshot; one tap = verify -> wipe -> restore, all through the proven restore path) as a
proper menu item with pin rename/manage, plus a **hardened Fresh Start** (clean Kodi canvas
that keeps EZM++ enabled). **90 tests green.** **SHIPPED + LIVE in the Tony.7.Bones Kodi
repo** (`tony7bones.github.io`, proxy v2.2.2). Part of the **Tony.7.Bones "++" suite** -
"take a proven app and strengthen it" - alongside **Estuary MOD V2++** and **Tony.7.Bones
Setup**. See `docs/one-tap-restore.md` for the One-Tap design + as-built notes.

## Known issues (to polish)

- **Advanced Settings (Buffer Size)** - reworked in **2026.06.30.21**. Correction to an
  earlier note in this file: `tools.advancedSettings()` already emitted the correct Kodi
  19+/Omega `<cache>` schema (`<memorysize>`/`<buffermode>`/`<readfactor>`) - it was NOT the
  pre-19 layout. The real weaknesses were a bare `open()` write with no verification, no way to
  see the already-saved value (so you could not tell whether a prior change stuck), and only a
  passive "please restart" message. It now shows the current buffer, writes via `xbmcvfs`
  (robust on tvOS) and verifies the file landed, always uses the Omega schema, and offers to
  restart. NOTE: the owner-reported "settings changed - keep new or old?" prompt was NOT
  reproduced off-device; if it persists after .21, it needs on-device investigation (likely a
  Kodi runtime-reload behavior, not the file content).

## Repo / build / test

- Repo: `~/Code/moquette/ezmaintenanceplusplus` (its OWN git repo, NOT tony7bones.github.io), branch `main`.
- Add-on dir: `script.ezmaintenanceplusplus/`. Version **2026.06.30.20**.
- Build: `./build.sh` -> `dist/script.ezmaintenanceplusplus-<version>.zip` (excludes cache cruft). The Dropbox **client_id is hardcoded** in `dropbox_remote.py` (public under PKCE; no `_appauth.py`, no secret).
- Deployed copy lives in `~/Code/moquette/tony7bones.github.io/addons/script.ezmaintenanceplusplus/`; to ship a new version use that repo's **`deploy` skill** (edit source here -> sync -> generate_repo -> push; EZM++ is raw-served, no proxy release needed).
- Tests: `cd repo && python3 -m pytest -q` (`tests/`, 61 passing, mock-Kodi harness).
- Dropbox creds: built-in App-folder app `tony-7-backup`; key/secret live in the gitignored `script.ezmaintenanceplusplus/resources/lib/modules/_appauth.py` (ships in the zip, NEVER in git) AND in the vault: `cd ~/Code/moquette/vault && bin/vault-get DROPBOX_APP_KEY` / `bin/vault-get DROPBOX_APP_SECRET` (VAULT.md §23).

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
