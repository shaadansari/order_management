"""Alembic migration environment.

WHY this file reaches into the app: Alembic needs (a) the DB URL and (b) the full ORM
metadata to compare the models against the live schema when autogenerating migrations.
We import `settings` (reads DATABASE_URL from the env) and `Base.metadata` (the single
source of truth for the schema), so the migration target never drifts from the models.
The URL is set on the config here, NOT in alembic.ini — that keeps credentials out of a
committed file.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make `app` importable when Alembic runs from the project root (and from inside the
# container, where CWD is /app). Insert the project root = parent of this file's dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402  (import after sys.path tweak)
from app.database import Base  # noqa: E402

# Import EVERY model so its table is registered on Base.metadata before autogenerate.
# If a model isn't imported here, Alembic won't see its table and won't migrate it.
from app.models.user import User  # noqa: E402,F401
from app.models.product import Product  # noqa: E402,F401
from app.models.order import Order  # noqa: E402,F401
from app.models.order_item import OrderItem  # noqa: E402,F401

config = context.config

# Inject the runtime DB URL (env-driven) into the Alembic config, overriding the blank
# sqlalchemy.url in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# WHY render_as_batch for SQLite: SQLite cannot ALTER most things in place (no DROP
# COLUMN / rename / type change without rebuilding the table). Alembic's "batch" mode
# emulates those by copying data into a rebuilt table. Dev runs on SQLite, so without
# batch mode any future column-changing migration would fail locally. Postgres needs no
# batching, so we toggle it per-engine.
render_as_batch = settings.database_url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits raw SQL via --sql)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=render_as_batch,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=render_as_batch,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
