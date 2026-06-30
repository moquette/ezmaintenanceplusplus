# Vendored encoder-only subset of python-qrcode (https://github.com/lincolnloop/python-qrcode),
# BSD-3-Clause. Image rendering is stripped (no PIL/pypng); use QRCode().get_matrix()
# and render with resources/lib/modules/_qrgen.py. Kept here so the on-device Dropbox
# sign-in QR is generated locally, needing no external QR service.
from qrcode.main import QRCode  # noqa: F401
from qrcode.constants import (  # noqa: F401
    ERROR_CORRECT_L,
    ERROR_CORRECT_M,
    ERROR_CORRECT_Q,
    ERROR_CORRECT_H,
)
