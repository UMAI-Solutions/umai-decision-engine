"""Console sessions in the policy service (ADR-0024 §2 amendment, contract console-sessions 0.1).

Two checks use the record: :meth:`SessionService.verify` on every call that carries an
assertion (the assertion's ``session_id`` must name a live session of the same person), and
:meth:`SessionService.check` for the console middleware, which also answers whether the person
is still a member of the session's organization. A person lists and ends their own sessions.
Logs carry record ids and reasons, never a session id."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from umai_decision_engine.contracts.sessions import (
    ConsoleSession,
    SessionCheck,
    SessionStart,
    SessionStatus,
)
from umai_decision_engine.control_plane.authz import Actor
from umai_decision_engine.db.organizations import MembershipRepository, UserInfo
from umai_decision_engine.db.repositories import Conflict, TenantRepository
from umai_decision_engine.db.sessions import SessionInfo, SessionRepository

log = logging.getLogger("umai.control_plane.sessions")


class SessionError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _ended(reason: str) -> SessionError:
    return SessionError(401, "session_ended", f"your console session {reason}: sign in again")


def session_view(info: SessionInfo, current: uuid.UUID | None) -> dict[str, Any]:
    return ConsoleSession(
        id=info.id,
        device=info.device,
        created_at=info.created_at,
        last_seen_at=info.last_seen_at,
        expires_at=info.expires_at,
        current=info.id == current,
    ).model_dump(mode="json")


class SessionService:
    def __init__(
        self,
        sessions: SessionRepository,
        tenants: TenantRepository,
        memberships: MembershipRepository,
        *,
        mode: Literal["required", "optional"],
    ) -> None:
        self.sessions = sessions
        self.tenants = tenants
        self.memberships = memberships
        self.mode = mode

    async def verify(self, actor: Actor) -> SessionInfo | None:
        """The live session the person's assertion names. Unknown: refused in ``required`` mode,
        allowed (None) in ``optional``. Ended, or another person's: always refused."""

        info = await self.sessions.find(actor.session_id or "")
        if info is None:
            if self.mode == "required":
                log.warning("session.refused actor=%s reason=unknown", actor.label)
                raise _ended("is unknown")
            return None
        if info.subject != actor.sub or not self.sessions.is_live(info):
            log.warning("session.refused actor=%s session=%s reason=ended", actor.label, info.id)
            raise _ended("has ended")
        await self.sessions.touch(info)
        return info

    async def start(self, actor: Actor, user: UserInfo, body: SessionStart) -> dict[str, Any]:
        try:
            info = await self.sessions.start(user.id, actor.session_id or "", body.device)
        except Conflict as exc:
            raise SessionError(409, "session_exists", "this session is already recorded") from exc
        log.info("session.started user=%s session=%s", user.id, info.id)
        return session_view(info, info.id)

    async def mine(self, user: UserInfo, current: SessionInfo | None) -> list[dict[str, Any]]:
        current_id = current.id if current else None
        return [session_view(s, current_id) for s in await self.sessions.live(user.id)]

    async def end(self, user: UserInfo, record_id: uuid.UUID, current: SessionInfo | None) -> None:
        reason = "signed_out" if current and current.id == record_id else "revoked"
        if not await self.sessions.revoke(user.id, record_id, reason):
            raise SessionError(404, "not_found", "no such session")
        log.info("session.ended user=%s session=%s reason=%s", user.id, record_id, reason)

    async def end_current(self, user: UserInfo, current: SessionInfo | None) -> None:
        """Sign-out. Without a record (``optional`` mode) there is nothing to end."""

        if current is not None:
            await self.end(user, current.id, current)

    async def end_others(self, user: UserInfo, current: SessionInfo | None) -> int:
        if current is None:
            raise _ended("is unknown")  # keeping "this one" needs a record of it
        revoked = await self.sessions.revoke_others(user.id, current.id)
        log.info("session.ended_others user=%s kept=%s revoked=%d", user.id, current.id, revoked)
        return revoked

    async def check(self, body: SessionCheck) -> SessionStatus:
        """The console middleware's question (operator call): is this session alive, and is its
        person still an active member of the active organization?"""

        info = await self.sessions.find(body.session_id)
        if info is None:
            return SessionStatus(state="unknown")
        if info.subject != body.subject or not self.sessions.is_live(info):
            return SessionStatus(state="ended")
        await self.sessions.touch(info)
        member = None
        if body.tenant_id is not None:
            tenant = await self.tenants.get(body.tenant_id)
            member = (
                tenant is not None
                and tenant.status == "active"
                and await self.memberships.is_member(tenant, info.user_id)
            )
        return SessionStatus(state="active", member=member)
