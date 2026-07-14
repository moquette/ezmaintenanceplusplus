"""A tvOS-accurate fake of Kodi's App-Sandbox cross-layer read quirk.

WHY THIS EXISTS (and why it is SEPARATE from fake_kodi_storage.py)
-----------------------------------------------------------------
This models a DIFFERENT tvOS bug family from the NSUserDefaults shadow. The shadow is
about `userdata/*.xml` being vectored into NSUserDefaults (fake_kodi_storage.py). THIS is
about LOCAL and `special://temp` files - which are NOT under userdata, so CTVOSFile never
touches them - where Apple's App Sandbox scoped-resource behavior means:

    On tvOS, a local file must be READ back through the SAME layer it was WRITTEN through.

  - written THROUGH xbmcvfs -> readable through xbmcvfs (and Kodi's texture loader, which
    reads through the VFS) AND by plain open (the bytes are on disk).
  - written by plain open() -> readable by plain open(); but Kodi's VFS reads it EMPTY,
    while `xbmcvfs.Stat` still reports the real size. That asymmetry (size right, bytes
    empty) is what made the backup-size-mismatch bug take four releases to find.

On Android / desktop there is no such quirk: both layers cross-read freely.

TWO SHIPPED FIXES DEPEND ON THIS, IN OPPOSITE DIRECTIONS:
  - dropbox_remote._qr_image writes the QR THROUGH xbmcvfs so the texture loader can read
    it (2026.07.13.6 - the blank-barcode bug). Reverting it to plain open() -> blank on
    Apple TV.
  - ui._open_reader reads a LOCAL backup source with plain open(), NOT xbmcvfs, because the
    VFS reads a foreign local file empty (2026.07.04.2-.5 - the size-mismatch bug).
    Routing it through xbmcvfs -> empty reads, corrupt/short backups on Apple TV.

A blanket "always prefer xbmcvfs" breaks the second; a blanket "always prefer plain open"
breaks the first. The rule is ORIGIN-based, and this fake encodes exactly that so a change
in either direction fails a test instead of a device.

GROUND TRUTH
------------
docs/playbooks/kodi-vfs-cannot-read-foreign-local-files.md (hardware-confirmed on tvOS:
plain-open PNG blank on Apple TV, fine on Fire TV; verified by rollback). This fake matches
that observed behavior; if a future tvOS/Kodi changes it, re-confirm on a device and update
here - never guess.

CAVEAT (be honest about what is logged vs inferred): the ControlImage/QR bug's Android
no-quirk behavior IS hardware-confirmed (the barcode was fine on Fire TV). The foreign-local
READ bug's non-repro is logged for macOS Kodi, not Android specifically - so this fake's
"android reads cross-layer fine" for THAT direction is a reasonable architectural inference
(Android has no App-Sandbox scoped-resource restriction), not a literal device log. If a
Fire TV ever shows a short/empty local read, that assumption is what to re-check first.
"""

import os

__all__ = ["FakeSandboxFS", "make_modules"]


class FakeSandboxFS:
    """A real POSIX tree plus a registry of which files Kodi's VFS 'knows'.

    A file is 'known' to the VFS iff it was written THROUGH xbmcvfs.File(w). On tvOS, a
    VFS read of an unknown (plain-open-written) file returns empty, though Stat sees it.
    """

    def __init__(self, home, platform="tvos"):
        self.home = str(home)
        self.platform = platform  # "tvos" | "android"
        self.vfs_written = (
            set()
        )  # real paths Kodi's VFS produced -> it can read them back
        self.log = []

    def translate(self, path):
        p = str(path)
        for pfx, sub in (
            ("special://temp/", "temp"),
            ("special://profile/", "userdata"),
            ("special://userdata/", "userdata"),
            ("special://home/", ""),
        ):
            if p.startswith(pfx):
                return (
                    os.path.join(self.home, sub, p[len(pfx) :])
                    if sub
                    else os.path.join(self.home, p[len(pfx) :])
                )
        return p

    def _vfs_can_read(self, real):
        # tvOS: only files the VFS itself wrote are readable through the VFS. Everywhere
        # else, the VFS reads any on-disk file.
        if self.platform != "tvos":
            return True
        return real in self.vfs_written

    # -- plain POSIX side (what a plain open() / zipfile extract / a foreign writer does) --

    def posix_write(self, path, data):
        real = self.translate(path)
        d = os.path.dirname(real)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(real, "wb") as f:
            f.write(bytes(data))
        # A plain write makes the file NOT VFS-known (and un-knows it if the VFS knew it).
        self.vfs_written.discard(real)

    # -- helpers exposed to the fake xbmcvfs module --------------------------------------

    def vfs_write(self, path, data):
        real = self.translate(path)
        d = os.path.dirname(real)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(real, "wb") as f:
            f.write(bytes(data))
        self.vfs_written.add(real)  # now 'known' to Kodi
        return True

    def vfs_read(self, path):
        real = self.translate(path)
        if not os.path.isfile(real):
            return bytearray()
        if not self._vfs_can_read(real):
            return bytearray()  # tvOS: foreign local file reads EMPTY through the VFS
        with open(real, "rb") as f:
            return bytearray(f.read())

    def vfs_stat_size(self, path):
        # Stat sees the real disk file even when the VFS read returns empty - THE trap.
        real = self.translate(path)
        return os.path.getsize(real) if os.path.isfile(real) else -1


def make_modules(store):
    """Return (xbmc, xbmcvfs) stand-ins bound to `store`, matching the real API surface
    used by dropbox_remote._qr_image and ui._open_reader/_vfs_size."""

    class _File:
        def __init__(self, path, mode="r"):
            self._path, self._mode, self._buf = path, mode, bytearray()
            self._pos = (
                0  # read cursor - readBytes must ADVANCE or a copy loop spins forever
            )

        def read(self):
            return bytes(store.vfs_read(self._path))

        def readBytes(self, n=None):
            data = store.vfs_read(self._path)
            if n is None:
                return data
            chunk = data[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

        def write(self, data):
            self._buf += bytearray(data)
            return True

        def close(self):
            if "w" in self._mode:
                store.vfs_write(self._path, self._buf)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    class _Stat:
        def __init__(self, path):
            self._sz = store.vfs_stat_size(path)

        def st_size(self):
            return self._sz

    class _Vfs:
        File = staticmethod(_File)
        Stat = staticmethod(_Stat)
        translatePath = staticmethod(store.translate)

        @staticmethod
        def exists(p):
            return os.path.isfile(store.translate(p))

        @staticmethod
        def delete(p):
            real = store.translate(p)
            if os.path.isfile(real):
                os.remove(real)
                store.vfs_written.discard(real)
                return True
            return False

    class _Xbmc:
        LOGINFO, LOGWARNING, LOGERROR, LOGDEBUG = 1, 2, 3, 0

        @staticmethod
        def log(msg, level=1):
            store.log.append(msg)

    return _Xbmc, _Vfs
