from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.common.uploads import (
    ClamAvFileScanner,
    FileScanResult,
    IncomingUploadFile,
    NoopFileScanner,
    UploadValidationError,
    UploadValidationOptions,
    detect_mime_from_magic_bytes,
    validate_upload_files,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 20
GIF_BYTES = b"GIF89a" + b"\x00" * 20
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 20


def test_magic_bytes_detection() -> None:
    png = detect_mime_from_magic_bytes(PNG_BYTES)
    assert png is not None and png.mime == "image/png" and png.ext == "png"

    jpg = detect_mime_from_magic_bytes(JPEG_BYTES)
    assert jpg is not None and jpg.mime == "image/jpeg" and jpg.ext == "jpg"

    gif = detect_mime_from_magic_bytes(GIF_BYTES)
    assert gif is not None and gif.mime == "image/gif" and gif.ext == "gif"

    webp = detect_mime_from_magic_bytes(WEBP_BYTES)
    assert webp is not None and webp.mime == "image/webp" and webp.ext == "webp"

    pdf = detect_mime_from_magic_bytes(PDF_BYTES)
    assert pdf is not None and pdf.mime == "application/pdf" and pdf.ext == "pdf"

    assert detect_mime_from_magic_bytes(b"short") is None
    assert detect_mime_from_magic_bytes(b"UNKNOWN_BINARY_DATA_123456") is None


@pytest.mark.asyncio
async def test_scanners() -> None:
    noop = NoopFileScanner()
    assert noop.is_scanning_enabled is False
    res = await noop.scan(b"test", "test.txt")
    assert res.status == "clean"

    clam = ClamAvFileScanner("localhost", 3310)
    assert clam.is_scanning_enabled is True


@pytest.mark.asyncio
async def test_validate_upload_files_empty() -> None:
    scanner = NoopFileScanner()
    opts = UploadValidationOptions()

    with pytest.raises(UploadValidationError) as exc:
        await validate_upload_files([], opts, scanner)
    assert exc.value.code == "EMPTY_FILE"


@pytest.mark.asyncio
async def test_validate_upload_files_max_count() -> None:
    scanner = NoopFileScanner()
    opts = UploadValidationOptions(max_files=1)

    files = [
        IncomingUploadFile("files", "a.png", "image/png", PNG_BYTES, len(PNG_BYTES)),
        IncomingUploadFile("files", "b.png", "image/png", PNG_BYTES, len(PNG_BYTES)),
    ]
    with pytest.raises(UploadValidationError) as exc:
        await validate_upload_files(files, opts, scanner)
    assert exc.value.code == "MAX_FILES_EXCEEDED"


@pytest.mark.asyncio
async def test_validate_upload_files_size_limits() -> None:
    scanner = NoopFileScanner()
    opts = UploadValidationOptions(max_file_size=10, max_total_size=100)

    files = [
        IncomingUploadFile("files", "a.png", "image/png", PNG_BYTES, len(PNG_BYTES)),
    ]
    with pytest.raises(UploadValidationError) as exc:
        await validate_upload_files(files, opts, scanner)
    assert exc.value.code == "FILE_TOO_LARGE"

    opts2 = UploadValidationOptions(max_file_size=100, max_total_size=20)
    with pytest.raises(UploadValidationError) as exc:
        await validate_upload_files(files, opts2, scanner)
    assert exc.value.code == "TOTAL_SIZE_EXCEEDED"


@pytest.mark.asyncio
async def test_validate_upload_files_types_and_magic_mismatch() -> None:
    scanner = NoopFileScanner()
    opts = UploadValidationOptions()

    # Invalid extension
    files = [
        IncomingUploadFile("files", "bad.exe", "image/png", PNG_BYTES, len(PNG_BYTES)),
    ]
    with pytest.raises(UploadValidationError) as exc:
        await validate_upload_files(files, opts, scanner)
    assert exc.value.code == "INVALID_FILE_TYPE"

    # Spoofed extension (claims .png but bytes are random)
    fake_png = b"NOT_A_PNG_HEADER_12345678"
    files2 = [
        IncomingUploadFile("files", "fake.png", "image/png", fake_png, len(fake_png)),
    ]
    with pytest.raises(UploadValidationError) as exc:
        await validate_upload_files(files2, opts, scanner)
    assert exc.value.code == "INVALID_FILE_TYPE"


@pytest.mark.asyncio
async def test_validate_upload_files_malware_and_scanner_error() -> None:
    opts = UploadValidationOptions()
    mock_scanner = AsyncMock()

    # Infected
    mock_scanner.scan.return_value = FileScanResult(
        status="infected", signature="Eicar-Test-Signature"
    )
    files = [
        IncomingUploadFile("files", "test.png", "image/png", PNG_BYTES, len(PNG_BYTES)),
    ]
    with pytest.raises(UploadValidationError) as exc:
        await validate_upload_files(files, opts, mock_scanner)
    assert exc.value.code == "MALWARE_DETECTED"

    # Scanner error -> 503
    mock_scanner.scan.return_value = FileScanResult(
        status="error", message="Connection refused"
    )
    with pytest.raises(HTTPException) as exc_http:
        await validate_upload_files(files, opts, mock_scanner)
    assert exc_http.value.status_code == 503


@pytest.mark.asyncio
async def test_validate_upload_files_success() -> None:
    scanner = NoopFileScanner()
    opts = UploadValidationOptions()

    files = [
        IncomingUploadFile(
            "files", "image.png", "image/png", PNG_BYTES, len(PNG_BYTES)
        ),
        IncomingUploadFile(
            "files", "doc.pdf", "application/pdf", PDF_BYTES, len(PDF_BYTES)
        ),
    ]
    accepted = await validate_upload_files(files, opts, scanner)
    assert len(accepted) == 2
    assert accepted[0].original_name == "image.png"
    assert accepted[0].mime_type == "image/png"
    assert accepted[0].stored_name.endswith(".png")
    assert accepted[1].original_name == "doc.pdf"
    assert accepted[1].mime_type == "application/pdf"
    assert accepted[1].stored_name.endswith(".pdf")
