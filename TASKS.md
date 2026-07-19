# TASKS

Tracking for EZ Maintenance++ (`script.ezmaintenanceplusplus`).
Project rules + backup/restore contract: `CLAUDE.md`. Repo/build/release notes:
`RESUME.md`.

> This file did not exist until 2026-07-18. Work was previously tracked only in
> `repo/TASKS.md` (the hub) and in scattered plan documents, which is why an
> investigation on 2026-07-18 initially missed established project doctrine. If
> you are an agent picking this project up, **this file is the index. Start
> here.**

---

## ⛔ WORKFLOW - non-negotiable (do NOT skip or reorder)

> **implement -> TEST -> GATE (`/opt/homebrew/bin/python3 -m pytest tests/ -q` +
> `ruff check tests/ tools/` green) -> adversarial QA + architecture review ->
> REAL-DEVICE verification -> DOCUMENT -> only THEN commit/release.**

1. **Self-verification is never sufficient.** The owner requires independent QA
   agent AND architecture agent review before any phase is declared done.
2. **"Fixed in code" is not a claim this add-on may make.** Any change touching
   `nsud.py` / `boxsetup.py` / restore / storage behavior requires a fresh
   `verification/<version>.json` pulled live over JSON-RPC from a real box
   (`tests/test_storage_change_requires_device_verification.py`). This gate is
   owner-gated.
3. **System `python3` is 3.9 and too old for this suite.** Use
   `/opt/homebrew/bin/python3`.
4. **A release here is only half.** After `tools/release.sh`, bump the hosted
   `addon.xml` in `tony7bones.github.io` and ship via
   `python3 _tools/release.py --proxy`. A version bumped here alone is live on
   no box.

---

## 🟢 DONE, NOT RELEASED - post-restore prompt deleted, values preserved

**Owner decision 2026-07-19. Version `2026.07.19.4`. Ready; shipping is the
owner's call.** Full record: **`docs/devicename-buffer-preserve-2026-07-19.md`**

The two questions the boot service asked after a restore (name this device, size
the video cache buffer) are DELETED. A restore now PRESERVES this box's own
`services.devicename` and `filecache.memorysize` instead of cloning the source
box's and then asking the user to repair them. Both halves are required and both
shipped: the ids joined `_kodisettings._BOOT_STATE_ONLY` (never live-applied) AND
`wiz._preserve_device_settings` writes the captured values back into the restored
`guisettings.xml` and vectors it. Skipping the live-apply alone is insufficient -
the archive's value still sits in the file and wins at the next boot.

Both settings stay changeable on demand from the add-on menu: **Video Cache
Buffer** (existing) and **Device Name** (new).

**This removed the LAST dependency between EZM++ and `skin.estuary7`.** The
25-second `_wait_skin_settled` wait, its timing constant, and its
skinshortcuts-isrunning polling were deleted rather than renamed. The boot
service now opens NO dialog and knows nothing about any skin; two tests pin that
it cannot come back. `nsud.py`'s skinshortcuts KEEP rules are untouched - they
are correct behaviour with skin-specific prose, and that file was not otherwise
edited.

**Hardware gate: BOTH CLASSES DONE. The suite is GREEN.**
`verification/2026.07.19.4.json` carries genuine `tvos` (atv2) and `android`
(office Fire TV) entries. On both boxes the build was deployed and then READ BACK
and hash-verified (10 files each, including all five contract files), and each
box's own published contract fingerprint equals HEAD.

The office Fire TV was authorized by the owner on 2026-07-19 and handled with a
before/after content-hash baseline: all 26 skinshortcuts files, the active
includes, the skin version and `addons.updatemode` are byte-identical afterwards.
Kodi was stopped with a clean `Application.Quit`, not a force-stop. No crash.

**Correction to a documented fleet fact:** `atv-log-pull/SKILL.md` §7 claims
`devicectl device copy to` silently refuses to overwrite. Tested directly on
atv2 2026-07-19: **it overwrites fine**, including the same-size 1980-timestamp
case deterministic builds produce. That false claim is the stated reason an
earlier session declared tvOS verification blocked. Read-back-and-hash is still
right; only the impossibility claim is wrong.

## 🟢 FIXED IN CODE - Restore defect A: restored skin settings are clobbered

**FIXED 2026-07-18 (`be31322`), awaiting the same device gate as defect B.**
`_apply_skin_settings` in `wiz.py` re-applies the restored skin settings IN
MEMORY, one-shot, immediately before the restart, so the shutdown flush
serializes the archive's values rather than the pre-restore ones. Captured early
(tvOS vectoring drops the POSIX copy), guarded on the live skin, two-argument
builtins only. The five false docstrings that armed this defect are corrected.
Six tests, two mutation-checked.

A3, `lookandfeel.skin` itself: **OPEN BY DESIGN, root cause CONFIRMED.** The
device run this entry used to ask for is no longer owed - the local macOS bench
settled it on 2026-07-19 with a three-arm experiment (clean quit loses the
written value; SIGKILL with no flush keeps it; a boot honors whatever is on
disk), isolating the cause to `CSettings::Save()` at
`Application.cpp:2131`. `_apply_boot_skin` writes the skin to disk and the
shutdown flush overwrites it from live memory, so a restore that CHANGES the
skin reopens on the old one.

It is not FIXED because Kodi offers no way to set the skin live without arming
the 10-second keep-skin countdown, and any non-Yes - including a DESTROYED
dialog - reverts (`ApplicationSkinHandling.cpp:394-401`). That is the mechanism
that corrupted atv2 to stock on 2026-07-17. A boot-time re-assert was evaluated
and REJECTED for the same reason: it moves an unanswerable dialog to a boot
screen, where defect B proves dialogs get torn down.

Shipped in 2026.07.19.0: **detect and report.** The restore records the
archive's skin in the restore-check marker and the first boot after a restore
compares it to `xbmc.getSkinDir()`, reporting a mismatch. The restored skin's
own settings are NOT at risk on this path - the flush writes into the LIVE
skin's `addon_data` dir, so it cannot reach the restored skin's file.

**Accepted next-cycle design: terminate instead of `Quit`** so the shutdown
flush never runs at all, which closes this whole clobber class rather than
adding a fourth guard. Not implemented. Its one real hazard is sqlite journal
state at kill time and must be closed by proof, not argument.

**OPEN OWNER QUESTION:** the wrong-skin finding currently rides the locked
notification ("Something from the restore needs attention - open EZ
Maintenance++"), but the remedy is in Kodi's own Settings > Interface > Skin,
not in the add-on. Giving this finding its own on-screen line means adding to
the locked restore vocabulary, which is an owner decision.

### Historical record of the defect follows

## 🔴 was OPEN - Restore defect A: restored skin settings are clobbered

**Status: ROOT CAUSE CONFIRMED (empirically reproduced 2026-07-18). NOT FIXED.
No code changed. Fix plan PROPOSED, not approved.**

Restore correctly writes `addon_data/<skin>/settings.xml`, then `ui.restart()`
-> `Quit` -> `CApplication::Stop()` -> `g_SkinInfo->SaveSettings()` serializes
the PRE-RESTORE in-memory skin settings back over it. Restored skin settings are
destroyed on the way out.

- **NOT tvOS-only.** Reproduced on macOS/POSIX with no NSUserDefaults layer. It
  is silent on Fire TV only because the value there already matched. All seven
  boxes are latently affected.
- **`wiz.py:753-764` carries a FALSE docstring** claiming `Quit` skips the
  clean-shutdown flush. It does not. Fixing the code without correcting that
  comment leaves the trap armed.
- **This is instance number 4 of a named class.** Read
  `repo/docs/playbooks/kodi-settings-clobber.md` FIRST - its instance 1 is this
  exact file and owner, and the documented fix is BOTH mechanisms (in-memory set
  AND direct file write, reconciled), not one.
- **Do NOT re-propose an every-boot re-assert.**
  `repo/docs/plans/atv-every-boot-settings-reassert.md` was REJECTED by
  unanimous adversarial review 2026-07-08; its verdict holds the corrected fix
  that became `nsud.rewrite_userdata_xml`.

Full record, fix plan, task breakdown, acceptance criteria and references:
**`docs/restore-defects-2026-07-18.md`**

---

## 🟢 FIXED IN CODE, AWAITING DEVICE RUN - Restore defect B

**2026-07-18: trigger identified, REPRODUCED TWICE on the local Kodi bench, and
fixed. Code + tests are in; only the hardware gate remains.**

The trigger was never in EZM++. `skin.estuary7`'s `Home.xml:9` arms
`AlarmClock(t7bbuild,...,00:15)` on the first Home load of every boot; when the
menu is stale (exactly what a restore produces) the skinshortcuts rebuild ends in
`ReloadSkin()`, which destroys the entire window stack. Bench evidence:

```text
17:50:20.052  started alarm t7bbuild
17:50:20.140  Window Init (DialogSelect.xml)     <- EZM++ prompt opens
17:50:35.106  type=buildxml                      <- 15.05 s later
17:50:35.405  Window Deinit (DialogSelect.xml)   <- prompt DESTROYED, marker consumed
```

**Correction to the old record:** the `DialogSelect` (prompt 1 of 2) dies before
the keyboard ever opens. The ambiguity exists twice, not once - `dialog.select`
returns -1 for both Back and teardown, exactly as `isConfirmed()` is False for
both. Hardening only `_get_keyboard` would have left the likelier path broken.

What shipped: `_keyboard_result` returns `(confirmed, text)` without collapsing a
non-answer (`_get_keyboard` keeps its legacy sentinel contract for the three
existing callers); `prompt_devicename_after_restore` re-presents a non-answer
instead of advancing and carries half-typed text forward, bounded to
`_PROMPT_MAX_ATTEMPTS`; and `service._wait_skin_settled` waits past the skin's
deferred build before any prompt opens.

**Verified on the bench after the fix:** reload at `18:00:43.944`, prompt opened
`18:00:53.713` (9.8 s later, safely after), and survived 80 s until a scripted
quit. Full record: `docs/restore-defect-b-reproduced-2026-07-18.md`.

Remaining: the device-verification gate below.

---

## 🟢 CLEARED - device-verification gate for 2026.07.18.0

**Closed. Both device runs happened.** `verification/2026.07.18.0.json` carries
REAL evidence for both classes (`android`, bedroom Fire TV; `tvos`, atv2), as do
`2026.07.18.1` and `2026.07.18.2`. Nothing is owed on the 18.0 gate. Do not
re-run it.

The hole this entry was opened to close is also closed and should stay closed:
the branch had changed `nsud.py` and `wiz.py` (commits `2805f48`, `b162c03`)
while still declaring the RELEASED version `2026.07.17.7`.
`tools/verify_device.py` refuses to write an artifact when the box's version
differs from `addon.xml`, so identical version strings would have let a device
run certify branch code the box was not running. The version bump closed it, and
`2026.07.19.2` later added a per-class storage fingerprint so a fresh run of one
class cannot launder the other.

**The live gate is now on the CURRENT version, not 18.0.** Check
`addon.xml` against `verification/<version>.json` before assuming anything.
`verification/2026.07.19.3.json` carries a real `android` run and an owner-signed
`tvos` WAIVER: `xcrun devicectl` silently refuses to overwrite existing files, so
atv2 is stuck at `2026.07.19.2` and that release is itself the delivery mechanism
that unblocks it. **That waiver is explicitly temporary** - once atv2 updates from
the repository, run the real tvOS verification and REPLACE it with device
evidence. A waiver left in place after the box can be reached is a lie.

---

## 🔴 SEPARATE - EZM++ service ignores Kodi's shutdown abort

Surfaced on the bench 2026-07-18, not previously recorded, NOT part of defect B:

```text
error <general>: CPythonInvoker(2, .../service.py): script didn't stop in 5 seconds
```

Kodi asks the service to abort and kills it after 5 s. Likely a blocking call or
a `waitForAbort` gap in the maintenance loop. Reproducible on the bench. Do not
bundle into the defect B fix.

---

## 🔴 SUPERSEDED - Restore defect B: post-restore device-name prompt discards input

**Status: MECHANISM PROVEN, TRIGGER UNIDENTIFIED. NOT FIXED.** Affects BOTH
tvOS and Fire OS.

The device-name keyboard is torn down mid-typing by something external, and
`_get_keyboard` (`tools.py:482-490`) cannot distinguish "destroyed" from
"cancelled" - it returns the current name either way, so the flow silently
advances to the buffer prompt and discards what the user typed.

- **EZM++ sets NO timeout anywhere.** `grep -rn autoclose resources/lib/`
  returns nothing; `doModal()` is called bare. Do not go hunting for one.
- B1 (unambiguous keyboard result) and B2 (do not advance on a non-answer) can
  proceed on the proven mechanism alone.
- B3 (remove the teardown trigger) must NOT be guessed at. Leading hypothesis is
  that `_wait_kodi_ready` (`service.py:36-52`) returns on
  `Window.IsVisible(home)`, which is not boot-settled - skinshortcuts may still
  be rebuilding and `_maybe_resume_paused_pvr()` (`service.py:433`) just
  re-enabled the PVR client. **Unproven.** Needs a `kodi.log` from a real
  post-restore boot.

Full record: **`docs/restore-defects-2026-07-18.md`**

---

## 🔴 OPEN - release blocker held in the SIBLING hub repo

**Flagged 2026-07-18. Not previously restated here.**

`repo/docs/incident-2026-07-16-ezmpp-full-backup-was-not-full.md` (lines
177-189) is a SELF-DECLARED OPEN incident that gates releases of this add-on:

> no EZM++ release carrying the [backup] overhaul ships until both runs above
> pass. This incident stays OPEN until then.

The two outstanding runs are both owner-gated hardware runs:

1. **tvOS source:** full backup, then portability lint, then cross-restore onto
   one Fire TV, then `tools/verify_device.py --diff`.
2. **Fire TV source:** the same shape, including a wipe first.

Neither has been recorded as done. **Treat the backup/restore overhaul as
unreleasable until the owner runs these.** The incident also carries a
"verify the mem0 memory write of the NSUD rules landed" item; those rules do
appear in the session memory store, so that one is likely done but unticked.

**Related process gap, also unclosed:** a hardware-verification gate on this
add-on's RELEASE CHECKLIST is requested by three separate incident writeups in
the hub repo (`incident-2026-07-08-ezmpp-repeated-hardware-burns.md:100`,
`incident-2026-07-08-ezmpp-atv-settings-nsuserdefaults.md:65`, and implicitly
the 2026-07-17 Estuary 7 menu-refresh incident). This repo has a MECHANICAL
gate (`tests/test_storage_change_requires_device_verification.py`). The
CHECKLIST/process half appears never to have landed. Roughly ten older EZM++
incident writeups in the hub each end with an uncaptured "verify on real
hardware" step; the two runs above would satisfy most of them at once.

---

## 🟡 QUEUE - investigated, unshipped

**`docs/next-update-candidates.md`** is the forward queue. Summary:

1. **Video cache `readfactor` - DO NOT RAISE.** Investigated 2026-07-18 by three
   independent agents. It is a throttle, not a booster; it is absent from the
   stall path; 842 of the fleet's channels are realtime-bound live TS where
   fill-ahead is structurally impossible; and there is no recorded playback
   complaint anywhere in the project. Also confirmed: EZM++ deleting a stale
   `advancedsettings.xml` `<cache>` block is CORRECT on Omega (that section no
   longer exists) and must not be "fixed." Recorded so the question is not
   re-opened from scratch.
2. **`memorysize` is set to values Kodi does not offer** (200 / 166; the list is
   16/20/24/32/48/64/96/128/192/256/384/512/768/1024). They ARE honored - Kodi
   skips validation when a dynamic options filler is present, verified in
   `Setting.cpp` - so the retune works. But opening that setting in Kodi's own
   GUI may snap it to a listed value. Candidate: snap `_recommended_mb()` to the
   nearest listed value. NOT agreed.
3. **Credentials in cleartext inside the backup zips - OWNER DECISION OPEN.**
   Both current base images carry live Real-Debrid, Easynews, Trakt, mdblist and
   TMDB secrets in `addon_data/plugin.video.pov/settings.xml`. Those zips sit
   under `~/Kodi/Backup/` on the mini, NFS-exported to every box. Not a coding
   error - a consequence of "full means full" plus POV's cleartext storage plus
   the export. Raised 2026-07-18, no action taken, export scope not verified.
4. **Stage D deferral** - `buildInstaller` / `BUILDS` / `install_build` removal,
   deferred to a separate PR (`docs/ui-consistency-plan.md:4-5, 18, 242`).

---

## ⚠ INLINED DOCTRINE - the settings-clobber class

> **Inlined deliberately.** The authoritative copy lives in the SIBLING repo
> (`tony7bones/tony7bones.github.io`, local checkout `~/Code/moquette/kodi/repo`,
> file `docs/playbooks/kodi-settings-clobber.md`). That is a DIFFERENT git repo:
> if you cloned `moquette/ezmaintenanceplusplus` on its own, those paths do not
> exist for you. The essentials are reproduced here so this add-on's most
> expensive lesson cannot go missing. If you can reach the playbook, read it -
> it is authoritative and more complete.

**The class:** a live Kodi component (the skin engine, a PVR client, core) holds
its settings IN MEMORY and flushes them to disk at a lifecycle event (clean
shutdown, client teardown, skin switch). Any naive interaction with the on-disk
file races that flush, in one of two directions:

1. **Your direct file write is CLOBBERED** - the live component later flushes its
   stale in-memory values back over your file.
2. **Your in-memory set is LOST** - the flush that would persist it never happens
   (e.g. a first boot that never reaches a clean shutdown).

This bit the project THREE separate times before it was named, and Defect A above
is the fourth.

**Mechanism A - the owner is always live and cannot be disabled (the skin):**
use the in-memory API (`Skin.SetBool` / `Skin.SetString`) when a clean shutdown is
guaranteed to follow; write the file directly when it is not; and when the write
happens while the owner is live, **do BOTH and reconcile**. Writing while Kodi is
fully down is the degenerate safe case.

**Mechanism B - the owner can be disabled (a PVR client, any binary add-on):**
disable it (its teardown flushes ITS settings first, ending the race), settle
~1s, write the file(s), re-enable in a `finally`. The re-enable matters as much
as the disable: it forces a re-read so every later flush writes YOUR values.

**Decision guide.** Who holds this setting in memory while Kodi runs? Nobody ->
just write the file. The skin -> Mechanism A. A disableable add-on -> Mechanism
B. When does that owner flush? Clean shutdown only -> an in-memory set survives
an orderly restart but is LOST on a first boot. Will the box reach a clean
shutdown after your write? If not guaranteed, never rely on an in-memory set
alone.

**Separately (tvOS only):** on Apple TV, Kodi vectors `userdata/*.xml` into
NSUserDefaults (~500 KB budget); reads check the KEY FIRST and fall back to disk
only when no key exists, so a key SHADOWS the disk file and nothing ever copies a
key back to disk. The fix is to write the file THROUGH `xbmcvfs` so `CTVOSFile`
vectors it (this add-on's `nsud.rewrite_userdata_xml`). That is a DIFFERENT
mechanism from the in-memory clobber above, with the same "my write was silently
undone" smell. Do not conflate them.

---

## Where the rest of the history lives

**These live in the SIBLING hub repo** (`tony7bones/tony7bones.github.io`, local
checkout `~/Code/moquette/kodi/repo`), NOT in this repo. A standalone clone of
`moquette/ezmaintenanceplusplus` cannot reach them:

- `repo/docs/playbooks/kodi-settings-clobber.md` - the settings-clobber class
- `repo/docs/playbooks/ezm-restore-hardening.md` - 2026.07.07.x restore hardening
- `repo/docs/plans/atv-every-boot-settings-reassert.md` - REJECTED design + the
  corrected fix
- `repo/docs/incident-2026-07-*-ezmpp-*.md` - fifteen incident writeups
- `repo/.claude/skills/ezm-backup-doctor/SKILL.md` - triage guide
- `repo/.claude/skills/kodi-storage-map/SKILL.md` - per-OS file map
- `repo/TASKS.md` - the hub tracker (EZM++ sections there are historical: the
  repo extraction that already happened)

## Hard constraint

The office Fire TV at `192.168.7.162` is HANDS-OFF. Never adb, JSON-RPC, ping or
otherwise contact it without explicit per-instance owner permission. The bedroom
Fire TV at `192.168.7.84` is the sanctioned JSON-RPC target. Carry this
prohibition into any subagent prompt you write.
