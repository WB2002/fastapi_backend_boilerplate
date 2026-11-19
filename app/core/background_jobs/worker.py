from __future__ import annotations

import sys

from app.core.background_jobs.celery_app import celery_app


def main() -> None:
    """CLI entrypoint for running Celery background worker process."""
    argv = sys.argv[1:]
    if not argv:
        argv = [
            "worker",
            "-Q",
            "background-jobs,celery,default",
            "-l",
            "INFO",
            "-c",
            "2",
        ]
    elif argv[0] != "worker":
        argv = ["worker", *argv]

    celery_app.worker_main(argv)


if __name__ == "__main__":
    main()
