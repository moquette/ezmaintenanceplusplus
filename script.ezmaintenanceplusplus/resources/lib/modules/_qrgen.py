"""Local QR generation - no network, no PIL.

Uses the vendored encoder-only qrcode (resources/lib/modules/_vendor/qrcode) to build
the module matrix, then renders it to an opaque 8-bit grayscale PNG with a tiny
zlib-based writer (zlib is in Kodi's Python). This is what lets the PKCE sign-in QR -
whose URL changes every sign-in, so it cannot be pre-baked - be generated on the device
without depending on any external QR service.
"""

import os
import struct
import sys
import zlib

_VENDOR = os.path.join(os.path.dirname(__file__), "_vendor")


def make_qr_png_bytes(data, scale=12, border=4):
    """Return PNG bytes (opaque, black-on-white) encoding `data` as a QR code."""
    if _VENDOR not in sys.path:
        sys.path.insert(0, _VENDOR)
    import qrcode  # vendored

    qr = qrcode.QRCode(border=border, error_correction=qrcode.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    return _matrix_to_png(qr.get_matrix(), scale)


def _matrix_to_png(matrix, scale):
    n = len(matrix)
    side = n * scale
    raw = bytearray()
    # 32-bit RGBA (opaque). Kodi's texture loader is unreliable with 8-bit grayscale
    # PNGs on newer builds (verified: a grayscale QR rendered blank on Kodi 21.3/tvOS
    # while the sign-in window itself showed) - a truecolor+alpha PNG is the format
    # skins are told to use and loads everywhere.
    dark = b"\x00\x00\x00\xff"  # opaque black
    light = b"\xff\xff\xff\xff"  # opaque white
    for row in matrix:
        line = bytearray([0])  # PNG filter type 0 (None) for this scanline
        for cell in row:
            line += (dark if cell else light) * scale  # dark->black, light->white
        raw += line * scale  # each module row is `scale` identical scanlines

    def _chunk(typ, body):
        block = typ + body
        return (
            struct.pack(">I", len(body))
            + block
            + struct.pack(">I", zlib.crc32(block) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(
        ">IIBBBBB", side, side, 8, 6, 0, 0, 0
    )  # 8-bit RGBA (truecolor+alpha)
    idat = zlib.compress(bytes(raw), 9)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
