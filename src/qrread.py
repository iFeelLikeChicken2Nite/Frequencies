"""Frequencies — reading a station back out of a picture.

Sharing writes a QR code with the station packed into it. This is the
other end: hand it an image file and it gives back the code that was
drawn in it.

The decoder is zxing-cpp, which ships as a compiled wheel and needs
nothing installed on the machine. It is imported lazily and its absence
is reported as a sentence rather than an ImportError, because a build
without it should still run — it just cannot import from pictures.
"""

import os


class QRError(Exception):
    """Raised with something worth showing a person."""


def available():
    """Whether this build can read codes at all."""
    try:
        import zxingcpp                         # noqa: F401
        return True
    except Exception:                           # noqa: BLE001
        return False


def _load(path):
    """The image, as something the decoder accepts.

    PIL if it is here, Qt if it is not — Qt is always present in this
    app and can read every format the share window writes.
    """
    try:
        from PIL import Image
        return Image.open(path).convert("RGB")
    except ImportError:
        pass

    import imagelock
    image = imagelock.image(path)      # under the image lock (task 54)
    if image.isNull():
        raise QRError("That file is not an image this app can read.")
    return image


def read_code(path):
    """The text drawn in the QR code in this image.

    Raises QRError with a plain sentence for anything a person could
    reasonably do something about: no decoder, unreadable file, or an
    image with no code in it.
    """
    if not path or not os.path.exists(path):
        raise QRError("That file is not there any more.")

    try:
        import zxingcpp
    except Exception as exc:                    # noqa: BLE001
        raise QRError(
            "This build cannot read QR codes — the decoder is missing."
        ) from exc

    image = _load(path)

    # A Qt image (no PIL here, as in the Windows build). zxing-cpp 2.x+
    # takes a buffer shaped (height, width) of gray bytes; older ones took
    # raw bytes, width, height and the row length. Build 126: on Windows
    # every QR picture failed with "could not read", because only the old
    # call was tried and the bundled zxing-cpp is 3.x.
    if not hasattr(image, "size") or not hasattr(image, "convert"):
        from PyQt5.QtGui import QImage
        image = image.convertToFormat(QImage.Format_Grayscale8)
        width, height = image.width(), image.height()
        stride = image.bytesPerLine()
        padded = bytes(image.constBits().asstring(stride * height))
        raw = b"".join(padded[y * stride:y * stride + width]
                       for y in range(height))
        try:
            results = zxingcpp.read_barcodes(
                memoryview(raw).cast("B", (height, width)))
        except TypeError:
            try:
                results = zxingcpp.read_barcodes(raw, width, height, width)
            except TypeError:
                raise QRError("The decoder could not read that image.")
    else:
        results = zxingcpp.read_barcodes(image)

    for result in results or []:
        text = (getattr(result, "text", "") or "").strip()
        if text:
            return text
    raise QRError("No code was found in that picture.")


def read_station(path):
    """The station drawn in this image, ready to add.

    A picture that holds a code for something else — a website, a
    payment link — is a QR code the app understands perfectly well and
    still cannot use, so it is named as such rather than reported as a
    decoding failure.
    """
    import storage

    text = read_code(path)
    if not text.startswith("FREQ1:"):
        raise QRError("That code is not a Frequencies station.")

    station = storage.station_from_code(text)
    if not station:
        raise QRError("That station code could not be unpacked.")
    return station
