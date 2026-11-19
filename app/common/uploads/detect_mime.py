from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DetectedMime:
    mime: str
    ext: str


def detect_mime_from_magic_bytes(buffer: bytes) -> DetectedMime | None:
    """Sniff magic bytes for supported upload file formats matching NestJS."""
    length = len(buffer)
    if length < 4:
        return None

    # PNG: 89 50 4E 47
    if (
        buffer[0] == 0x89
        and buffer[1] == 0x50
        and buffer[2] == 0x4E
        and buffer[3] == 0x47
    ):
        return DetectedMime(mime="image/png", ext="png")

    # JPEG: FF D8 FF
    if buffer[0] == 0xFF and buffer[1] == 0xD8 and buffer[2] == 0xFF:
        return DetectedMime(mime="image/jpeg", ext="jpg")

    # GIF: GIF87a / GIF89a (6 bytes)
    if (
        length >= 6
        and buffer[0] == 0x47
        and buffer[1] == 0x49
        and buffer[2] == 0x46
        and buffer[3] == 0x38
        and buffer[4] in (0x37, 0x39)
        and buffer[5] == 0x61
    ):
        return DetectedMime(mime="image/gif", ext="gif")

    # WEBP: RIFF....WEBP (12 bytes)
    if (
        length >= 12
        and buffer[0] == 0x52
        and buffer[1] == 0x49
        and buffer[2] == 0x46
        and buffer[3] == 0x46
        and buffer[8] == 0x57
        and buffer[9] == 0x45
        and buffer[10] == 0x42
        and buffer[11] == 0x50
    ):
        return DetectedMime(mime="image/webp", ext="webp")

    # PDF: %PDF (4 bytes)
    if (
        buffer[0] == 0x25
        and buffer[1] == 0x50
        and buffer[2] == 0x44
        and buffer[3] == 0x46
    ):
        return DetectedMime(mime="application/pdf", ext="pdf")

    return None
