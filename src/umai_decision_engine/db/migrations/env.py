"""Alembic environment: async engine, URL from the config set by ``db.migrate``."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from umai_decision_engine.db.models import Base

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(context.config.get_main_option("sqlalchemy.url"))
    async with engine.begin() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=context.config.get_main_option("sqlalchemy.url"))
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
