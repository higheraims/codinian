"""QR matrices for the Remote Access dialog (ISSUE-029).

Kept apart from the dialog so the encoding has no GTK import and can be checked
on its own. `matrix_for` returns the module grid; drawing it is the widget's
job.

The encoder is python3-qrcode, a Fedora package on the same interpreter the app
already runs under, so there is nothing to pip install into a system Python.
It is still treated as optional: a machine without it gets None and a dialog
that says so, rather than an import error at startup.
"""

from __future__ import annotations

# A QR code needs a light margin around it or scanners will not find the
# finder patterns. Four modules is what the spec asks for.
QUIET_ZONE = 4


def available() -> bool:
    """Whether this machine can build a QR code at all."""
    try:
        import qrcode  # noqa: F401
    except ImportError:
        return False
    return True


def matrix_for(text: str) -> list[list[bool]] | None:
    """The QR module grid for `text`, quiet zone included, as rows of booleans
    where True is a dark module. Row 0 is the top; within a row, index 0 is the
    left. Returns None when the encoder is not installed, or when the text is
    empty or too long to encode.

    Error correction is the library default (M, about 15% recovery). L would
    give fewer, larger modules, but these codes get scanned off a screen at an
    angle, where the extra redundancy is worth more than the extra size.
    """
    if not text:
        return None
    try:
        import qrcode
    except ImportError:
        return None

    code = qrcode.QRCode(border=QUIET_ZONE)
    code.add_data(text)
    try:
        # fit=True picks the smallest version that holds the data; it raises
        # rather than truncating when nothing is big enough.
        code.make(fit=True)
    except Exception:
        return None
    return [[bool(cell) for cell in row] for row in code.get_matrix()]
