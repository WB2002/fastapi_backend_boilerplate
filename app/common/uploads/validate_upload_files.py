from __future__ import annotations

import os
import re
import uuid

from fastapi import HTTPException

from app.common.uploads.detect_mime import detect_mime_from_magic_bytes
from app.common.uploads.scanners import FileScanner
from app.common.uploads.upload_types import (
    UPLOAD_REJECT_CODES,
    AcceptedUploadFile,
    IncomingUploadFile,
    UploadValidationError,
    UploadValidationOptions,
)

MIME_TO_EXTENSIONS: dict[str, list[str]] = {
    "image/jpeg": [".jpg", ".jpeg"],
    "image/png": [".png"],
    "image/webp": [".webp"],
    "image/gif": [".gif"],
    "application/pdf": [".pdf"],
}


def sanitize_original_name(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^\w.\-()+ ]+", "_", base).strip()
    return cleaned[:200] if cleaned else "file"


def mime_matches_extension(mime: str, extension: str) -> bool:
    allowed = MIME_TO_EXTENSIONS.get(mime, [])
    return extension.lower() in allowed


async def validate_upload_files(
    files: list[IncomingUploadFile],
    options: UploadValidationOptions,
    scanner: FileScanner,
) -> list[AcceptedUploadFile]:
    if not files:
        raise UploadValidationError(
            UPLOAD_REJECT_CODES["EMPTY_FILE"], "At least one file is required"
        )

    if len(files) > options.max_files:
        raise UploadValidationError(
            UPLOAD_REJECT_CODES["MAX_FILES_EXCEEDED"],
            f"At most {options.max_files} files are allowed",
        )

    total_size = 0
    accepted: list[AcceptedUploadFile] = []

    for file in files:
        if not file.buffer or file.size == 0:
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["EMPTY_FILE"], "Empty files are not allowed"
            )

        if (
            file.size > options.max_file_size
            or len(file.buffer) > options.max_file_size
        ):
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["FILE_TOO_LARGE"],
                f"Each file must be at most {options.max_file_size} bytes",
            )

        total_size += len(file.buffer)
        if total_size > options.max_total_size:
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["TOTAL_SIZE_EXCEEDED"],
                f"Total upload size must be at most {options.max_total_size} bytes",
            )

        original_name = sanitize_original_name(file.originalname)
        _, ext = os.path.splitext(original_name)
        extension = ext.lower()

        if extension not in options.allowed_extensions:
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["INVALID_FILE_TYPE"],
                "File extension is not allowed",
            )

        declared_mime = (file.mimetype or "").lower()
        if declared_mime not in options.allowed_mime_types:
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["INVALID_FILE_TYPE"],
                "File MIME type is not allowed",
            )

        detected = detect_mime_from_magic_bytes(file.buffer)
        if (
            not detected
            or detected.mime not in options.allowed_mime_types
            or not mime_matches_extension(detected.mime, extension)
        ):
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["INVALID_FILE_TYPE"],
                "File content does not match an allowed type",
            )

        scan = await scanner.scan(file.buffer, original_name)
        if scan.status == "infected":
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["MALWARE_DETECTED"],
                "Malicious file rejected",
            )
        if scan.status == "error":
            raise HTTPException(
                status_code=503,
                detail={
                    "statusCode": 503,
                    "code": UPLOAD_REJECT_CODES["SCANNER_UNAVAILABLE"],
                    "message": "Virus scanner unavailable",
                },
            )

        file_id = str(uuid.uuid4())
        accepted.append(
            AcceptedUploadFile(
                id=file_id,
                original_name=original_name,
                stored_name=f"{file_id}{extension}",
                mime_type=detected.mime,
                size=len(file.buffer),
                scanned=scanner.is_scanning_enabled,
            )
        )

    return accepted
