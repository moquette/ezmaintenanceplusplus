# Restore defects, findings and fix plan (2026-07-18)

> **SUPERSEDED AS STATUS, PRESERVED AS DIAGNOSIS (banner added 2026-07-19).**
> Everything below was written BEFORE the fixes and states "NOT FIXED". That is
> no longer true and this document is no longer the status of anything. Both
> defects are FIXED IN CODE: defect A in `be31322` (`_apply_skin_settings`,
> `wiz.py:869`), defect B after its trigger was reproduced on the local bench
> (the trigger was in `skin.estuary7`, not in EZM++). A3, `lookandfeel.skin`
> itself, remains OPEN BY DESIGN. **`TASKS.md` is the status.** Read on for the
> evidence and reasoning, which are still accurate and still worth having.
>
> Two specific traps if you read further: the `wiz.py:753-764` "false docstring"
> cited below NO LONGER EXISTS (it was corrected; the current text at
> `wiz.py:979-981` is true), and defect B's trigger is NOT unknown any more.

**ORIGINAL STATUS LINE (historical): DIAGNOSED, NOT FIXED. NO CODE HAS BEEN CHANGED.**
**The fix plan below is PROPOSED and has NOT been approved by QA or the architect.**

Two defects were found while investigating why a full restore of the ATV base
image did not bring up POV search on `atv1`. Defect A's root cause is confirmed
empirically. Defect B is partially diagnosed: the mechanism is proven, the
trigger is not.

Read this before touching `wiz.py`, `tools.py`, `ui.py`, `_kodisettings.py`, or
`service.py`.

---

## Defect A: restore silently loses skin settings (ROOT CAUSE CONFIRMED)

### Symptom (Defect A)

Owner restored `tvos/base_202607180333.zip` to `atv1`. After the restart, the
Home "Search" item prompted to install a global search mod instead of showing
POV's four search entries. The owner had to open skin settings and toggle
"Enable POV search" on by hand.

### The backup was NOT at fault

Verified inside `tvos/base_202607180333.zip`:

| Check         | Location                                         | Value                                        |
| ------------- | ------------------------------------------------ | -------------------------------------------- |
| Active skin   | `userdata/guisettings.xml`                       | `skin.estuary7`                              |
| POV toggle    | `userdata/addon_data/skin.estuary7/settings.xml` | `use_pov_search` = `true`                    |
| POV installed | `addons/plugin.video.pov/`                       | present, v6.07.07                            |
| POV enabled   | `userdata/Database/Addons33.db`                  | `plugin.video.pov` enabled=1                 |
| Manifest      | `backup_manifest.json`                           | 6307 entries, `failed: []`, source_os `tvos` |

The skin gates POV search on
`Skin.HasSetting(use_pov_search) + System.AddonIsEnabled(plugin.video.pov)`
(see `estuary7/tools/skin_transforms.py`, `_POV_SEARCH_ON`). POV was enabled, so
the only leg that could be false was `Skin.HasSetting(use_pov_search)`. The
value was correct in the archive and wrong in Kodi's live view after restore.

Owner confirmed they restored the top-level `tvos/` image, NOT the older
`tvos/base/base_202607171629.zip` (which does carry `use_pov_search=false`). The
wrong-image explanation is ruled out.

### Root cause

`ui.restart()` is not a force-quit.

```python
def restart():
    """Restart Kodi the only way that actually takes a restore/wipe live."""
    xbmc.executebuiltin("Quit")
```

`resources/lib/modules/ui.py:546-548`

`Quit` posts `TMSG_QUIT`, which runs `CApplication::Stop()`. Inside `Stop()`:

- `application/Application.cpp:2130-2131` - `CSettings::Save()` (guisettings.xml)
- `application/Application.cpp:2139-2141` - `if (g_SkinInfo != nullptr) g_SkinInfo->SaveSettings();`

`g_SkinInfo->SaveSettings()` is `CAddon::SaveSettings` (`addons/Addon.cpp:375-396`),
which serializes the **in-memory** `m_settings` to
`special://profile/addon_data/<skin>/settings.xml`.

Sequence on the box:

1. Restore extracts `addon_data/skin.estuary7/settings.xml` (`wiz.py:1969`).
2. On tvOS, `nsud.rewrite_userdata_xml` (`wiz.py:1231`) vectors it into the
   durable NSUserDefaults key, then removes the POSIX copy
   (`nsud.py:270-275`). At this instant the restored value is live and correct.
3. `ui.ask_restart` -> `restart()` -> `Quit`.
4. `CApplication::Stop` serializes the **pre-restore in-memory** skin settings
   over the restored file/key. `use_pov_search` was never in memory, so it is
   absent from the flush.
5. Reopen: `Skin.HasSetting(use_pov_search)` is false. Stock search item runs.

### Empirical proof (macOS bench, 2026-07-18)

Reproduced on a clean profile, Kodi 21.3 Omega, `skin.estuary7` live, on macOS
(plain POSIX, NO tvOS NSUserDefaults layer):

| Condition    | Action                                                | Result                                                  |
| ------------ | ----------------------------------------------------- | ------------------------------------------------------- |
| Kodi RUNNING | inject `use_pov_search=true` on disk, then clean quit | **DESTROYED** - file ends with Kodi's in-memory `false` |
| Kodi STOPPED | inject `use_pov_search=true` on disk, then boot       | **SURVIVED** - live value reads `true`                  |

The write is valid. The clean-shutdown flush is what destroys it.

### This is NOT tvOS-only

The bench run was on POSIX with no key layer, so the mechanism is
platform-independent. The reason it has only ever been _seen_ on Apple TV:

**The clobber is SILENT whenever the pre-restore in-memory value already equals
the restored value.** Both fireos base images carry `use_pov_search=true` and
those boxes were already POV-on, so the flush wrote `true` over `true`. The ATV
was going `false` -> `true`, so it regressed visibly.

All seven boxes are latently affected. Any restore that CHANGES a skin setting
will hit this on any platform.

The tvOS split is an amplifier, not the cause: because
`nsud.rewrite_userdata_xml` deletes the POSIX copy after a confirmed vector, the
key is the only copy, so there is no disk fallback to recover from.

### The false premise that shipped it

`wiz.py:753-764` states:

> `"restart" is a FORCE-QUIT (RestartApp is desktop-only; ask_restart only
quits), which skips Kodi's clean-shutdown settings flush - so the reopen boots
whatever value is LAST on disk`

This is wrong. `RestartApp` being desktop-only means `Quit` does not RELAUNCH.
It does not mean `Quit` skips `CApplication::Stop`.

The same add-on already states the correct behavior in two places:

- `_kodisettings.py:104-105` - "...updates only Kodi's in-memory store, which is
  flushed to guisettings.xml on a CLEAN shutdown"
- `tools.py:205-210` - "a full restore's extracted settings.xml + Kodi's
  in-memory-settings clobber make setSetting unreliable here"
- `CLAUDE.md`, PVR pause rule - "Without the pause, the live client flushes stale
  in-memory instance settings over the restored files at the next clean
  shutdown (hardware-proven, kodi-settings-clobber.md)"

Three correct statements and one wrong one. The restore path acted on the wrong
one. **Fixing the code without correcting that docstring leaves the trap armed.**

### Existing guards, and the gap

| Restored file                                       | Guard                                                                                                                 | Status     |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ---------- |
| `guisettings.xml`                                   | `apply_guisettings` re-applies every value into live memory via `Settings.SetSettingValue` (`_kodisettings.py:65-98`) | protected  |
| `addon_data/pvr.iptvsimple/instance-settings-*.xml` | bounded disable/re-enable window (`wiz.py:1038-1072`, `:1270-1278`)                                                   | protected  |
| `addon_data/<skin>/settings.xml`                    | **none**                                                                                                              | **BROKEN** |

There is no `Skin.SetBool` / `Skin.SetString` re-apply anywhere in
`resources/lib/modules/`.

### Blast radius (unverified beyond Defect A itself)

Ordered by user-visible impact. Everything here is flushed from memory on clean
shutdown and has no live re-apply guard:

1. `addon_data/<active skin>/settings.xml` - BROKEN NOW, this defect.
2. `guisettings.xml` `lookandfeel.skin` - `_BOOT_STATE_ONLY` (`_kodisettings.py:62`)
   deliberately EXCLUDES `lookandfeel.skin` from the live re-apply, and
   `_apply_boot_skin` writes it expecting no flush. `CSettings::Save()` then
   stamps the live skin over it. **This is very likely the real cause of the
   "reopens on the previous skin" flakiness currently attributed to the
   keep-skin dialog.** Not yet confirmed.
3. `profiles.xml` - in-memory in `CProfileManager`, saved on shutdown. No guard.
4. `sources.xml` - `CMediaSourceSettings` in-memory. No guard. NFS/SMB shares
   reverting.
5. `favourites.xml`, `RssFeeds.xml`, `mediasources.xml` - same class, lower impact.
6. `addon_data/<id>/settings.xml` for any other add-on with a live service and
   settings loaded - same exposure, unguarded.

Secondary mechanism in the same class: `CSkinSettingUpdateHandler::OnTimeout` ->
`m_addon.SaveSettings()` (`addons/Skin.cpp:912-915`) fires on a timer after ANY
skin setting is touched. So even without a restart, any skin-settings
interaction after a restore flushes the stale in-memory set over the restored
values.

---

## Defect B: post-restore device-name prompt discards input (MECHANISM PROVEN, TRIGGER UNKNOWN)

### Symptom (Defect B)

Owner-reported, on BOTH tvOS and Fire OS. On the first boot after a restore, the
device-name prompt appears. Part-way through typing a new name the keyboard
closes on its own and the flow jumps to the next prompt. The name cannot be
entered.

### The prompt sequence

1. `prompt_devicename_after_restore` (`tools.py:421-461`) - "Finish setup (1 of 2):
   Device name" -> `dialog.select` (Rename / Keep) -> on Rename, `_get_keyboard`.
2. `prompt_buffer_after_restore` (`tools.py:380-417`) - "Finish setup (2 of 2):
   Video quality" -> `dialog.select` with "Pick a different amount myself..." ->
   `advancedSettings()`. This is the "advanced values" prompt in the report.

Driven by `prompt_after_restore` (`tools.py:464-479`), called from
`_maybe_prompt_after_restore` (`service.py:162-182`).

### RULED OUT: EZM++ does not set any timeout

`grep -rn autoclose resources/lib/` returns **nothing**. The keyboard call is bare:

```python
keyboard = xbmc.Keyboard(default, heading, hidden)
keyboard.doModal()          # no autoclose arg; Kodi default 0 = never auto-close
```

`tools.py:486-487`

Neither `dialog.select` passes `autoclose` either. Something EXTERNAL is
destroying the modal. Do not go looking for a timeout constant in this add-on;
there isn't one.

### PROVEN DEFECT (independent of the trigger)

`_get_keyboard` (`tools.py:482-490`) cannot distinguish "user cancelled" from
"dialog was destroyed":

```python
if keyboard.isConfirmed():
    return unicode(keyboard.getText())
return cancel               # cancel defaults to the CURRENT name
```

When the modal is torn down mid-typing it returns the current name, so
`new == cur`, so `prompt_devicename_after_restore` returns False and the flow
**silently advances** to prompt 2 of 2. Half-typed input is discarded and
treated as consent to keep the old name. No error, no retry, no log line.

This is a real defect on its own and is worth fixing even if the trigger is
never identified.

### LEADING HYPOTHESIS for the trigger (UNPROVEN)

`_wait_kodi_ready` (`service.py:36-52`) returns as soon as
`Window.IsVisible(home)` is true. Home being visible is not the same as boot
being settled:

- skinshortcuts may still be rebuilding the menu (a skin reload destroys open
  modals)
- `_maybe_resume_paused_pvr()` (`service.py:433`) has just re-enabled
  `pvr.iptvsimple` a few lines earlier, so the PVR manager is starting up

Either would tear down an open modal, and neither is platform-specific, which
fits the both-OSes report.

**This is a hypothesis. It has not been confirmed.** Confirming it needs a
`kodi.log` from a real post-restore boot, timestamping the dialog teardown
against a skin reload or PVR startup.

---

## PRIOR ART - read before designing anything (added after a full docs sweep)

**Defect A is NOT a new discovery. It is instance number 4 of a named,
documented bug class, and the project already has a decision guide for it.**

`repo/docs/playbooks/kodi-settings-clobber.md` names the class: a live Kodi
component holds settings in memory and flushes them at a lifecycle event, so
either (1) your direct file write is clobbered by the later flush, or (2) your
in-memory set is lost because the flush never happens. It records three prior
instances. **Instance 1 is our exact file and owner:**
`addon_data/skin.estuary/settings.xml`, live owner the active skin, observed
failure "direct write clobbered on shutdown; `Skin.SetBool` alone lost on a first
boot (no clean shutdown ever happens)."

**The documented fix for instance 1 is BOTH mechanisms, not one:**
`Skin.SetBool` (survives a clean shutdown) AND a direct `settings.xml` merge
(survives a first boot), with reconciliation. See that playbook's "Mechanism A"
and its decision guide (lines 26-76).

This materially corrects proposal A1 below. The restore already performs the
direct file write (the extract). The missing half is the in-memory re-apply. A1
is therefore **completing a documented both-ways pattern, not inventing a
guard** - and it must not be shipped as an in-memory set ALONE, because the
playbook records that failing on a first boot.

**Also read `repo/docs/plans/atv-every-boot-settings-reassert.md`.** An
every-boot re-assert design was REJECTED on 2026-07-08 by unanimous adversarial
review (QA plus two architects). Do not re-propose it. That document's verdict
section also records the corrected fix that became `nsud.rewrite_userdata_xml`
(write restored userdata xml THROUGH `xbmcvfs` so it vectors into
NSUserDefaults with `synchronize=true`, durable regardless of quit
cleanliness). Its "Kill 1" argument is directly relevant to any re-apply
proposal: re-asserting BASE-BACKUP values repeatedly will fight the user's own
later edits. A one-shot re-apply at restore time avoids that; a recurring one
does not.

**Also read `repo/docs/playbooks/ezm-restore-hardening.md`** for the restore
path's existing hardening and the Fire OS text-renderer crash lesson.

### A conflict this sweep surfaced - resolve it, do not assume

Blast-radius item 2 below claims `lookandfeel.skin` is clobbered by
`CSettings::Save()`. **`kodi-settings-clobber.md` lines 90-93 explicitly say the
opposite**: that `lookandfeel.skin` reverting is NOT this class, but the "Keep
this skin?" safety timeout (`kodi-install-mechanics.md` section 13) - same smell,
different mechanism. Both could be true (a UI timeout AND a settings flush), or
one analysis is wrong. **This is unresolved.** Do not act on item 2 until it is
settled with evidence.

---

## PROPOSED fix plan (NOT APPROVED - needs QA + architect sign-off)

### A1. Re-apply restored skin settings into live memory before restart

Mirror what `apply_guisettings` already does for guisettings. After the extract
and before `ui.ask_restart`, parse the restored `addon_data/<active
skin>/settings.xml` and push every value into Kodi's live memory via
`Skin.SetBool` / `Skin.SetString` (or the JSON-RPC equivalent), so the
shutdown flush writes the RESTORED values rather than stale ones.

**Constraints from the prior art above - these are not optional:**

- **Do not ship the in-memory set alone.** `kodi-settings-clobber.md` records
  that `Skin.SetBool` by itself was LOST on a first boot for this exact file and
  owner (instance 1). The documented fix is both-ways: in-memory set AND the
  direct file write, reconciled. The restore already does the file write, so
  this proposal supplies the missing half - but the pairing must be deliberate,
  not incidental.
- **Run it ONCE, at restore time.** Do not make it recurring. "Kill 1" in
  `atv-every-boot-settings-reassert.md` proves a repeated re-assert of
  base-backup values fights the user's own later edits and has no stable
  resting state.
- **On tvOS, respect the existing vector.** `nsud.rewrite_userdata_xml` already
  writes restored userdata xml through `xbmcvfs` (the corrected fix from the
  2026-07-08 review). Any new write must not strand or duplicate that layer.

Open question for the architect: this is the third bolt-on guard for the same
root cause. A general "re-apply restored state into live memory before restart"
pass may be the correct shape instead of a per-file patch. Weigh generalizing
over adding guard number three.

### A2. Fix the false docstring

Correct `wiz.py:753-764`. The claim that `Quit` skips the clean-shutdown flush
is false and is what caused this defect. Align it with `_kodisettings.py:104-105`.

### A3. `lookandfeel.skin` - RESOLVED 2026-07-19, shipped as detect-and-report

**The contest is settled, in favour of the flush.** `kodi-settings-clobber.md:90-93`
attributed the skin reverting to the "Keep this skin?" safety timeout and NOT to
this bug class. That is not the mechanism operating here. A three-arm bench
experiment on the local macOS Kodi (2026-07-19) proved it:

| Arm | What ran | Disk value after |
| --- | --- | --- |
| Clean quit (the restore path) | full `CApplication::Stop` | **pre-restore skin** - the write was destroyed |
| `SIGKILL` (negative control) | no flush | restored skin survived |
| Relaunch | boot reads disk | booted whatever disk held |

Log evidence from the clean quit: `Stopping the application...` then
`Saving settings` (`CSettings::Save`, guisettings from LIVE memory,
`Application.cpp:2131`) then `Saving skin settings` (`:2141`), with
`guisettings.xml`'s mtime matching `Saving settings` to the second. The negative
control rules out "the write never landed". Both mechanisms may exist, but the
flush alone is sufficient and is what fires here.

**Why it is not FIXED.** There is no add-on-reachable way to change the live skin
without arming the countdown: `OnSettingChanged` for `lookandfeel.skin` posts
`ReloadSkin` and appends `(confirm)` whenever `m_confirmSkinChange` is true
(`ApplicationSkinHandling.cpp:502-504`), which a Python service cannot clear. Any
outcome that is not an explicit Yes within 10 seconds reverts to the old skin
(`:394-401`) - and a DESTROYED dialog is one of those outcomes, which is exactly
defect B's proven mechanism. That is how atv2 was corrupted to stock on
2026-07-17. A one-shot next-boot re-assert was evaluated and REJECTED: it escapes
the 2026-07-08 every-boot objection but raises the same countdown on a boot
screen, where dialogs are demonstrably torn down.

**Shipped (2026.07.19.0): C2, detect and report.** `mark_restore_check_pending`
records the archive's skin; the first boot after a restore compares it to
`xbmc.getSkinDir()` (the read-only probe - `Skin.HasSetting`/`GetInfoBooleans`
MUTATE) and reports a mismatch through the existing needs-attention line.

**`_BOOT_STATE_ONLY` correctly continues to exclude `lookandfeel.skin`.** On the
mismatch path the flush writes into the LIVE skin's `addon_data` dir
(`Application.cpp:2141`, `Addon.cpp:375-396`), so it cannot reach the restored
skin's settings file - they survive intact on disk. This is also why
`_apply_skin_settings` returning `skipped:not-live` is correct rather than a hole.

**Accepted next-cycle design: C4** - terminate the process instead of `Quit` so
the flush never runs, closing this class outright. NOT implemented. Its hazard is
sqlite journal/WAL state at kill time and must be closed by proof.

### A4. Regression test

**TRAP - read this before writing the test.** Querying `Skin.HasSetting(<id>)`
over JSON-RPC **creates** the setting id in Kodi's memory with a default of
`false`, and it persists on the next flush. Observed live on the bench: the id
appeared in `settings.xml` without ever being written by the test. `GetInfoBooleans`
is NOT a read-only probe for skin settings; it mutates what it measures. A
regression test built on it will pass for the wrong reason.

Prefer asserting on the file contents after a simulated restore-plus-shutdown,
using the existing two-layer fake (`tests/fake_kodi_sandbox_io.py`), rather than
a live JSON-RPC probe.

### B1. Make the keyboard result unambiguous

Distinguish confirmed / cancelled / destroyed in `_get_keyboard` instead of
collapsing all three to `cancel`. A torn-down dialog must not be read as
consent.

### B2. Do not advance on a non-answer

The device-name step should re-present rather than fall through to prompt 2 when
the dialog was destroyed without an answer. Owner requirement, stated directly:
the prompt should sit there until the user answers it.

### B3. Identify and remove the teardown trigger

Pull a `kodi.log` from a real post-restore boot. If the hypothesis holds,
`_wait_kodi_ready` needs a stronger settled-boot condition than
`Window.IsVisible(home)` (for example: also require that skinshortcuts is not
running and the PVR manager has finished starting), or the prompt needs to be
deferred until the box is genuinely idle.

---

## THE TASK FOR THE NEXT AGENT

Do these in order. Do not skip the gates.

1. **Read first.** This document, `CLAUDE.md` (backup/restore contract and tvOS
   storage rules), and `repo/docs/playbooks/kodi-settings-clobber.md`. Defect A
   is instance number 4 of the class that playbook already names; its decision
   guide answers this correctly and the restore path simply never applied it to
   the skin.

2. **Do not re-derive Defect A.** Root cause is confirmed empirically. Do not
   spend budget re-proving it. If you want to see it yourself, the bench recipe
   is in the "Empirical proof" section above and takes about five minutes on any
   desktop Kodi.

3. **Get the fix plan approved before writing code.** The owner requires
   independent QA agent and architecture agent review before any phase is
   declared done. Self-verification is never sufficient. Send them the PROPOSED
   plan above, including the A1 open question about generalizing versus a third
   bolt-on guard.

4. **Confirm Defect B's trigger before fixing B3.** B1 and B2 can proceed on the
   proven mechanism alone. B3 must not be guessed at; get the log.

5. **Confirm or drop the blast-radius items.** Items 2 through 6 are reasoned
   from the same mechanism but are NOT individually verified. Do not report them
   as confirmed defects without testing each.

6. **Gates before any release.** All of these, in this order:
   - `/opt/homebrew/bin/python3 -m pytest tests/ -q` green (system python3 is
     3.9, too old)
   - `ruff check tests/ tools/` clean
   - adversarial QA review
   - **real-device verification.** A change touching restore/storage behavior
     requires a fresh `verification/<version>.json` pulled live over JSON-RPC
     from a real box, per
     `tests/test_storage_change_requires_device_verification.py`. "Fixed in
     code" is not a claim this add-on may make. This gate is owner-gated and
     needs a box the owner authorizes.
   - `tools/release.sh` here, THEN bump the hosted `addon.xml` in
     `tony7bones.github.io` and ship via `python3 _tools/release.py --proxy`. A
     version bumped here alone is not live on any box.

7. **Hard constraint.** The office Fire TV at `192.168.7.162` is HANDS-OFF. Never
   adb, JSON-RPC, ping, or otherwise contact it without explicit per-instance
   owner permission. The bedroom Fire TV at `192.168.7.84` is the sanctioned
   JSON-RPC target. Carry this prohibition into any subagent prompt you write.

### Acceptance criteria

- A restore that CHANGES a skin setting leaves the restored value live after the
  restart, on both a Fire TV and an Apple TV.
- Specifically: restoring an image with `use_pov_search=true` onto a box whose
  current value is `false` results in POV search being active after the restart,
  with no manual toggle.
- The device-name prompt cannot be dismissed by anything other than a user
  answer. Half-typed input is never silently discarded.
- The false docstring at `wiz.py:753-764` is corrected.
- A regression test covers Defect A and does NOT use `GetInfoBooleans` as its
  probe.
- Device verification artifact present for the shipped version.

### What was NOT done in the 2026-07-18 session

- No code changed. No build cut. No release.
- Fix plan not reviewed by QA or the architect.
- Defect B trigger not identified.
- Blast-radius items 2 through 6 not individually verified.
- The Fire TV prediction (that Defect A affects Fire OS equally) is proven by
  the POSIX bench run but was NOT demonstrated on an actual Fire TV.

---

## References

### Project doctrine (read these first - they predate and govern this writeup)

> **These live in the SIBLING hub repo** (`tony7bones/tony7bones.github.io`,
> local checkout `~/Code/moquette/kodi/repo`). They are NOT in this git repo. If
> you cloned `moquette/ezmaintenanceplusplus` standalone, the `repo/...` paths
> below do not resolve for you - the essentials of the most important one are
> inlined in this repo's `TASKS.md`.

| Document | Why it matters here |
| --- | --- |
| `repo/docs/playbooks/kodi-settings-clobber.md` | **Names the bug class.** Instance 1 (lines 20-24) is this exact file and owner. Mechanism A + decision guide, lines 26-76. Lines 90-93 contest blast-radius item 2. Lines 95-111 cover the tvOS NSUserDefaults model. |
| `repo/docs/plans/atv-every-boot-settings-reassert.md` | **REJECTED design, do not re-propose** (verdict lines 133-160). Kill 1 (lines 137-146) constrains any re-apply proposal. The corrected fix that became `nsud.rewrite_userdata_xml` is at lines 171-199, with open caveats at 187-199 (500 KB NSUserDefaults budget, PVR clobber orthogonality, hardware verification still owed). |
| `repo/docs/playbooks/ezm-restore-hardening.md` | The 2026.07.07.x restore hardening, incl. the Fire OS 8 text-renderer SIGSEGV caused by per-file progress text. Any change to restore progress UI must respect it. |
| `~/Code/moquette/kodi/.claude/skills/kodi-storage-map/SKILL.md` | Exhaustive per-OS file map; section 5 covers the key-is-only-copy state. |
| `~/Code/moquette/kodi/.claude/skills/ezm-backup-doctor/SKILL.md` | Triage guide for backup/restore failures. |
| `ezmpp/CLAUDE.md` | The backup/restore contract, tvOS storage rules, and the three mechanical guards (chokepoint lint, two-layer fake, device-verification gate). |
| `ezmpp/docs/next-update-candidates.md` | The forward queue: cache/readfactor investigation, memorysize GUI-list hazard, the credential-exposure decision. |

### Related incident record (same add-on, same subsystem)

`repo/docs/` carries the burn history. Most directly relevant:

- `incident-2026-07-08-ezmpp-atv-settings-nsuserdefaults.md`
- `incident-2026-07-08-ezmpp-tvos-restore-duplicate-userdata.md` - why the POSIX copy is dropped after a confirmed vector
- `incident-2026-07-14-ezmpp-restore-wiped-custom-menu-tvos.md`
- `incident-2026-07-16-ezmpp-full-backup-was-not-full.md` - origin of the "full means full" contract
- `incident-2026-07-07-ezmpp-wrong-device-buffer-after-restore.md` - why the post-restore buffer prompt exists at all (Defect B's flow)
- `incident-2026-07-07-ezmpp-dishonest-restart-prompt.md` - prior art on restart-prompt honesty
- `incident-2026-07-08-ezmpp-repeated-hardware-burns.md` - why "fixed in code" is not a claim this add-on may make
- `repo/docs/agent-postmortem-do-not-repeat.md`

### Kodi Omega source (github.com/xbmc/xbmc, branch `Omega`)

| Claim | Citation |
| --- | --- |
| `Quit` runs `CApplication::Stop` | `xbmc/interfaces/builtins/ApplicationBuiltins.cpp:75-77`, registered `:257` |
| `CSettings::Save()` on shutdown | `xbmc/application/Application.cpp:2130-2131` |
| `g_SkinInfo->SaveSettings()` on shutdown - the clobber | `xbmc/application/Application.cpp:2139-2141` |
| Serializes in-memory `m_settings` to the addon_data path | `xbmc/addons/Addon.cpp:375-396`, path at `:606` |
| Skin-settings timer flush (clobber without a restart) | `xbmc/addons/Skin.cpp:912-915` |
| tvOS vectoring predicate `WantsFile` | `xbmc/platform/darwin/tvos/filesystem/TVOSFile.cpp:39-45`, key translation `TVOSNSUserDefaults.mm:27-52`, `:216-226` |
| tvOS file factory branch | `xbmc/filesystem/FileFactory.cpp:116-118` |

### EZ Maintenance++ source

| Claim | Citation |
| --- | --- |
| `restart()` is `Quit`, not a force-quit | `resources/lib/modules/ui.py:546-548` |
| The FALSE premise | `resources/lib/modules/wiz.py:753-764` |
| Correct statements contradicting it | `resources/lib/modules/_kodisettings.py:104-105`, `tools.py:205-210` |
| Extract writes the skin settings file | `resources/lib/modules/wiz.py:1969` |
| tvOS vector on restore | `resources/lib/modules/wiz.py:1231`, `nsud.py:104-130`, `:171`, `:270-275` |
| Stale-key purge runs first | `resources/lib/modules/wiz.py:1209-1211` |
| guisettings live re-apply (the working guard) | `resources/lib/modules/_kodisettings.py:65-98`, exclusion list `:62` |
| PVR pause window (the other working guard) | `resources/lib/modules/wiz.py:1038-1072`, `:1270-1278` |
| Defect B prompt sequence | `resources/lib/modules/tools.py:421-461`, `:380-417`, `:464-479` |
| Defect B proven conflation | `resources/lib/modules/tools.py:482-490`, keyboard call `:486-487` |
| Boot service invocation and readiness gate | `service.py:162-182`, `:36-52`, PVR resume `:433` |
| Restore verification probe dirs | `resources/lib/modules/restorecheck.py:40-43` |

### Skin

`estuary7/tools/skin_transforms.py` - `_POV_SEARCH_ON` (the gate), `_POV_SEARCH_ITEMS`,
`_edit_searchdialog` (stock item `InstallAddon(script.globalsearch)` is the "global
search mod" prompt observed). Test: `estuary7/tests/test_baked_defaults.py:181-206`.

### Bench environment used for the empirical proof

Kodi `21.3 (21.3.0) Git:20251031-a3a448d26b` on macOS, throwaway profile, skin.estuary7
1.0.31 live (older than the fleet's 1.0.66; irrelevant because the mechanism is Kodi core,
not skin code). Profile and artifacts deleted after the run; the owner's own profile was
restored byte-identically.
