"""Alembic environment. The database URL comes from DATABASE_URL (see config.py)."""

import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import pool

from alembic import context
from kilometrix.db import Base, make_engine

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = os.environ.get("DATABASE_URL", "sqlite:///./kilometrix.db")
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
