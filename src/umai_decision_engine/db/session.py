"""Engines and sessions. :func:`tenant_session` is the only way to reach a tenant table
(ADR-0025 §6): one transaction, ``app.tenant_id`` set with ``SET LOCAL`` semantics so
transaction-pooling proxies are safe and the setting dies with the transaction."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from umai_decision_engine.db.rls import TENANT_SETTING, USER_SETTING

NO_TENANT = uuid.UUID(int=0)  # matches no tenant row: user sessions read only their own


def sqlite_url(path: Path | str) -> str:
    if str(path) == ":memory:":
        return "sqlite+aiosqlite://"
    return f"sqlite+aiosqlite:///{Path(path).as_posix()}"


def make_engine(url: str) -> AsyncEngine:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {}
    return create_async_engine(url, **kwargs)


def is_postgres(engine: AsyncEngine) -> bool:
    return engine.dialect.name == "postgresql"


@asynccontextmanager
async def global_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """For global (allowlisted) tables only. Touching a tenant table from here fails on
    Postgres: ``current_setting`` without ``missing_ok`` raises when no tenant is set."""

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        yield session


@asynccontextmanager
async def tenant_session(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        if is_postgres(engine):
            # set_config(..., is_local=true) == SET LOCAL, but accepts a bind parameter.
            await session.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": TENANT_SETTING, "value": str(tenant_id)},
            )
        yield session


@asynccontextmanager
async def user_session(engine: AsyncEngine, user_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A person's own memberships across tenants (``member_self``, ADR-0033 §2), before a tenant
    is chosen: ``app.tenant_id`` is the nil UUID, so ``tenant_isolation`` matches nothing and
    only the person's rows are visible, read-only. On SQLite the repositories filter by user."""

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        if is_postgres(engine):
            for name, value in ((TENANT_SETTING, NO_TENANT), (USER_SETTING, user_id)):
                await session.execute(
                    text("SELECT set_config(:name, :value, true)"),
                    {"name": name, "value": str(value)},
                )
        yield session
