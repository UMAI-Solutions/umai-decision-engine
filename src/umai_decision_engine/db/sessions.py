"""Console sessions (ADR-0024 §2 amendment, contract console-sessions 0.1). Global table: every
query names a session id's hash or the person (``user_id``); nothing here reads a tenant table.
The session id itself is never stored, only its SHA-256."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.db.models import ConsoleSessionRow, User
from umai_decision_engine.db.repositories import Conflict
from umai_decision_engine.db.session import global_session

TOUCH_AFTER = timedelta(minutes=5)  # last_seen_at is written at most this often per session
RETENTION = timedelta(days=30)  # a record is deleted this long after its expires_at


def _now() -> datetime:
    return datetime.now(UTC)


def session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionInfo:
    id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    device: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None


def _info(row: ConsoleSessionRow, subject: str) -> SessionInfo:
    return SessionInfo(
        row.id,
        row.user_id,
        subject,
        row.device,
        row.created_at,
        row.last_seen_at,
        row.expires_at,
        row.revoked_at,
        row.revoke_reason,
    )


class SessionRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        lifetime: timedelta,
        idle: timedelta,
        limit: int,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.engine = engine
        self.lifetime = lifetime
        self.idle = idle
        self.limit = limit
        self.clock = clock

    def is_live(self, info: SessionInfo) -> bool:
        now = self.clock()
        return (
            info.revoked_at is None
            and now < info.expires_at
            and now - info.last_seen_at <= self.idle
        )

    def _live(self, now: datetime):
        return (
            ConsoleSessionRow.revoked_at.is_(None),
            ConsoleSessionRow.expires_at > now,
            ConsoleSessionRow.last_seen_at >= now - self.idle,
        )

    async def start(self, user_id: uuid.UUID, session_id: str, device: str | None) -> SessionInfo:
        """Records a new sign-in. One person's concurrent starts queue on their user row, so the
        limit holds: beyond it, the least recently seen live sessions end (reason ``limit``).
        Records past their retention are deleted here (one indexed statement)."""

        now = self.clock()
        async with global_session(self.engine) as session:
            subject = await session.scalar(
                select(User.subject).where(User.id == user_id).with_for_update()
            )
            await session.execute(
                delete(ConsoleSessionRow).where(ConsoleSessionRow.expires_at < now - RETENTION)
            )
            row = ConsoleSessionRow(
                sid_sha256=session_hash(session_id),
                user_id=user_id,
                device=device,
                created_at=now,
                last_seen_at=now,
                expires_at=now + self.lifetime,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise Conflict("the session is already recorded") from exc
            live = list(
                await session.scalars(
                    select(ConsoleSessionRow)
                    .where(ConsoleSessionRow.user_id == user_id, *self._live(now))
                    .order_by(ConsoleSessionRow.last_seen_at.desc(), ConsoleSessionRow.id)
                )
            )
            for old in [s for s in live if s.id != row.id][max(0, self.limit - 1) :]:
                old.revoked_at, old.revoke_reason = now, "limit"
            return _info(row, subject or "")

    async def find(self, session_id: str) -> SessionInfo | None:
        async with global_session(self.engine) as session:
            found = (
                await session.execute(
                    select(ConsoleSessionRow, User.subject)
                    .join(User, User.id == ConsoleSessionRow.user_id)
                    .where(ConsoleSessionRow.sid_sha256 == session_hash(session_id))
                )
            ).first()
            return _info(*found) if found else None

    async def touch(self, info: SessionInfo) -> None:
        """Last seen, written at most every :data:`TOUCH_AFTER` (the guard is in the statement,
        so concurrent requests write once)."""

        now = self.clock()
        if now - info.last_seen_at < TOUCH_AFTER:
            return
        async with global_session(self.engine) as session:
            await session.execute(
                update(ConsoleSessionRow)
                .where(
                    ConsoleSessionRow.id == info.id,
                    ConsoleSessionRow.last_seen_at < now - TOUCH_AFTER,
                )
                .values(last_seen_at=now)
            )

    async def live(self, user_id: uuid.UUID) -> list[SessionInfo]:
        """The person's live sessions, most recently seen first."""

        now = self.clock()
        async with global_session(self.engine) as session:
            rows = await session.execute(
                select(ConsoleSessionRow, User.subject)
                .join(User, User.id == ConsoleSessionRow.user_id)
                .where(ConsoleSessionRow.user_id == user_id, *self._live(now))
                .order_by(ConsoleSessionRow.last_seen_at.desc(), ConsoleSessionRow.id)
            )
            return [_info(row, subject) for row, subject in rows]

    async def revoke(self, user_id: uuid.UUID, record_id: uuid.UUID, reason: str) -> bool:
        """Ends one of the person's live sessions; False when there is no such live session."""

        now = self.clock()
        async with global_session(self.engine) as session:
            result = await session.execute(
                update(ConsoleSessionRow)
                .where(
                    ConsoleSessionRow.id == record_id,
                    ConsoleSessionRow.user_id == user_id,
                    *self._live(now),
                )
                .values(revoked_at=now, revoke_reason=reason)
            )
            return result.rowcount == 1

    async def revoke_others(self, user_id: uuid.UUID, keep: uuid.UUID) -> int:
        now = self.clock()
        async with global_session(self.engine) as session:
            result = await session.execute(
                update(ConsoleSessionRow)
                .where(
                    ConsoleSessionRow.user_id == user_id,
                    ConsoleSessionRow.id != keep,
                    *self._live(now),
                )
                .values(revoked_at=now, revoke_reason="signed_out_elsewhere")
            )
            return result.rowcount
