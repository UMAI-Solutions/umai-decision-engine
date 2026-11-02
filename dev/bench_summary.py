"""Cost of ``GET /v1/decision-events/summary`` (tagged-action 0.7) on a store with many synthetic
decision events: ``RecordsReader.summary`` timed as the policy service runs it, as the
application role under row-level security. Run it with this branch's ``src`` and with an older
one on ``PYTHONPATH`` to compare; the seeded tenants are reused (``--no-seed``).

python dev/bench_summary.py --database-url <app-role url> --admin-url <owner url>
    [--events 100000 1000000] [--runs 5] [--no-seed]

Events are made by PostgreSQL itself (``generate_series``), spread over the last 30 days: eight
applications and a few events without one, 85 % allowed, entity types and policies on the
others, an L1b call on 30 %. Synthetic ids and types only; no content.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from umai_decision_engine.control_plane.records import EventFilters, RecordsReader
from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.repositories import TenantRepository
from umai_decision_engine.db.session import make_engine, tenant_session

# One statement per tenant; random() makes the mix, the window is the 30 days before now.
SEED = """
INSERT INTO decision_events (
    id, tenant_id, event_id, occurred_at, source, request_id, engine_version, key_id,
    environment, application_id, bundle_id, bundle_version, phase, action, severity, decided_by,
    degraded, review, review_reasons, entities, tags, matched, errors,
    latency_total_ms, latency_l0_ms, latency_l1b_ms
)
SELECT
    gen_random_uuid(), CAST(:tenant AS uuid), 'ta-' || md5(:slug || i::text), r.at,
    CASE WHEN r.s < 0.7 THEN 'gateway' ELSE 'api' END, 'req-' || i::text, '0.1.0',
    CASE WHEN r.app IS NULL THEN NULL ELSE 'Key' || lpad(r.app::text, 4, '0') END,
    CASE WHEN r.app IS NULL THEN NULL ELSE 'live' END,
    CASE WHEN r.app IS NULL THEN NULL ELSE 'app-' || r.app::text END,
    'gr-demo-data-protection', 5, 'PRE_LLM', r.action,
    CASE WHEN r.action = 'BLOCK' THEN 'HIGH' ELSE 'LOW' END, 'L0', false, false,
    '[]', CAST(r.entities AS json), '[]', CAST(r.matched AS json), '[]',
    r.l0 + coalesce(r.l1b, 0) + 0.4, r.l0, r.l1b
FROM generate_series(1, :n) AS i,
LATERAL (
    SELECT
        now() - random() * interval '30 days' AS at,
        random() AS s,
        CASE WHEN random() < 0.03 THEN NULL ELSE 1 + floor(random() * 8)::int END AS app,
        a.action,
        CASE WHEN a.action = 'ALLOW' THEN '[]'
             ELSE '[{"type": "EMAIL_ADDRESS", "count": 1, "distinct": 1}]' END AS entities,
        CASE WHEN a.action = 'ALLOW' THEN '[]'
             ELSE '[{"kind": "l0_policy", "id": "contact-data", "version": 1, "action": "'
                  || a.action || '"}]' END AS matched,
        1 + random() * 12 AS l0,
        CASE WHEN random() < 0.3 THEN 180 + random() * 400 END AS l1b
    FROM (
        SELECT CASE
            WHEN u < 0.85 THEN 'ALLOW'
            WHEN u < 0.91 THEN 'ALLOW_WITH_MODIFICATIONS'
            WHEN u < 0.95 THEN 'BLOCK'
            WHEN u < 0.99 THEN 'ALLOW_WITH_WARNINGS'
            ELSE 'STEP_UP_APPROVAL' END AS action
        FROM (SELECT random() + 0 * i AS u) AS d
    ) AS a
) AS r
"""


async def _seed(engine, slug: str, n: int) -> None:
    tenant = await TenantRepository(engine).get_or_create(slug, "eu")
    async with tenant_session(engine, tenant.id) as session:
        existing = await session.scalar(
            text("SELECT count(*) FROM decision_events WHERE tenant_id = :t"), {"t": tenant.id}
        )
    for start in range(existing, n, 200_000):  # one transaction per 200 000 rows
        params = {"tenant": str(tenant.id), "slug": f"{slug}-{start}", "n": min(200_000, n - start)}
        async with tenant_session(engine, tenant.id) as session:
            await session.execute(text(SEED), params)
    async with engine.begin() as conn:
        await conn.execute(text("ANALYZE decision_events"))


async def _time(reader: RecordsReader, tenant, filters: EventFilters, runs: int) -> list[float]:
    await reader.summary(tenant, filters)  # warm-up: plan and pages cached, as in service
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        await reader.summary(tenant, filters)
        times.append((time.perf_counter() - started) * 1000)
    return times


async def main(args: argparse.Namespace) -> None:
    if args.admin_url:
        upgrade(args.admin_url)
    engine = make_engine(args.database_url)
    reader = RecordsReader(engine)
    now = datetime.now(UTC)
    windows = {"24 h": 1, "7 d": 7, "30 d": 30}
    print("| Events in the tenant | Window | Filter | median ms | min ms | max ms |")
    print("|---|---|---|---|---|---|")
    for n in args.events:
        slug = f"bench-summary-{n}"
        if not args.no_seed:
            started = time.perf_counter()
            await _seed(engine, slug, n)
            print(f"<!-- seeded {slug} in {time.perf_counter() - started:.1f} s -->")
        tenant = await TenantRepository(engine).get_or_create(slug, "eu")
        for label, days in windows.items():
            variants = {"none": EventFilters(since=now - timedelta(days=days), until=now)}
            if "playground" in EventFilters.__dataclass_fields__:
                variants["playground=false"] = EventFilters(
                    since=now - timedelta(days=days), until=now, playground=False
                )
            for name, filters in variants.items():
                times = await _time(reader, tenant, filters, args.runs)
                print(
                    f"| {n:,} | {label} | {name} | {statistics.median(times):.0f} | "
                    f"{min(times):.0f} | {max(times):.0f} |"
                )
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--admin-url")
    parser.add_argument("--events", type=int, nargs="+", default=[100_000, 1_000_000])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--no-seed", action="store_true")
    asyncio.run(main(parser.parse_args()))
