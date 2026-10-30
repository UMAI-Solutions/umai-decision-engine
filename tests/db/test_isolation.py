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
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError

from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.models import Base, Membership, User
from umai_decision_engine.db.organizations import MembershipRepository, UserRepository
from umai_decision_engine.db.repositories import (
    ApiKeyRepository,
    DraftRepository,
    NotFound,
    TenantRepository,
)
from umai_decision_engine.db.rls import (
    APPEND_ONLY_TABLES,
    GLOBAL_TABLES,
    POLICY_NAME,
    SELF_POLICY_NAME,
    SELF_READ_TABLES,
    TENANT_TABLES,
)
from umai_decision_engine.db.session import (
    global_session,
    make_engine,
    sqlite_url,
    tenant_session,
    user_session,
)

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


async def _user(engine) -> uuid.UUID:
    """A synthetic person in the global users table (memberships reference one)."""

    user_id = uuid.uuid4()
    async with global_session(engine) as session:
        await session.execute(
            insert(User).values(
                id=user_id,
                subject=f"auth0|{user_id.hex[:12]}",
                email=f"{user_id.hex[:8]}@example.com",
                email_verified=True,
            )
        )
    return user_id


def _row(table: str, tenant_id: uuid.UUID, user_id: uuid.UUID | None = None) -> dict:
    """A minimal valid row for each tenant table (synthetic values)."""

    base = {"id": uuid.uuid4(), "tenant_id": tenant_id}
    if table == "memberships":
        return {**base, "user_id": user_id, "roles": ["owner"], "state": "active"}
    if table == "invitations":
        return {
            **base,
            "email": "grace@example.com",
            "roles": ["viewer"],
            "token_sha256": uuid.uuid4().hex * 2,
            "invited_by": uuid.uuid4(),
            "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
        }
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
    record = {**base, "event_id": f"ta-{uuid.uuid4().hex}", "occurred_at": datetime.now(UTC)}
    if table == "decision_events":
        return {
            **record,
            "source": "api",
            "request_id": "req-i",
            "engine_version": "0.1.0",
            "bundle_id": "gr-test",
            "phase": "PRE_LLM",
            "action": "ALLOW",
            "severity": "LOW",
            "decided_by": "L1a",
            "degraded": False,
            "review": False,
            "review_reasons": [],
            "entities": [],
            "tags": [],
            "matched": [],
            "errors": [],
            "latency_total_ms": 1.0,
        }
    if table == "l1b_outcomes":
        return {
            **record,
            "bundle_id": "gr-test",
            "phase": "PRE_LLM",
            "policy_id": "prompt-attack",
            "policy_version": 1,
            "enforcement": "shadow",
            "outcome": "decided",
            "tags": [],
            "review_reasons": [],
        }
    if table == "usage_events":
        return {
            **record,
            "meter": "evaluations",
            "quantity": 1,
            "source": "api",
            "degraded": False,
        }
    if table == "billing_accounts":
        return {
            **base,
            "company_name": "Acme Teknoloji A.Ş.",
            "tax_number": "1234567890",
            "tax_office": "Kadıköy",
            "contact_name": "Ayşe",
            "contact_surname": "Demir",
            "email": "billing@example.com",
            "phone": "+905551112233",
            "address": "Örnek Mah. 1",
            "city": "İstanbul",
            "country": "Turkey",
            "updated_by": "person:test",
        }
    if table == "subscriptions":
        ref = f"sub-{uuid.uuid4().hex[:8]}"
        return {
            **base,
            "provider": "iyzico",
            "parent_reference": ref,
            "reference": ref,
            "pricing_plan_reference": "plan-1",
            "edition": "pro",
            "status": "active",
            "retry_count": 0,
        }
    if table == "billing_checkouts":
        return {
            **base,
            "checkout_id": f"co-{uuid.uuid4().hex[:8]}",
            "edition": "pro",
            "pricing_plan_reference": "plan-1",
            "status": "open",
            "created_by": "person:test",
            "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
        }
    if table == "billing_events":
        return {**base, "kind": "checkout_started", "occurred_at": datetime.now(UTC)}
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
    user = await _user(pg)
    async with tenant_session(pg, a.id) as session:
        await session.execute(insert(model).values(**_row(table, a.id, user)))
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
    user = await _user(pg)
    with pytest.raises(ProgrammingError):
        async with tenant_session(pg, b.id) as session:
            await session.execute(insert(model).values(**_row(table, a.id, user)))


# ------------------------------------------------------------ append-only records (ADR-0041)


@postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
async def test_records_are_append_only_for_the_application_role(pg, two_tenants, table) -> None:
    a, _ = two_tenants
    model = TENANT_MODELS[table]
    async with tenant_session(pg, a.id) as session:
        await session.execute(insert(model).values(**_row(table, a.id)))
    changed = {"kind": "changed"} if table == "billing_events" else {"event_id": "ta-changed"}
    for statement in (update(model).values(**changed), delete(model)):
        with pytest.raises(DBAPIError):  # permission denied: INSERT and SELECT only
            async with tenant_session(pg, a.id) as session:
                await session.execute(statement)
    async with tenant_session(pg, a.id) as session:
        assert len((await session.execute(select(model))).all()) == 1


@postgres
@pytest.mark.asyncio
async def test_the_records_writer_writes_once_and_only_for_its_tenant(pg, two_tenants) -> None:
    from umai_decision_engine.contracts.events import UsageRecord
    from umai_decision_engine.db.models import UsageEventRow
    from umai_decision_engine.events.database import DatabaseRecordWriter

    a, b = two_tenants
    record = UsageRecord(
        event_id=f"ta-{uuid.uuid4().hex}",
        tenant_id=a.slug,
        occurred_at=datetime.now(UTC),
        meter="evaluations",
        quantity=1,
        source="api",
    )
    writer = DatabaseRecordWriter(pg)
    assert (await writer.write([record])).written == 1
    assert (await writer.write([record])).written == 1  # retried: ON CONFLICT DO NOTHING
    async with tenant_session(pg, a.id) as session:
        assert len((await session.execute(select(UsageEventRow))).all()) == 1
    async with tenant_session(pg, b.id) as session:
        assert (await session.execute(select(UsageEventRow))).all() == []


@postgres
@pytest.mark.asyncio
async def test_retention_runs_as_owner_under_forced_rls_and_keeps_recent_rows(
    pg, two_tenants
) -> None:
    from umai_decision_engine.control_plane.records import apply_retention
    from umai_decision_engine.db.models import DecisionEventRow

    a, b = two_tenants
    now = datetime.now(UTC)
    old = {**_row("decision_events", a.id), "occurred_at": now - timedelta(days=45)}
    other_old = {**_row("decision_events", b.id), "occurred_at": now - timedelta(days=45)}
    recent = _row("decision_events", a.id)
    async with tenant_session(pg, a.id) as session:
        await session.execute(insert(DecisionEventRow).values(**old))
        await session.execute(insert(DecisionEventRow).values(**recent))
    async with tenant_session(pg, b.id) as session:
        await session.execute(insert(DecisionEventRow).values(**other_old))
    owner = make_engine(ADMIN_URL)
    try:
        deleted = await apply_retention(owner, decision_days=30, usage_days=400, now=now)
    finally:
        await owner.dispose()
    assert deleted["decision_events"] >= 2  # both tenants' old rows, tenant by tenant
    for tenant, expected in ((a, [recent["event_id"]]), (b, [])):
        async with tenant_session(pg, tenant.id) as session:
            rows = (await session.execute(select(DecisionEventRow.event_id))).scalars().all()
        assert rows == expected


@postgres
@pytest.mark.asyncio
async def test_summary_and_daily_usage_group_by_utc_day_on_postgres(pg, two_tenants) -> None:
    from umai_decision_engine.control_plane.records import RecordsReader
    from umai_decision_engine.db.models import DecisionEventRow, UsageEventRow

    a, b = two_tenants
    late = datetime(2026, 10, 30, 23, 30, tzinfo=UTC)  # still the 30th in UTC
    async with tenant_session(pg, a.id) as session:
        for at in (late, late + timedelta(hours=1)):  # the second is the 31st
            await session.execute(
                insert(DecisionEventRow).values(
                    **{**_row("decision_events", a.id), "occurred_at": at}
                )
            )
            await session.execute(
                insert(UsageEventRow).values(**{**_row("usage_events", a.id), "occurred_at": at})
            )
    reader = RecordsReader(pg)
    summary = await reader.summary(
        a, since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 2, tzinfo=UTC)
    )
    assert [d.date for d in summary.by_day] == ["2026-10-30", "2026-10-31"]
    usage = await reader.usage(a, "2026-10")
    assert [d.date for d in usage.daily] == ["2026-10-30", "2026-10-31"]
    other = await reader.summary(
        b, since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 2, tzinfo=UTC)
    )
    assert other.total == 0


@postgres
@pytest.mark.asyncio
async def test_quota_counts_requests_of_its_own_tenant_only_on_postgres(pg, two_tenants) -> None:
    """ADR-0043 D2, D6: API evaluations and gateway calls count, the gateway's own checks do
    not; test keys apart; under forced RLS, as the engine's ledger reader runs."""

    from umai_decision_engine.db.models import UsageEventRow
    from umai_decision_engine.events.ledger import LedgerCounts

    a, b = two_tenants
    at = datetime(2026, 10, 30, 12, tzinfo=UTC)
    rows = [
        ("evaluations", "api", "live", 1),
        ("evaluations", "api", None, 1),  # the operator token counts as live
        ("evaluations", "api", "test", 1),
        ("gateway_calls", "gateway", "live", 1),
        ("evaluations", "gateway", "live", 1),  # a gateway check: not a request
        ("provider_input_tokens", "gateway", "live", 500),
    ]
    for tenant in (a, b):
        async with tenant_session(pg, tenant.id) as session:
            for meter, source, environment, quantity in rows[: 6 if tenant is a else 1]:
                await session.execute(
                    insert(UsageEventRow).values(
                        **{
                            **_row("usage_events", tenant.id),
                            "occurred_at": at,
                            "meter": meter,
                            "source": source,
                            "environment": environment,
                            "quantity": quantity,
                        }
                    )
                )
    ledger = LedgerCounts(pg)
    start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
    assert await ledger.month(a.slug, start, end) == {"live": 3, "test": 1}
    assert await ledger.month(b.slug, start, end) == {"live": 1, "test": 0}
    assert await ledger.month("nobody", start, end) == {"live": 0, "test": 0}


# ------------------------------------------------------------ member_self (ADR-0033 §2)


@postgres
@pytest.mark.asyncio
async def test_member_self_reads_only_the_persons_own_rows_and_never_writes(
    pg, two_tenants
) -> None:
    a, b = two_tenants
    ada, grace = await _user(pg), await _user(pg)
    for tenant, user in ((a, ada), (b, ada), (a, grace)):
        async with tenant_session(pg, tenant.id) as session:
            await session.execute(insert(Membership).values(**_row("memberships", tenant.id, user)))
    async with user_session(pg, ada) as session:
        rows = (await session.execute(select(Membership))).scalars().all()  # no WHERE
    assert sorted(r.tenant_id for r in rows) == sorted([a.id, b.id])
    assert {r.user_id for r in rows} == {ada}
    with pytest.raises(ProgrammingError):  # read-only: the tenant policy refuses the write
        async with user_session(pg, ada) as session:
            await session.execute(insert(Membership).values(**_row("memberships", a.id, ada)))


@postgres
@pytest.mark.asyncio
async def test_only_the_allowlisted_tables_have_the_self_policy(pg) -> None:
    async with pg.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.relname, p.polcmd::text FROM pg_policy p JOIN pg_class c"
                    " ON c.oid = p.polrelid WHERE p.polname = :name"
                ),
                {"name": SELF_POLICY_NAME},
            )
        ).all()
    assert sorted(r[0] for r in rows) == sorted(SELF_READ_TABLES)
    assert {r[1] for r in rows} == {"r"}  # SELECT only


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

        users, members = UserRepository(engine), MembershipRepository(engine)
        ada = await users.upsert("auth0|ada", "ada@example.com", True, None)
        grace = await users.upsert("auth0|grace", "grace@example.com", True, None)
        await members.add(a, ada, ["owner"])
        await members.add(b, grace, ["owner"])
        assert [m.slug for m in await members.mine(ada)] == ["tenant-a"]
        assert [m.user.email for m in await members.list(b)] == ["grace@example.com"]
        assert await members.roles(b, ada) == frozenset()
    finally:
        await engine.dispose()


# ------------------------------------------------------------ console sessions (ADR-0024 §2)


@postgres
@pytest.mark.asyncio
async def test_console_sessions_are_global_and_the_application_role_manages_them(pg) -> None:
    """A person's sign-ins span tenants: no tenant context, no RLS; the application role starts,
    ends and purges them (migration 0011's grant)."""

    from umai_decision_engine.db.models import ConsoleSessionRow
    from umai_decision_engine.db.sessions import SessionRepository

    user_id = await _user(pg)
    repo = SessionRepository(pg, lifetime=timedelta(hours=12), idle=timedelta(minutes=70), limit=10)
    sid = uuid.uuid4().hex + uuid.uuid4().hex
    started = await repo.start(user_id, sid, "Firefox on Windows")
    found = await repo.find(sid)
    assert found is not None and found.id == started.id and repo.is_live(found)
    assert [s.id for s in await repo.live(user_id)] == [started.id]
    assert await repo.revoke(user_id, started.id, "signed_out")
    async with global_session(pg) as session:  # no tenant set: a global table answers
        rows = (await session.execute(select(ConsoleSessionRow.revoke_reason))).all()
        assert ("signed_out",) in rows
        await session.execute(delete(ConsoleSessionRow).where(ConsoleSessionRow.id == started.id))
