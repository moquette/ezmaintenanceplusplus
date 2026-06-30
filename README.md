# EZ Maintenance++

A fork of **EZ Maintenance+** (by aenema, peno) that makes Backup and Restore work
over Kodi's VFS, so the **Backup Location** and **Restore** folder can point straight
at a network share (`nfs://`, `smb://`) or any other VFS path, not just local storage.

## Why this fork exists

The original builds its backup zip with Python's `zipfile`, which can only open a
**local** filesystem path. Point its Backup Location at `nfs://host/share/...` and the
backup dies with `FileNotFoundError` because Python never sees Kodi's network layer.

EZ Maintenance++ keeps everything else identical and changes only the file I/O so it
goes through `xbmcvfs` (the same VFS the official "Backup" add-on uses):

- **Backup** builds the zip in `special://temp` (always local, where `zipfile` is happy),
  then `xbmcvfs.copy()`s the finished file to the configured destination and deletes the
  temp. A nfs/smb/any-VFS Backup Location now just works.
- **Backup cancel** cleanup uses `xbmcvfs.delete()` instead of `os.unlink()`.
- **Restore** lists the zip folder with `xbmcvfs.listdir()` and, for a remote zip,
  copies it to `special://temp` before extracting. So you can restore directly from a
  share too.

All of the original's other tools (cache clean, thumbnails, packages, log viewer,
speedtest, skin switch, the wizard) are unchanged.

## What changed (exactly)

Everything is in `resources/lib/modules/wiz.py`:

| Function        | Change                                                                                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `CreateZip`     | Stage the zip in `special://temp` when the destination is a VFS path (`://`), then `xbmcvfs.copy()` to the destination and remove the temp. |
| `backup`        | Cancel cleanup uses `xbmcvfs.delete(backup_zip)` (VFS-safe).                                                                                |
| `restoreFolder` | List the restore folder with `xbmcvfs.listdir()` instead of `os.listdir()`.                                                                 |
| `restore`       | Copy a remote (`://`) zip to `special://temp` before `ExtractZip`, then drop the temp.                                                      |

The add-on id was changed to `script.ezmaintenanceplusplus` so it installs alongside the
original without conflict. No behavior changes for local-path backups.

## Build the installable zip

```sh
./build.sh
```

Produces `dist/script.ezmaintenanceplusplus-<version>.zip`. Install it in Kodi with
**Add-ons -> Install from zip file** (you may need Settings -> System -> Add-ons ->
**Unknown sources** enabled first).

## Use it with a network share

1. Install the zip.
2. Open the add-on's settings -> set **Backup Location** to your share folder
   (the folder browser can reach network sources), e.g. an `nfs://` or `smb://` path.
3. Run a Backup. It stages locally, then lands on the share.
4. To restore: set the **Restore from Zip Location** to the same share folder and run Restore.

## Credit and license

Forked from **EZ Maintenance+** by **aenema** and **peno**. License is unchanged from the
upstream add-on (see `script.ezmaintenanceplusplus/addon.xml`). This fork only adds VFS
network-destination support.

**aenema** and **peno** authored the _original_ add-on only. They are not affiliated with,
and have not endorsed, this fork, and have not given permission for their names to be used
as its authors. They are therefore credited here as the upstream we forked, and are
deliberately kept out of the add-on's own `provider-name` (which lists only the fork
maintainer). This README credit is a factual statement of lineage, not a claim of authorship
or endorsement.
