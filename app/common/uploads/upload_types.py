from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from fastapi import HTTPException

UPLOAD_REJECT_CODES = {
    "MAX_FILES_EXCEEDED": "MAX_FILES_EXCEEDED",
    "FILE_TOO_LARGE": "FILE_TOO_LARGE",
    "TOTAL_SIZE_EXCEEDED": "TOTAL_SIZE_EXCEEDED",
    "INVALID_FILE_TYPE": "INVALID_FILE_TYPE",
    "EMPTY_FILE": "EMPTY_FILE",
    "MALWARE_DETECTED": "MALWARE_DETECTED",
    "SCANNER_UNAVAILABLE": "SCANNER_UNAVAILABLE",
}

type UploadRejectCode = Literal[
    "MAX_FILES_EXCEEDED",
    "FILE_TOO_LARGE",
    "TOTAL_SIZE_EXCEEDED",
    "INVALID_FILE_TYPE",
    "EMPTY_FILE",
    "MALWARE_DETECTED",
    "SCANNER_UNAVAILABLE",
]


class UploadValidationError(HTTPException):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            status_code=400,
            detail={"statusCode": 400, "code": code, "message": message},
        )
        self.code = code
        self.message = message


@dataclass(slots=True)
class UploadValidationOptions:
    max_files: int = 5
    max_file_size: int = 5_000_000
    max_total_size: int = 20_000_000
    allowed_mime_types: list[str] = field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
            "application/pdf",
        ]
    )
    allowed_extensions: list[str] = field(
        default_factory=lambda: [
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".pdf",
        ]
    )
    field_name: str = "files"

    @property
    def maxFiles(self) -> int:
        return self.max_files

    @property
    def maxFileSize(self) -> int:
        return self.max_file_size

    @property
    def maxTotalSize(self) -> int:
        return self.max_total_size

    @property
    def allowedMimeTypes(self) -> list[str]:
        return self.allowed_mime_types

    @property
    def allowedExtensions(self) -> list[str]:
        return self.allowed_extensions

    @property
    def fieldName(self) -> str:
        return self.field_name


DEFAULT_UPLOAD_OPTIONS: Final[UploadValidationOptions] = UploadValidationOptions()


@dataclass(slots=True)
class IncomingUploadFile:
    fieldname: str
    originalname: str
    mimetype: str
    buffer: bytes
    size: int


@dataclass(slots=True)
class AcceptedUploadFile:
    id: str
    original_name: str
    stored_name: str
    mime_type: str
    size: int
    scanned: bool

    @property
    def originalName(self) -> str:
        return self.original_name

    @property
    def storedName(self) -> str:
        return self.stored_name

    @property
    def mimeType(self) -> str:
        return self.mime_type
