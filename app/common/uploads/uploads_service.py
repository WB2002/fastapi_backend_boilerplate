from __future__ import annotations

import os

from app.common.config.parsers import parse_positive_int
from app.common.uploads.scanners import (
    ClamAvFileScanner,
    FileScanner,
    NoopFileScanner,
)
from app.common.uploads.upload_types import (
    DEFAULT_UPLOAD_OPTIONS,
    AcceptedUploadFile,
    IncomingUploadFile,
    UploadValidationOptions,
)
from app.common.uploads.validate_upload_files import validate_upload_files


class UploadsService:
    def __init__(self, scanner: FileScanner | None = None) -> None:
        self._scanner = scanner or self._create_scanner_from_env()

    async def process_files(
        self,
        files: list[IncomingUploadFile],
        overrides: UploadValidationOptions | None = None,
    ) -> list[AcceptedUploadFile]:
        options = overrides or self.resolve_options()
        return await validate_upload_files(files, options, self._scanner)

    def resolve_options(self) -> UploadValidationOptions:
        max_files = parse_positive_int(
            os.getenv("UPLOAD_MAX_FILES"), DEFAULT_UPLOAD_OPTIONS.max_files
        )
        max_file_size = parse_positive_int(
            os.getenv("UPLOAD_MAX_FILE_SIZE"),
            DEFAULT_UPLOAD_OPTIONS.max_file_size,
        )
        max_total_size = parse_positive_int(
            os.getenv("UPLOAD_MAX_TOTAL_SIZE"),
            DEFAULT_UPLOAD_OPTIONS.max_total_size,
        )

        return UploadValidationOptions(
            max_files=max_files,
            max_file_size=max_file_size,
            max_total_size=max_total_size,
            allowed_mime_types=DEFAULT_UPLOAD_OPTIONS.allowed_mime_types,
            allowed_extensions=DEFAULT_UPLOAD_OPTIONS.allowed_extensions,
            field_name=DEFAULT_UPLOAD_OPTIONS.field_name,
        )

    def _create_scanner_from_env(self) -> FileScanner:
        enabled = os.getenv("UPLOAD_VIRUS_SCAN", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        if not enabled:
            return NoopFileScanner()

        host = os.getenv("CLAMAV_HOST", "localhost")
        port = parse_positive_int(os.getenv("CLAMAV_PORT"), 3310)
        return ClamAvFileScanner(host=host, port=port)
