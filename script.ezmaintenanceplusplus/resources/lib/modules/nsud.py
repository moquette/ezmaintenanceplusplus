"""Make a restore stick on Apple TV (tvOS) by re-writing the restored userdata *.xml
THROUGH xbmcvfs, so Kodi vectors each file into NSUserDefaults.

Why this exists (root cause, verified in Kodi's tvOS source `xbmc/platform/darwin/tvos/`):
tvOS gives an app ~500 KB of normal app-directory storage, so Kodi stores `userdata/*.xml`
in the app's NSUserDefaults and rewrites the on-disk files from that mirror on launch. A
`.xml` write THROUGH xbmcvfs is dispatched to `CTVOSFile::Write` ->
`CTVOSNSUserDefaults::SetKeyDataFromPath(..., synchronize=true)` -> `[NSUserDefaults
synchronize]`, i.e. persisted to the only durable tvOS store BEFORE the call returns, with
no dependency on a clean shutdown. But the restore extracts with plain Python `zipfile`
(`zin.extract`) = a plain POSIX write that BYPASSES CTVOSFile, so the restored files never
reach NSUserDefaults and are shadowed by the stale mirror at boot. Re-writing each restored
`.xml` through xbmcvfs here dissolves that shadow. On Fire TV / desktop the same call is a
harmless rewrite of identical bytes.

This module is deliberately IPTV-free. It does NOT enable, disable, stage, probe, or in any
way manage pvr.iptvsimple - a restore must never touch the IPTV client. It only re-writes
restored userdata *.xml so tvOS keeps them.

Hard rules (see docs/plans/atv-restore-*.md and the adversarial review that produced them):
- SINGLE write per file, NEVER chunk. `CTVOSFile::Write` REPLACES the whole NSUserDefaults
  key on every call, so a chunked loop would leave only the last chunk -> a truncated XML
  fragment -> settings reset to defaults, unrecoverable (worse than the shadow bug). Read
  the whole file with plain `open()` (per kodi-vfs-cannot-read-foreign-local-files.md the
  READ must be plain, never xbmcvfs), write it in ONE `xbmcvfs.File.write` call, check the
  return.
- EXCLUDE this add-on's own settings.xml (carries the SOURCE box's paths + Dropbox secret,
  and service.py int()-parses those at import -> would crash the boot service).
"""

import os

import xbmcvfs

ADDON_ID = "script.ezmaintenanceplusplus"

# Files (relative to userdata/, forward-slash) the general walk must NOT re-vector.
DEFAULT_EXCLUDES = (
    # Our own settings carry the source box's download/restore paths AND its
    # dropbox_refresh_token (a secret); service.py reads several at import with int(),
    # so a foreign/blank value would crash the boot service.
    "addon_data/%s/settings.xml" % ADDON_ID,
)


def _special_for(rel):
    """special:// path Kodi routes through CTVOSFile on tvOS (the /userdata key match)."""
    return "special://home/userdata/" + rel.replace("\\", "/")


def _is_tvos():
    """True ONLY on Apple TV (tvOS).

    This gate is a HARD safety boundary. On tvOS a write to
    ``special://home/userdata/<rel>`` is dispatched to ``CTVOSFile`` and vectored into
    NSUserDefaults, which is a SEPARATE entity from the POSIX disk file - so after the
    rewrite the file exists in BOTH layers and tvOS File Manager lists it twice. On EVERY
    other platform (Fire TV / Android / desktop) that same ``special://`` path IS the POSIX
    disk file, so the caller must NEVER remove the POSIX copy there - doing so would delete
    the file just written. Detected via Kodi's own platform condition; defaults to False
    (the safe answer) on any error, so the POSIX copy is only ever dropped when tvOS is
    positively confirmed.
    """
    try:
        import xbmc

        return bool(xbmc.getCondVisibility("System.Platform.TVOS"))
    except Exception:
        return False


def _vfs_rewrite_once(posix_src, special_dst):
    """Read the whole file with PLAIN python, write it in EXACTLY ONE xbmcvfs write.

    Returns True only on a confirmed write. On any failure returns False and leaves the
    POSIX source untouched (so the worst case is the pre-existing shadow, never data loss).
    NEVER chunk here (see module docstring) and NEVER reuse ui.py's _stream_copy/_LocalReader.
    """
    try:
        with open(posix_src, "rb") as fh:
            data = fh.read()
    except OSError:
        return False
    f = None
    try:
        f = xbmcvfs.File(special_dst, "w")
        ok = f.write(
            bytearray(data)
        )  # ONE call, full payload; check the boolean return
        return bool(ok)
    except Exception:
        return False
    finally:
        try:
            if f is not None:
                f.close()
        except Exception:
            pass


def _vector_confirmed(special_dst, posix_src):
    """Read the just-vectored file BACK through xbmcvfs (on tvOS this reads NSUserDefaults)
    and confirm it byte-matches the POSIX source. Returns True ONLY on a full, non-empty
    match.

    This is the safety gate for dropping the POSIX copy: ``xbmcvfs.File.write`` returning
    True is not proof the durable store actually holds the bytes. tvOS gives the app a tiny
    NSUserDefaults budget (the ~500 KB limit this whole mechanism exists for), so a write
    could report success while the store silently truncates or evicts the key. Deleting the
    POSIX copy then would lose the only good copy. Requiring a read-back match turns "trust
    the write bool" into "positive evidence the store holds the identical content" before any
    irreversible delete. Any read failure or mismatch -> False -> keep the POSIX copy."""
    try:
        with open(posix_src, "rb") as fh:
            expected = fh.read()
    except OSError:
        return False
    if not expected:
        return False
    f = None
    try:
        f = xbmcvfs.File(special_dst)
        got = f.readBytes()
        got = bytes(got) if got is not None else b""
    except Exception:
        return False
    finally:
        try:
            if f is not None:
                f.close()
        except Exception:
            pass
    return got == expected


def rewrite_userdata_xml(
    userdata_dir,
    exclude_rel=DEFAULT_EXCLUDES,
    exclude_dir_prefixes=(),
    log=None,
    drop_posix_on_tvos=True,
):
    """Re-write every *.xml under userdata_dir through xbmcvfs. Returns
    (written, skipped, failed). Fully guarded; never raises.

    `exclude_dir_prefixes` is a GENERIC opt-out (userdata-relative, forward-slash) with an
    empty default - no add-on is special-cased. It never singles out pvr.iptvsimple.

    tvOS duplicate-entry fix: on Apple TV the POSIX file the restore extracted and the
    NSUserDefaults key this write creates are TWO entities, so File Manager lists every
    userdata file twice (see the tvOS-restore-duplicate-userdata incident). After a
    CONFIRMED vector (``_vfs_rewrite_once`` returned True, i.e. the bytes are in
    NSUserDefaults), and ONLY when ``_is_tvos()`` positively confirms Apple TV, remove the
    now-redundant POSIX copy so only the coherent CTVOSFile/NSUserDefaults entity remains;
    Kodi re-materializes the disk file from NSUserDefaults on the next launch. This is
    ordered write-then-delete (never delete a file whose content is not already durably in
    NSUserDefaults) and is a strict no-op on Fire TV / Android / desktop, where the same
    ``special://`` path IS the POSIX file. ``drop_posix_on_tvos=False`` disables it entirely
    (kept as an escape hatch and for the pre-fix behaviour in tests)."""
    written = skipped = failed = dropped = 0
    excl = {x.replace("\\", "/") for x in exclude_rel}
    prefixes = tuple(p.replace("\\", "/") for p in exclude_dir_prefixes)
    drop = bool(drop_posix_on_tvos) and _is_tvos()
    try:
        for dirpath, _dirnames, filenames in os.walk(userdata_dir):
            for name in filenames:
                if not name.lower().endswith(".xml"):
                    continue
                posix = os.path.join(dirpath, name)
                rel = os.path.relpath(posix, userdata_dir).replace("\\", "/")
                if rel in excl or any(rel.startswith(p) for p in prefixes):
                    skipped += 1
                    continue
                special = _special_for(rel)
                if _vfs_rewrite_once(posix, special):
                    written += 1
                    # tvOS ONLY, and ONLY after a READ-BACK confirms NSUserDefaults holds the
                    # identical bytes: drop the redundant POSIX copy so File Manager stops
                    # listing the file twice. The disk file returns coherently on the next
                    # launch. Read-back (not just the write bool) guards the tvOS store
                    # silently truncating a large key. Guarded; a failed remove just leaves
                    # the (harmless) duplicate, never an exception.
                    if drop and _vector_confirmed(special, posix):
                        try:
                            os.remove(posix)
                            dropped += 1
                        except OSError:
                            pass
                else:
                    failed += 1
    except Exception:
        pass
    if log:
        log(
            "nsud: userdata xml re-write: %d written, %d skipped, %d failed, "
            "%d posix-dropped (tvOS)" % (written, skipped, failed, dropped)
        )
    return (written, skipped, failed)


def persist_one(rel, log=None):
    """Persist ONE already-on-disk userdata file (userdata-relative, e.g.
    "sources.xml") the tvOS-safe way, for a caller that just wrote it with plain
    POSIX and would otherwise leave it dual-layered on Apple TV.

    Same ordered write-through -> read-back -> drop-POSIX as
    ``rewrite_userdata_xml``, for a single file: vector it through xbmcvfs (->
    NSUserDefaults on tvOS) and, ONLY on tvOS and ONLY after a read-back confirms
    the store holds the identical bytes, drop the now-redundant POSIX copy so File
    Manager stops listing the file (and its contents) twice - the
    tvOS-restore-duplicate-userdata bug. A strict no-op rewrite of identical bytes
    on Fire TV / Android / desktop (there the special:// path IS the POSIX file).
    Returns True on a confirmed vector; guarded, never raises."""
    rel = (rel or "").replace("\\", "/")
    special = _special_for(rel)
    posix = xbmcvfs.translatePath(special)
    try:
        if not _vfs_rewrite_once(posix, special):
            if log:
                log("nsud.persist_one: vector failed for %s (POSIX stands)" % rel)
            return False
        if _is_tvos() and _vector_confirmed(special, posix):
            try:
                os.remove(posix)
            except OSError:
                pass
        if log:
            log("nsud.persist_one: persisted %s" % rel)
        return True
    except Exception:  # noqa: BLE001 - never abort the caller
        return False
