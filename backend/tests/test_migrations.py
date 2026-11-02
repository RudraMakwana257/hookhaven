from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.core.config import get_settings

BACKEND = Path(__file__).resolve().parent.parent


def test_migrations_apply_and_match_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade works, models and migrations have no drift, downgrade works."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    get_settings.cache_clear()
    try:
        config = Config(str(BACKEND / "alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "base")
    finally:
        get_settings.cache_clear()
