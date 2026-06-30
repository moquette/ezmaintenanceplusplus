# One-Tap Restore (modernized "Wizard Creator") - build spec

Decided via interview 2026-06-30. This replaces the legacy "MY WIZARD" / "Wizard
Creator" feature (a urllib `FancyURLopener` "install a build from an http URL" tool)
with a private, VFS-native one-tap restore of the user's own backups.

## What it is

Pin a **specific** backup file to a slot; one tap restores it - a "golden snapshot"
you can always fall back to. Private by construction (no public capability URLs).

## Decisions

| Area          | Decision                                                                                       |
| ------------- | ---------------------------------------------------------------------------------------------- |
| Name          | **"One-Tap Restore"** - page title + settings tab (retire "MY WIZARD"/"Wizard Creator")        |
| Job           | One-tap restore of the user's _own_ backups                                                    |
| Pin           | A _specific_ backup file (golden snapshot), not a folder/latest                                |
| Per-pin type  | A pin can be a **full** or **userdata** backup; the pin remembers which                        |
| Sources       | VFS (`nfs://`, `smb://`, local, `special://`) **+** Dropbox (API-listed). **No raw http/ftp.** |
| Apply         | **Clean wipe -> restore**: download + verify valid zip FIRST, confirm, THEN wipe, then restore |
| Slots         | A few fixed slots (~8) in the settings tab; each = Name + "Pick backup"                        |
| Pin-as-you-go | After every backup: "Pin this for one-tap restore?" -> choose slot + name                      |
| Row display   | Name . source . date . size                                                                    |
| Verify        | A "is this pin still valid?" check (does the backup still exist + is it a valid zip)           |
| Fresh Start   | Stays a standalone "wipe to clean Kodi" action AND is the reused, hardened wipe inside One-Tap |

## Engine

- Rip out `urllib` `FancyURLopener`; go **VFS-native** (`xbmcvfs.copy` / `xbmcvfs.File`)
  so it speaks `nfs://`, `smb://`, `special://`, local.
- Dropbox pins resolve through the **authenticated `dropbox_remote` API** (list +
  download), never a public link.
- Route the actual restore through the **proven `wiz.restore()` path**, so every tvOS
  fix comes for free: `temp/` skip, settings re-apply (`_kodisettings`), `UpdateLocalAddons`,
  verify-before-extract, honest extract reporting, clean `Quit`.

## Safety

- **Verify before wipe**: never Fresh Start until the backup is downloaded and confirmed
  a valid zip. A bad download/source can never strand a wiped box.
- Clear "this will wipe the box" confirmation before a clean-wipe restore.
- Pinned backup deleted/rotated -> clear error, **no wipe**.
- `FRESHSTART` hardening: correct excludes (the add-on must survive the wipe), fix the
  crude `os.remove`/`os.rmdir`-same-path logic.

## Storage

Fixed slots persist in the add-on settings (like the legacy `name#/url#/img#`), extended
with: source kind (vfs/dropbox), the path/identifier, backup type (full/userdata),
captured date/size for display.

## Out of scope (for v1)

Dynamic unlimited in-page add/edit (we chose fixed slots + pin-as-you-go); installing
external community builds (this is now a personal-restore tool).

## The "++" pattern it inherits (from Estuary MOD V2++)

One-Tap Restore is the next add-on in the Tony.7.Bones "++" suite, and it is built to the
same discipline already proven in Estuary MOD V2++ (`script.tony7bones.modv2plus`). That
add-on's contract is the spec for the whole line:

- **In-tab Apply / Restore + Verify buttons** in a settings tab (mirror modv2plus's Skin
  Settings category UX - the "one tap" IS that Apply button).
- **Non-destructive**: back up / confirm before you touch anything. Here: download and
  verify the pinned snapshot is a valid zip BEFORE any wipe.
- **Verify before destroy**: never Fresh Start until the snapshot is confirmed good - a bad
  source can never strand a wiped box.
- **Survive the device's weirdness**: route the actual restore through the proven
  `wiz.restore()` path so the tvOS fixes (temp/-skip, `_kodisettings` re-apply,
  `UpdateLocalAddons`, clean Quit) come for free.
- **Self-heal**: consider a settings-aware gate (like modv2plus's boot service) that can
  re-verify pins on launch and warn if a pinned snapshot has gone missing.

The suite thesis in one line: **back up before you touch, verify before you destroy,
survive the device, self-heal.** Anything that earns the "++" clears that bar.
