# Post-restore prompt deleted; device name and cache buffer preserved

**Status: implemented, tested, tvOS-verified on hardware. NOT released.**
Version `2026.07.19.4`. Owner decision 2026-07-19.

## What changed

A restore used to clone the SOURCE box's `services.devicename` and
`filecache.memorysize`, and then, on the first boot afterwards, open a two-step
modal asking the user to repair both. The prompt is deleted. The values are
preserved instead.

## Why the questions were wrong

**The buffer question was theatre.** `_recommended_mb()` derives its answer from
the box's own RAM, so the user could contribute nothing he knew better. Its own
docstring already conceded that the 10 percent factor and the 50/200 bounds
"were asserted when written, never derived from a measurement or a Kodi source
citation"; that on tvOS `System.Memory(total)` has no relationship to the
per-app jetsam budget, so both Apple TV tiers land on the ceiling and the value
is effectively a constant there; and that a real jetsam report put Kodi's
LIFETIME peak on an Apple TV at about 69 MB, so the 200 MB it offered had never
been allocated.

**The device-name question existed only to undo the clone.** A fresh Kodi
install is already named "Kodi", so preserving covers every case including a
first-ever run. There is no first-run gap and no first-run decision.

**The flow's failure surface went with it:** an unattended boot modal that any
component owning the window stack could destroy, an in-boot attempt counter, a
multi-boot marker, and the first-run arming that fired the same prompt on a
brand-new box.

## How the preserve works: BOTH halves, or it does not work

Skipping the live-apply alone is insufficient - the archive's value still sits
in the restored `guisettings.xml` and wins at the next boot. This is the pattern
already proven for `lookandfeel.skin`.

1. **Capture.** `tools.capture_device_identity()` reads this box's live
   `services.devicename` and `filecache.memorysize` over JSON-RPC. It is the
   FIRST statement in `wiz.restore()`, so no later edit can slip a wipe or an
   extract in front of it. Reading from LIVE settings is why it is still correct
   on the One-Tap path, where the caller wipes BEFORE calling `restore()`: a
   wipe removes files, not the running process's memory. A value that cannot be
   read is OMITTED, never defaulted - the archive's value beats an invented one.

2. **Half A, memory.** Both ids joined `lookandfeel.skin` in
   `_kodisettings._BOOT_STATE_ONLY`, so `apply_guisettings` never pushes the
   archive's values into Kodi's live store.

3. **Half B, disk.** `wiz._preserve_device_settings()` writes the captured
   values back into the restored `guisettings.xml` via `write_guisetting`, then
   vectors the file with `nsud.persist_one` (on tvOS the NSUserDefaults key
   SHADOWS the disk file and Kodi never copies a key back, so an unvectored
   write is invisible there). It runs after the tvOS re-vector - which DROPS the
   POSIX copy - and re-materializes the file from the VFS first, never a stub.

Ordering is load-bearing and is pinned by test: a `persist_one` that ran before
the write-back would publish the ARCHIVE's values into the durable store.

## Decoupling: EZM++ no longer knows any skin exists

The prompt was the last thing tying this add-on to `skin.estuary7`. Deleted, not
renamed or generalised:

- `_SKIN_DEFERRED_BUILD_SECS = 25` and the comment naming the skin's AlarmClock
- `_wait_skin_settled` and its `skinshortcuts-isrunning` polling
- the `_wait_kodi_ready` docstring instructing callers to also call it
- the call site in `_maybe_prompt_after_restore`
- the `tools.py` comments citing the skin's behaviour as rationale

The test applied: could this skin run with NO EZ Maintenance++ installed, and
could EZ Maintenance++ run under ANY skin, both correct with zero knowledge of
the other? A renamed wait, any polling of skin-internal state, any shared marker
or property handshake would all still be coupling. A handshake IS a dependency.

Deliberately KEPT, because each is legitimate and skin-agnostic in behaviour:
`restorecheck.py` skinshortcuts duplicate probing; the `wiz.py` skin-settings
reapply guarded on `getSkinDir()`; the `lookandfeel.skin` read/write;
`_BOOT_STATE_ONLY`; EZM++'s own namespaced `Window(10000)` properties; and the
generic Kodi-API paragraph in `tools.py`. `nsud.py`'s skinshortcuts KEEP rules
are correct behaviour carrying skin-specific PROSE; that file was not otherwise
touched, so the prose was left alone rather than rewritten in passing.

## On-demand replacements

Both settings remain changeable whenever the user wants, from the add-on's own
menu: **Video Cache Buffer** (`tools.advancedSettings`, already existed) and
**Device Name** (`tools.deviceName`, new). Renaming is now a deliberate act
rather than an answer to a question nobody asked for.

## Hardware verification

`wiz.py` is a CONTRACT_FILE, so this needed a fresh two-class artifact.

**tvOS: DONE.** `verification/2026.07.19.4.json`, pulled live from atv2.

The build was deployed with `devicectl device copy to` and then **read back off
the box and hashed against source** - ten files including all five contract
files, all MATCH. Exit status and a bumped manifest were not trusted. The box's
own `ezm_contract_fingerprint` property
(`3fd90fcd04eafcde0c9662f829e6dddc3cb2ca6c5d530ebcb34c4645e9fce51f`) equals the
HEAD fingerprint, so the artifact certifies the code actually running.

`clean_single_layer: false` in that artifact is PRE-EXISTING and unrelated. All
23 duplicates are `script.skinshortcuts/*.DATA.xml`, the skin's own deliberate
durability sidecar documented at `nsud.py:413`, and the count has been identical
in every tvOS artifact since 2026.07.17.6. Non-skinshortcuts duplicates: zero.

The live read on atv2 returned `services.devicename = "atv2"` and
`filecache.memorysize = 200` - exactly the pair a restore would previously have
overwritten with the golden image's values.

**android: DONE.** Same artifact, `Office` entry, pulled live from the office
Fire TV under an explicit owner authorization on 2026-07-19.

That box was baselined by content hash BEFORE anything was deployed, and
re-measured afterwards. Everything except the EZM++ version is byte-identical:
all 26 `script.skinshortcuts` files including `mainmenu.DATA.xml`
(`d2bcc3464c0ff970d0465d3933f7827f`), the active includes (533783 bytes,
`8f6e4b91d65a257511afc29162dbad35`, 0 `addtile.png`, 0 `System.HasPVRAddon`),
`skin.estuary7` 1.0.70, and `addons.updatemode=1`.

The deploy was an adb push, because 2026.07.19.4 is unreleased and therefore has
no repository path; the repository-only rule covers SKINS. All ten files
including the five contract files were pulled back off the box and hashed
against source: all match. Kodi was stopped with a CLEAN `Application.Quit`
rather than a force-stop, to protect sqlite state, and came back healthy. The
box's published `ezm_contract_fingerprint` equals HEAD. The only errors in the
boot log are the upstream `feeds.kodi.tv` 404s, which appear 19 times in the
PREVIOUS boot's log and are unrelated. No crash, no abort, no
`CRepositoryUpdateJob` activity.

With both classes present the suite is GREEN.

## Correction to a documented fleet fact

`repo/.claude/skills/atv-log-pull/SKILL.md` §7 states that
`devicectl device copy to` "silently refuses to OVERWRITE an existing file,
reporting success either way", proven "across nine attempts", and concludes the
only route for replacing files is a Kodi repository update.

**That is false, and it was tested directly on atv2 on 2026-07-19.** Overwrite
works, including the same-size case with a 1980 timestamp that this project's
deterministic builds produce. The real add-on files - `wiz.py`, `tools.py`,
`service.py` and the rest - were overwritten and hash-verified.

The likely cause of the original observation is visible in the tool's own help
text: `copy to` "copies a file or directory to the device, **skipping files that
have not been modified**". A flag-order mistake also produces a `Usage:` block on
stderr that is easy to read as a silent success - it happened once during this
session before the flags were corrected. `copy to` does NOT accept the same flag
order as `copy from`.

The read-back-and-hash discipline the skill recommends remains right and was
followed. Only the impossibility claim is wrong, and it matters: it is the stated
reason an earlier session concluded tvOS verification was blocked.
