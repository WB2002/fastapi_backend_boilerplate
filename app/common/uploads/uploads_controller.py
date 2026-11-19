from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile

from app.common.uploads.upload_types import (
    UPLOAD_REJECT_CODES,
    IncomingUploadFile,
    UploadValidationError,
)
from app.common.uploads.uploads_service import UploadsService

router = APIRouter(prefix="/uploads", tags=["Uploads"])


def get_uploads_service() -> UploadsService:
    return UploadsService()


@router.post("")
async def upload_files(
    files: Annotated[list[UploadFile], File(...)],
    service: Annotated[UploadsService, Depends(get_uploads_service)],
) -> dict[str, list[dict[str, Any]]]:
    options = service.resolve_options()

    if len(files) > options.max_files:
        raise UploadValidationError(
            UPLOAD_REJECT_CODES["MAX_FILES_EXCEEDED"],
            f"At most {options.max_files} files are allowed",
        )

    incoming: list[IncomingUploadFile] = []
    total_size = 0

    for f in files:
        content = await f.read()
        size = len(content)

        if size > options.max_file_size:
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["FILE_TOO_LARGE"],
                f"Each file must be at most {options.max_file_size} bytes",
            )

        total_size += size
        if total_size > options.max_total_size:
            raise UploadValidationError(
                UPLOAD_REJECT_CODES["TOTAL_SIZE_EXCEEDED"],
                f"Total upload size must be at most {options.max_total_size} bytes",
            )

        incoming.append(
            IncomingUploadFile(
                fieldname="files",
                originalname=f.filename or "file",
                mimetype=f.content_type or "application/octet-stream",
                buffer=content,
                size=size,
            )
        )

    accepted = await service.process_files(incoming, options)

    return {
        "files": [
            {
                "id": f.id,
                "originalName": f.original_name,
                "mimeType": f.mime_type,
                "size": f.size,
                "scanned": f.scanned,
            }
            for f in accepted
        ]
    }
