"""create background_jobs table and enum

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

job_status_enum = postgresql.ENUM(
    "pending",
    "processing",
    "completed",
    "failed",
    "cancelled",
    name="background_job_status",
    create_type=False,
)


def upgrade() -> None:
    # Safely create enum type if not present
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE background_job_status AS ENUM (
                'pending', 'processing', 'completed', 'failed', 'cancelled'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.create_table(
        "background_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "queue_name",
            sa.String(length=64),
            nullable=False,
            server_default="default",
        ),
        sa.Column("job_name", sa.String(length=128), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            job_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "idx_background_jobs_queue_status_run_at",
        "background_jobs",
        ["queue_name", "status", "run_at"],
    )
    op.create_index(
        "idx_background_jobs_lease_reclaim",
        "background_jobs",
        ["status", "lease_until"],
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "uq_background_jobs_dedupe_active",
        "background_jobs",
        ["queue_name", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'processing') AND dedupe_key IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_background_jobs_dedupe_active",
        table_name="background_jobs",
    )
    op.drop_index(
        "idx_background_jobs_lease_reclaim",
        table_name="background_jobs",
    )
    op.drop_index(
        "idx_background_jobs_queue_status_run_at",
        table_name="background_jobs",
    )
    op.drop_table("background_jobs")
    op.execute("DROP TYPE IF EXISTS background_job_status;")
