"""Programmatic binary file fixtures for upload, magic-byte, and ClamAV E2E tests."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

UPLOAD_FIELD_NAME = "files"
DEFAULT_UPLOAD_MAX_FILES = 5
DEFAULT_UPLOAD_MAX_FILE_SIZE = 5_000_000
DEFAULT_UPLOAD_MAX_TOTAL_SIZE = 20_000_000

ALLOWED_MIME_TYPES: tuple[str, ...] = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
)

ALLOWED_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".pdf",
)

# Assembled via concatenation to avoid raw plaintext AV triggers in source files
EICAR_TEST_SIGNATURE: bytes = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


@dataclass(frozen=True, slots=True)
class UploadFilePayload:
    """Multipart upload tuple wrapper compatible with ``httpx`` ``files=[...]``."""

    field_name: str
    filename: str
    content: bytes
    content_type: str

    def as_httpx_file(self) -> tuple[str, tuple[str, bytes, str]]:
        """Return ``(field_name, (filename, content, content_type))`` for ``httpx``."""
        return (self.field_name, (self.filename, self.content, self.content_type))


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def make_png(
    *,
    extra_text: bytes = b"",
    target_size: int | None = None,
) -> bytes:
    """Generate a minimal structurally valid 1x1 RGB PNG image."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    iend = _png_chunk(b"IEND", b"")

    chunks = [signature, ihdr, idat]
    if extra_text:
        chunks.append(_png_chunk(b"tEXt", b"Comment\x00" + extra_text))

    base = b"".join(chunks) + iend
    if target_size is not None and target_size > len(base):
        needed = target_size - len(base)
        # Chunk overhead: 4B length + 4B "tEXt" + 2B "P\x00" + 4B CRC = 14B
        if needed < 14:
            msg = (
                f"target_size={target_size} requires {needed} padding bytes, "
                "which is below the 14-byte minimum PNG tEXt chunk overhead"
            )
            raise ValueError(msg)
        pad_bytes = b"A" * (needed - 14)
        chunks.append(_png_chunk(b"tEXt", b"P\x00" + pad_bytes))
        return b"".join(chunks) + iend
    return base


def make_jpeg() -> bytes:
    """Generate a minimal valid 1x1 JFIF baseline JPEG image."""
    soi = bytes.fromhex("ffd8")
    app0 = bytes.fromhex("ffe000104a46494600010100000100010000")
    dqt = bytes.fromhex("ffdb004300") + (b"\x01" * 64)
    sof0 = bytes.fromhex("ffc0000b080001000101011100")
    dht_dc = bytes.fromhex("ffc40014000100000000000000000000000000000000")
    dht_ac = bytes.fromhex("ffc40014100100000000000000000000000000000000")
    sos = bytes.fromhex("ffda0008010100003f00")
    scan_data = bytes.fromhex("3f")
    eoi = bytes.fromhex("ffd9")
    return soi + app0 + dqt + sof0 + dht_dc + dht_ac + sos + scan_data + eoi


def _make_gif(header: bytes) -> bytes:
    lsd = struct.pack("<HHBBB", 1, 1, 0x80, 0, 0)
    gct = b"\xff\xff\xff\x00\x00\x00"
    img_desc = b"," + struct.pack("<HHHHB", 0, 0, 1, 1, 0)
    lzw = b"\x02\x02\x44\x01\x00"
    return header + lsd + gct + img_desc + lzw + b";"


def make_gif87a() -> bytes:
    """Generate a minimal valid 1x1 GIF87a image."""
    return _make_gif(b"GIF87a")


def make_gif89a() -> bytes:
    """Generate a minimal valid 1x1 GIF89a image."""
    return _make_gif(b"GIF89a")


def make_webp() -> bytes:
    """Generate a minimal valid 1x1 lossless WEBP (VP8L) image."""
    vp8l_payload = bytes.fromhex("2f00000088880800")
    vp8l_chunk = b"VP8L" + struct.pack("<I", len(vp8l_payload)) + vp8l_payload
    return b"RIFF" + struct.pack("<I", 4 + len(vp8l_chunk)) + b"WEBP" + vp8l_chunk


def _escape_pdf_text(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return escaped.encode("latin-1", errors="replace")


def make_pdf(text: str = "E2E Test PDF") -> bytes:
    """Generate a minimal structurally valid PDF 1.4 document with accurate xref."""
    stream_body = b"BT /F1 12 Tf 72 720 Td (" + _escape_pdf_text(text) + b") Tj ET"
    objects: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n"
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R >>\nendobj\n"
        ),
        (
            f"4 0 obj\n<< /Length {len(stream_body)} >>\nstream\n".encode("ascii")
            + stream_body
            + b"\nendstream\nendobj\n"
        ),
    ]

    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for obj in objects:
        offsets.append(len(buf))
        buf.extend(obj)

    xref_offset = len(buf)
    buf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(buf)


def make_eicar_raw() -> bytes:
    """Return the canonical 68-byte EICAR test signature."""
    return EICAR_TEST_SIGNATURE


def make_eicar_png() -> bytes:
    """Return a valid PNG embedding EICAR in a ``tEXt`` chunk."""
    return make_png(extra_text=EICAR_TEST_SIGNATURE)


def make_valid_png_payload(filename: str = "valid.png") -> UploadFilePayload:
    """Return an ``UploadFilePayload`` with a valid PNG image."""
    return UploadFilePayload(UPLOAD_FIELD_NAME, filename, make_png(), "image/png")


def make_spoofed_html_as_png() -> UploadFilePayload:
    """Return HTML bytes masquerading as a PNG upload."""
    return UploadFilePayload(
        UPLOAD_FIELD_NAME,
        "xss.png",
        b"<html><script>alert(1)</script></html>",
        "image/png",
    )


def make_spoofed_exe_as_png() -> UploadFilePayload:
    """Return PE/MZ executable bytes masquerading as a PNG upload."""
    return UploadFilePayload(
        UPLOAD_FIELD_NAME,
        "malware.png",
        b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00PE\x00\x00",
        "image/png",
    )


def make_mismatched_png_as_pdf() -> UploadFilePayload:
    """Return PNG magic bytes declared with ``.pdf`` / ``application/pdf``."""
    return UploadFilePayload(
        UPLOAD_FIELD_NAME,
        "evil.pdf",
        make_png(),
        "application/pdf",
    )


def make_mismatched_pdf_as_png() -> UploadFilePayload:
    """Return PDF magic bytes declared with ``.png`` / ``image/png``."""
    return UploadFilePayload(
        UPLOAD_FIELD_NAME,
        "evil.png",
        make_pdf(),
        "image/png",
    )


def make_disallowed_extension_txt() -> UploadFilePayload:
    """Return a ``.txt`` file upload with disallowed extension and MIME type."""
    return UploadFilePayload(
        UPLOAD_FIELD_NAME,
        "notes.txt",
        b"hello world",
        "text/plain",
    )


def make_empty_png() -> UploadFilePayload:
    """Return a zero-byte ``empty.png`` upload payload."""
    return UploadFilePayload(UPLOAD_FIELD_NAME, "empty.png", b"", "image/png")


def make_oversized_png(
    size_bytes: int,
    filename: str = "big.png",
) -> UploadFilePayload:
    """Return a valid PNG padded to ``size_bytes`` bytes."""
    return UploadFilePayload(
        UPLOAD_FIELD_NAME,
        filename,
        make_png(target_size=size_bytes),
        "image/png",
    )


__all__ = [
    "ALLOWED_EXTENSIONS",
    "ALLOWED_MIME_TYPES",
    "DEFAULT_UPLOAD_MAX_FILES",
    "DEFAULT_UPLOAD_MAX_FILE_SIZE",
    "DEFAULT_UPLOAD_MAX_TOTAL_SIZE",
    "EICAR_TEST_SIGNATURE",
    "UPLOAD_FIELD_NAME",
    "UploadFilePayload",
    "make_disallowed_extension_txt",
    "make_eicar_png",
    "make_eicar_raw",
    "make_empty_png",
    "make_gif87a",
    "make_gif89a",
    "make_jpeg",
    "make_mismatched_pdf_as_png",
    "make_mismatched_png_as_pdf",
    "make_oversized_png",
    "make_pdf",
    "make_png",
    "make_spoofed_exe_as_png",
    "make_spoofed_html_as_png",
    "make_valid_png_payload",
    "make_webp",
]
