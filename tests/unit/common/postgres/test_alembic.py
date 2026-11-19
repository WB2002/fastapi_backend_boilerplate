from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.common.postgres.migrator import (
    run_alembic_downgrade,
    run_alembic_upgrade_head,
)
from migrations.alembic.env import get_database_url


def test_alembic_ini_and_script_directory() -> None:
    root = Path(__file__).resolve().parents[4]
    ini_path = root / "alembic.ini"
    assert ini_path.exists(), "alembic.ini must exist at repository root"

    cfg = Config(str(ini_path))
    script_dir = ScriptDirectory.from_config(cfg)

    # Verify head revision is 0002
    heads = script_dir.get_heads()
    assert heads == ["0002"]

    # Verify linear revision history: base -> 0001 -> 0002
    rev_0001 = script_dir.get_revision("0001")
    assert rev_0001 is not None
    assert rev_0001.down_revision is None

    rev_0002 = script_dir.get_revision("0002")
    assert rev_0002 is not None
    assert rev_0002.down_revision == "0001"


def test_alembic_get_database_url_from_env() -> None:
    env = {
        "POSTGRES_HOST": "db.internal",
        "POSTGRES_PORT": "5433",
        "POSTGRES_USER": "myuser",
        "POSTGRES_PASSWORD": "mypassword",
        "POSTGRES_DB": "testdb",
    }
    with patch.dict("os.environ", env, clear=True):
        url = get_database_url()
        assert url == "postgresql+asyncpg://myuser:mypassword@db.internal:5433/testdb"


def test_alembic_get_database_url_from_database_url() -> None:
    env = {"DATABASE_URL": "postgresql://usr:pwd@host:5432/db"}
    with patch.dict("os.environ", env, clear=True):
        url = get_database_url()
        assert url == "postgresql+asyncpg://usr:pwd@host:5432/db"


def test_run_alembic_upgrade_and_downgrade_call_command() -> None:
    with (
        patch("alembic.command.upgrade") as mock_upgrade,
        patch("alembic.command.downgrade") as mock_downgrade,
    ):
        run_alembic_upgrade_head()
        mock_upgrade.assert_called_once()

        run_alembic_downgrade("base")
        mock_downgrade.assert_called_once()
