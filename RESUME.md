# EZ Maintenance++ - RESUME HERE (handoff, 2026-06-26)

Paste this (or point an agent at it) to pick up where we left off.

## TL;DR

EZ Maintenance++ (`script.ezmaintenanceplusplus`) is the all-in-one "Swiss Army
Knife" Kodi 21 Omega backup util: ONE tool, three destinations **Local | Network
(SMB/NFS) | Dropbox**. Built, QA-hardened, signed in to Dropbox, and live-proven for
normal backups. **ONE open issue:** very large backups (>~100 MB) time out on the
chunked Dropbox upload from a Fire TV Stick. **Next task: harden the chunked upload.**

## Repo / build / test

- Repo: `~/Code/moquette/ezmaintenanceplusplus` (its OWN git repo, NOT tony7bones.github.io), branch `main`.
- Add-on dir: `script.ezmaintenanceplusplus/`. Version **2026.06.26.2**. Latest commit **f7da26c** (QA fixes + test suite).
- Build: `./build.sh` -> `dist/script.ezmaintenanceplusplus-<version>.zip` (excludes cache cruft; INCLUDES the gitignored `_appauth.py`).
- Tests: `cd repo && python3 -m pytest -q` (`tests/`, 48 passing, mock-Kodi harness).
- Dropbox creds: built-in App-folder app `tony-7-backup`; key/secret live in the gitignored `script.ezmaintenanceplusplus/resources/lib/modules/_appauth.py` (ships in the zip, NEVER in git) AND in the vault: `cd ~/Code/moquette/vault && bin/vault-get DROPBOX_APP_KEY` / `bin/vault-get DROPBOX_APP_SECRET` (VAULT.md §23).

## What WORKS (proven)

- **Local backup + restore**: live-proven on the box (24 MB zip, 139 files, byte-identical round-trip).
- **Dropbox one-tap sign-in** (built-in key): owner signed in; refresh token loaded into the add-on's settings on the Bedroom box.
- **Dropbox round-trip**: live-proven (upload/list/download/delete via the exact add-on calls; owner visually confirmed `EZMpp_live_proof.txt` in `/Apps/tony-7-backup/`).
- **Full USERDATA backup -> Dropbox: SUCCEEDED** - `EZMpp_proof_userdata_202606261521.zip` (25 MB) is in `/Apps/tony-7-backup/`.
- **48 unit tests green.** QA (2 agents) found + fixed 6 bugs, incl. two data-loss HIGHs: (1) a failed VFS/SMB copy reported success then rotation deleted the prior backup; (2) keep-N rotation sorted by filename not timestamp (could delete the newest). Both fixed + proven on-box.

## OPEN / BROKEN - the next task

**Large backups time out on the chunked Dropbox upload.** A 135 MB addons-mode zip
builds fine (~90s) but the upload fails 3x with `ConnectionError: write operation
timed out`. Current upload (`resources/lib/modules/dropbox_remote.py`): **50 MB
chunks** (`CHUNK = 50 * 1000 * 1000`), a `(10, 300)` requests timeout, and it retries
the **WHOLE upload once** (NOT per-chunk, NO resume-from-offset). On a Fire TV Stick's
slow wifi uplink a single 50 MB chunk write blows the timeout, and the whole-upload
retry just times out again. A true "full/everything" backup (`mode='full'` =
`control.HOME`, userdata + addons) is even bigger and would also fail.

### NEXT TASK: harden the chunked upload (standard, no hacks)

Adopt the resilient upload-session pattern:

1. **Smaller chunks** (multiple of 4 MB; likely ~8-16 MB) so each finishes within the timeout.
2. **Retry EACH chunk** with backoff.
3. **RESUME from the session offset** on failure (Dropbox upload sessions are resumable; on `incorrect_offset` Dropbox returns the `correct_offset`) instead of re-sending the whole file.
4. Sane per-request timeout; keep streaming chunks from disk (the session path already streams).

- We were mid-answering **"how are the others handling chunking?"**: xbmcbackup uses 50 MB chunks via the official Dropbox SDK + retry-whole-once (the SDK owns the HTTP/timeout). Finish grounding the exact Dropbox-recommended chunk size + the resilient pattern, then implement.
- After implementing: **re-test the 135 MB backup INLINE, narrating each step** (do NOT background a live on-device test), verify it lands via the Dropbox API, bump version, re-run pytest, commit.

## Box / device state (Bedroom Fire TV)

- `192.168.7.84:5555` (adb). Kodi data `/sdcard/Android/data/org.xbmc.kodi/files/.kodi`. JSON-RPC `http://192.168.7.84:8080/jsonrpc` (kodi:kodi). `GUI.ExecuteBuiltin` NOT available (use `Addons.ExecuteAddon`/`SetAddonEnabled`; restart Kodi to register new add-ons; foreground with `am start -n org.xbmc.kodi/.Splash`). The box's Kodi can WEDGE after repeated force-stop/relaunch cycles - reboot to recover; do not hammer restarts.
- Current state: EZM++ 2026.06.26.2 installed, `broken:false`, **SIGNED IN** (refresh token in its `settings.xml`), `destination=2` (Dropbox), `backup.keep=3`. The shipped `default.py` was restored (a test agent's temp `fullbackup`/`fullrestore` routes were removed - verified 0 left). Dropbox `/Apps/tony-7-backup/` holds `EZMpp_live_proof.txt` (134B) + `EZMpp_proof_userdata_...zip` (25 MB). **Owner reports the TV is back to normal.**

## Process notes / lessons (for resume)

- Do NOT background a long on-device test the owner is watching live - drive it INLINE and narrate. (A silent background agent looped a failing 135 MB upload 3x with no feedback - bad UX; the owner had to be my eyes.)
- Verify against GROUND TRUTH (the Dropbox listing) before declaring pass/fail. (I called it "failing" off a partial log tail; the folder showed the 25 MB userdata backup had actually succeeded.)
- Owner constraints: standard solutions only, NO server hacks/workarounds; lean/elegant; UX must beat xbmcbackup; collaborative round-table over solo cowboy work.
