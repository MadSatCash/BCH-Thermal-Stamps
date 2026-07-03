"""QR code image generation using the qrcode library (pure Python).

Replaces the previous hand-rolled QR encoder. For stamps this matters: a wrong
QR means unrecoverable funds, so we rely on a well-tested encoder. The claim QR
carries a private key, so it uses the highest error-correction level to survive
the low quality of thermal printing.
"""

from __future__ import annotations

import qrcode
from qrcode import exceptions as qr_exceptions
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M
from PIL import Image


def make_qr_image(data: str, target_px: int, border: int = 4, high_ec: bool = True) -> Image.Image:
    """Return a crisp black/white QR image sized at or just under `target_px`.

    The matrix is rendered at one pixel per module and then scaled by an integer
    factor, so every module stays the same width (important for scanning).
    """
    ec = ERROR_CORRECT_H if high_ec else ERROR_CORRECT_M
    payload = data or " "
    # qrcode has a known bug (glog(0)) where letting it auto-fit a version for
    # certain payload-length / error-correction combos picks one too small.
    # Forcing an explicit version that comfortably fits sidesteps it.
    base = None
    for version in range(1, 41):
        try:
            qr = qrcode.QRCode(version=version, error_correction=ec, box_size=1, border=border)
            qr.add_data(payload)
            qr.make(fit=False)
            base = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            break
        except (ValueError, qr_exceptions.DataOverflowError):
            continue
    if base is None:
        raise ValueError("No se pudo generar el QR: datos demasiado largos.")

    modules = base.size[0]
    scale = max(1, target_px // modules)
    size = modules * scale
    if scale == 1:
        return base
    return base.resize((size, size), Image.Resampling.NEAREST)
