"""Tenant isolation on PostgreSQL (ADR-0025 Validation): the schema test, the generated
cross-tenant tests and the role test. They need a real Postgres and run only when

    UMAI_DE_TEST_POSTGRES_ADMIN_URL   owner role (migrations)        postgresql+asyncpg://…
    UMAI_DE_TEST_POSTGRES_APP_URL     application role (no BYPASSRLS)

are set (CI: service container; locally: deploy/postgres-init.sql on any Postgres ≥ 16).
The SQLite part at the end runs everywhere and covers the repositories' own tenant filter.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.models import Base
from umai_decision_engine.db.repositories import (
    ApiKeyRepository,
    DraftRepository,
    NotFound,
    TenantRepository,
)
from umai_decision_engine.db.rls import GLOBAL_TABLES, POLICY_NAME, TENANT_TABLES
from umai_decision_engine.db.session import global_session, make_engine, sqlite_url, tenant_session

ADMIN_URL = os.environ.get("UMAI_DE_TEST_POSTGRES_ADMIN_URL")
APP_URL = os.environ.get("UMAI_DE_TEST_POSTGRES_APP_URL")
postgres = pytest.mark.skipif(not (ADMIN_URL and APP_URL), reason="no test Postgres configured")

TENANT_MODELS = {t.name: t for t in Base.metadata.sorted_tables if t.name in TENANT_TABLES}


@pytest.fixture(scope="module")
def migrated() -> None:
    """Fresh schema once per module (owner role), then the migrations."""

    async def _reset() -> None:
        admin = make_engine(ADMIN_URL)
        async with admin.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("GRANT USAGE ON SCHEMA public TO umai_app"))
        await admin.dispose()

    if ADMIN_URL and APP_URL:
        asyncio.run(_reset())
        upgrade(ADMIN_URL)


@pytest_asyncio.fixture
async def pg(migrated):
    """Application-role engine per test (engines are bound to the test's event loop)."""

    app = make_engine(APP_URL)
    yield app
    await app.dispose()


@pytest_asyncio.fixture
async def two_tenants(pg):
    repo = TenantRepository(pg)
    a = await repo.get_or_create(f"a-{uuid.uuid4().hex[:8]}", "eu")
    b = await repo.get_or_create(f"b-{uuid.uuid4().hex[:8]}", "eu")
    return a, b


def _row(table: str, tenant_id: uuid.UUID) -> dict:
    """A minimal valid row for each tenant table (synthetic values)."""

    base = {"id": uuid.uuid4(), "tenant_id": tenant_id}
    if table == "api_keys":
        return {
            **base,
            "key_id": uuid.uuid4().hex[:8],
            "secret_sha256": "0" * 64,
            "env": "live",
            "region": "eu",
            "scopes": ["evaluate"],
            "name": "t",
            "created_by": "t",
        }
    if table == "drafts":
        return {**base, "bundle_id": uuid.uuid4().hex[:8], "base_version": 1, "bundle": {}}
    raise AssertionError(f"no row factory for {table}: add one when adding a tenant table")


# ------------------------------------------------------------ schema test (I1)


@postgres
@pytest.mark.asyncio
async def test_every_table_is_tenant_scoped_with_rls_or_allowlisted(pg) -> None:
    async with pg.connect() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
                    " EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid"
                    "   AND p.polname = :policy) AS has_policy,"
                    " EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid"
                    "   AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS has_tenant"
                    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind = 'r'"
                ),
                {"policy": POLICY_NAME},
            )
        ).all()
    names = {row[0] for row in tables}
    assert set(TENANT_TABLES) <= names
    for name, rls, force, has_policy, has_tenant in tables:
        if name in GLOBAL_TABLES:
            continue
        assert name in TENANT_TABLES, f"{name}: not in TENANT_TABLES nor GLOBAL_TABLES"
        assert has_tenant and rls and force and has_policy, f"{name}: RLS incomplete"


# ------------------------------------------------------------ generated cross-tenant tests (I2)


@postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_write_as_a_read_as_b_returns_nothing(pg, two_tenants, table) -> None:
    a, b = two_tenants
    model = TENANT_MODELS[table]
    async with tenant_session(pg, a.id) as session:
        await session.execute(insert(model).values(**_row(table, a.id)))
    async with tenant_session(pg, b.id) as session:
        rows = (await session.execute(select(model))).all()  # no WHERE on purpose
    assert rows == []
    async with tenant_session(pg, a.id) as session:
        assert len((await session.execute(select(model))).all()) >= 1


@postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_no_tenant_context_is_an_error_not_an_empty_page(pg, table) -> None:
    model = TENANT_MODELS[table]
    with pytest.raises(DBAPIError):
        async with global_session(pg) as session:
            await session.execute(select(model))


@postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_with_check_rejects_foreign_tenant_inserts(pg, two_tenants, table) -> None:
    a, b = two_tenants
    model = TENANT_MODELS[table]
    with pytest.raises(ProgrammingError):
        async with tenant_session(pg, b.id) as session:
            await session.execute(insert(model).values(**_row(table, a.id)))


# ------------------------------------------------------------ role test (I3)


@postgres
@pytest.mark.asyncio
async def test_application_role_cannot_bypass_rls_or_own_tenant_tables(pg) -> None:
    async with pg.connect() as conn:
        role = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcreaterole,"
                    " CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'azure_pg_admin')"
                    "   THEN pg_has_role(current_user, 'azure_pg_admin', 'member') ELSE false END"
                    " FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        owned = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                    " AND tableowner = current_user"
                )
            )
        ).all()
    superuser, bypass, createrole, azure_admin = role
    assert not superuser and not bypass and not createrole
    assert not azure_admin
    assert owned == []


# ------------------------------------------------------------ SQLite: repository filter


@pytest.mark.asyncio
async def test_repositories_filter_by_tenant_on_sqlite(tmp_path) -> None:
    url = sqlite_url(tmp_path / "c.sqlite")
    upgrade(url)
    engine = make_engine(url)
    try:
        tenants = TenantRepository(engine)
        a = await tenants.create("tenant-a", "A", "sh")
        b = await tenants.create("tenant-b", "B", "sh")
        keys, drafts = ApiKeyRepository(engine), DraftRepository(engine)
        record, _ = await keys.create(
            a, name="k", env="live", region="sh", scopes=None, created_by="t"
        )
        await drafts.create(a, "gr", 1, {"id": "gr"})
        assert [k.key_id for k in await keys.list(b)] == []
        assert await drafts.get(b, "gr") is None
        assert await drafts.ids(b) == set()
        with pytest.raises(NotFound):
            await keys.revoke(b, record.key_id)
        assert (await keys.snapshot(b)).keys == []
        assert (await keys.snapshot(a)).keys[0].key_id == record.key_id
    finally:
        await engine.dispose()
