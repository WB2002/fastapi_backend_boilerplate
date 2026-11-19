from __future__ import annotations

import logging
from typing import Any

from app.core.background_jobs.handlers import JobHandler

logger = logging.getLogger("ExampleEchoJobHandler")
EXAMPLE_ECHO_JOB_NAME = "example.echo"


class ExampleEchoJobHandler(JobHandler):
    async def handle(self, payload: Any) -> None:
        if isinstance(payload, dict) and "message" in payload:
            message = str(payload["message"])
        else:
            message = str(payload)
        logger.info("Echo job: %s", message)
