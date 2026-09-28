"""
CareLoop AI — Alembic Environment Configuration
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure backend root is in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Import models & Base so Alembic sees all Phase 1 tables
from app.core.config import get_settings
from app.core.database import Base
import app.models  # noqa: F401 - ensure all models are registered

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Set database URL dynamically from environment / settings
settings = get_settings()
db_url = os.environ.get("DATABASE_URL", settings.database_url)
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # One transaction PER MIGRATION, not one for the whole run.
            #
            # Required since Phase 6, which adds enum values with
            # `ALTER TYPE ... ADD VALUE` and then references those new values
            # from an index predicate. Postgres only makes a new enum value
            # visible on COMMIT, so under the default single-transaction run a
            # later migration in the same run fails with
            # `unsafe use of new value "checkin" of enum type reminder_type` -
            # and because the whole run shares a transaction, the earlier
            # revision's successful work is rolled back with it.
            #
            # Per-migration transactions also mean a failure leaves the database
            # at the last fully applied revision instead of at the previous
            # one, which is the behaviour an operator expects from a migration
            # tool.
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
