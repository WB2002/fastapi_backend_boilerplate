from __future__ import annotations

from app.common.uploads.detect_mime import (
    DetectedMime,
    detect_mime_from_magic_bytes,
)
from app.common.uploads.scanners import (
    ClamAvFileScanner,
    FileScanner,
    FileScanResult,
    NoopFileScanner,
    ScanStatus,
)
from app.common.uploads.upload_types import (
    DEFAULT_UPLOAD_OPTIONS,
    UPLOAD_REJECT_CODES,
    AcceptedUploadFile,
    IncomingUploadFile,
    UploadRejectCode,
    UploadValidationError,
    UploadValidationOptions,
)
from app.common.uploads.uploads_controller import router as uploads_router
from app.common.uploads.uploads_service import UploadsService
from app.common.uploads.validate_upload_files import validate_upload_files

__all__ = [
    "DEFAULT_UPLOAD_OPTIONS",
    "UPLOAD_REJECT_CODES",
    "AcceptedUploadFile",
    "ClamAvFileScanner",
    "DetectedMime",
    "FileScanResult",
    "FileScanner",
    "IncomingUploadFile",
    "NoopFileScanner",
    "ScanStatus",
    "UploadRejectCode",
    "UploadValidationError",
    "UploadValidationOptions",
    "UploadsService",
    "detect_mime_from_magic_bytes",
    "uploads_router",
    "validate_upload_files",
]
