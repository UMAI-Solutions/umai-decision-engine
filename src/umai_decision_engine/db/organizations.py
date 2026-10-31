"""People, memberships and invitations (ADR-0033 §2–§5, contract organizations 0.2).

Like ``repositories``: every tenant-table query filters on ``tenant_id`` explicitly (defence in
depth on Postgres, the only guard on SQLite); a person's own memberships across tenants are read
through :func:`user_session` (``member_self``). Account metadata only, never content."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.db.models import Invitation, Membership, Tenant, User
from umai_decision_engine.db.repositories import Conflict, NotFound, TenantInfo, tenant_info
from umai_decision_engine.db.session import (
    enter_tenant,
    global_session,
    tenant_session,
    user_session,
)

OWNER = "owner"


def _now() -> datetime:
    return datetime.now(UTC)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LastOwner(Exception):
    """The change would leave an organization without an active owner (ADR-0033 §5)."""


class OwnerOnly(Exception):
    """Only an owner grants, removes or changes ownership (ADR-0033 §5)."""


class OwnedLimit(Exception):
    """The person already owns as many organizations of the edition as allowed (ADR-0033 §4)."""


class InvitationRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UserInfo:
    id: uuid.UUID
    subject: str
    email: str
    email_verified: bool
    name: str | None


@dataclass(frozen=True)
class MyMembership:
    slug: str
    name: str
    region: str
    status: str
    edition: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class MemberInfo:
    tenant_slug: str
    user: UserInfo
    roles: tuple[str, ...]
    state: str
    invited_by: uuid.UUID | None
    joined_at: datetime


@dataclass(frozen=True)
class InvitationInfo:
    id: uuid.UUID
    tenant_slug: str
    email: str
    roles: tuple[str, ...]
    invited_by: uuid.UUID
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None

    @property
    def state(self) -> str:
        if self.accepted_at:
            return "accepted"
        if self.revoked_at:
            return "revoked"
        return "expired" if self.expires_at <= _now() else "pending"


def _user(row: User) -> UserInfo:
    return UserInfo(row.id, row.subject, row.email, row.email_verified, row.name)


def _invitation(row: Invitation, slug: str) -> InvitationInfo:
    return InvitationInfo(
        row.id,
        slug,
        row.email,
        tuple(row.roles),
        row.invited_by,
        row.created_at,
        row.expires_at,
        row.accepted_at,
        row.revoked_at,
    )


class UserRepository:
    """Global table."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def upsert(
        self, subject: str, email: str, email_verified: bool, name: str | None
    ) -> UserInfo:
        """The person as the identity service last described them (assertion claims)."""

        async with global_session(self.engine) as session:
            row = await session.scalar(select(User).where(User.subject == subject))
            if row is None:
                row = User(subject=subject, email=email, email_verified=email_verified, name=name)
                session.add(row)
            else:
                row.email, row.email_verified, row.last_seen_at = email, email_verified, _now()
                if name:
                    row.name = name
            try:
                await session.flush()
            except IntegrityError:  # created concurrently: read the winner
                await session.rollback()
                return await self.require(subject)
            return _user(row)

    async def get(self, subject: str) -> UserInfo | None:
        async with global_session(self.engine) as session:
            row = await session.scalar(select(User).where(User.subject == subject))
            return _user(row) if row else None

    async def require(self, subject: str) -> UserInfo:
        user = await self.get(subject)
        if user is None:
            raise NotFound("unknown person")
        return user


class MembershipRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def mine(self, user: UserInfo) -> list[MyMembership]:
        """The person's active memberships in every tenant of this stack (the switcher)."""

        async with user_session(self.engine, user.id) as session:
            rows = await session.execute(
                select(Membership, Tenant)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(Membership.user_id == user.id, Membership.state == "active")
                .order_by(Tenant.name)
            )
            return [
                MyMembership(t.slug, t.name, t.region, t.status, t.edition, tuple(m.roles))
                for m, t in rows
            ]

    async def roles(self, tenant: TenantInfo, user: UserInfo) -> frozenset[str]:
        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(Membership).where(
                    Membership.tenant_id == tenant.id,
                    Membership.user_id == user.id,
                    Membership.state == "active",
                )
            )
            return frozenset(row.roles) if row else frozenset()

    async def is_member(self, tenant: TenantInfo, user_id: uuid.UUID) -> bool:
        """An active membership (the console's session check, ADR-0024 §2 amendment)."""

        async with tenant_session(self.engine, tenant.id) as session:
            found = await session.scalar(
                select(Membership.id).where(
                    Membership.tenant_id == tenant.id,
                    Membership.user_id == user_id,
                    Membership.state == "active",
                )
            )
            return found is not None

    async def list(self, tenant: TenantInfo) -> list[MemberInfo]:
        async with tenant_session(self.engine, tenant.id) as session:
            rows = await session.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(Membership.tenant_id == tenant.id)
                .order_by(User.email)
            )
            return [
                MemberInfo(
                    tenant.slug, _user(u), tuple(m.roles), m.state, m.invited_by, m.joined_at
                )
                for m, u in rows
            ]

    async def add(
        self,
        tenant: TenantInfo,
        user: UserInfo,
        roles: list[str],
        invited_by: uuid.UUID | None = None,
    ) -> None:
        async with tenant_session(self.engine, tenant.id) as session:
            session.add(
                Membership(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    roles=list(roles),
                    state="active",
                    invited_by=invited_by,
                )
            )
            try:
                await session.flush()
            except IntegrityError as exc:
                raise Conflict("already a member") from exc

    async def change(
        self,
        tenant: TenantInfo,
        user_id: uuid.UUID,
        roles: list[str] | None,
        *,
        may_change_owners: bool,
    ) -> list[str]:
        """New roles, or removal (``roles`` None). The tenant's memberships are locked first and
        every rule is checked on the locked rows, so concurrent changes cannot slip past them
        (ADR-0033 §5): without ``may_change_owners`` (an owner acting) neither an owner nor the
        owner role is touched, and an active owner must remain. Returns the roles before."""

        async with tenant_session(self.engine, tenant.id) as session:
            rows = list(
                await session.scalars(
                    select(Membership).where(Membership.tenant_id == tenant.id).with_for_update()
                )
            )
            target = next((m for m in rows if m.user_id == user_id), None)
            if target is None:
                raise NotFound("no such member")
            if not may_change_owners and (OWNER in target.roles or OWNER in (roles or [])):
                raise OwnerOnly(
                    "only an owner removes an owner"
                    if roles is None
                    else "only an owner grants or removes ownership"
                )

            def roles_after(m: Membership) -> list[str] | None:
                return m.roles if m.user_id != user_id else roles  # None: removed

            owners_after = sum(
                1 for m in rows if m.state == "active" and OWNER in (roles_after(m) or [])
            )
            if owners_after == 0:
                raise LastOwner("the organization would have no owner")
            before = list(target.roles)
            if roles is None:
                await session.delete(target)
            else:
                target.roles = list(roles)
            return before

    async def create_with_owner(
        self,
        slug: str,
        name: str,
        region: str,
        *,
        edition: str,
        owner: UserInfo,
        owned_limit: int,
        terms_version: str,
    ) -> TenantInfo:
        """The organization, its first owner, the terms they accepted and the per-person limit in
        one transaction (ADR-0033 §4): no organization exists without its owner, and one
        person's concurrent creations queue on their user row, so the limit holds."""

        async with user_session(self.engine, owner.id) as session:
            await session.execute(select(User.id).where(User.id == owner.id).with_for_update())
            row = Tenant(
                slug=slug,
                name=name,
                region=region,
                status="active",
                edition=edition,
                created_by=owner.id,
                terms_version=terms_version,
                terms_accepted_by=owner.id,
                terms_accepted_at=_now(),
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise Conflict(f"tenant {slug} already exists") from exc
            await enter_tenant(session, row.id)
            session.add(Membership(tenant_id=row.id, user_id=owner.id, roles=[OWNER]))
            await session.flush()
            owned = await session.execute(
                select(Membership.roles)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(Membership.user_id == owner.id, Tenant.edition == edition)
            )
            if sum(1 for (roles,) in owned if OWNER in roles) > owned_limit:
                raise OwnedLimit(f"you already own {owned_limit} {edition} organization(s)")
            return tenant_info(row)

    async def owned(self, user: UserInfo, edition: str) -> int:
        """How many organizations of ``edition`` the person owns (ADR-0033 §4 limit)."""

        async with user_session(self.engine, user.id) as session:
            rows = await session.execute(
                select(Membership.roles)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(Membership.user_id == user.id, Tenant.edition == edition)
            )
            return sum(1 for (roles,) in rows if OWNER in roles)


class InvitationRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(
        self,
        tenant: TenantInfo,
        email: str,
        roles: list[str],
        invited_by: uuid.UUID,
        ttl_days: int,
        pending_limit: int,
    ) -> tuple[InvitationInfo, str]:
        """Returns the record and the link secret, which is shown once and stored as a hash."""

        token = secrets.token_urlsafe(32)
        now = _now()
        async with tenant_session(self.engine, tenant.id) as session:
            pending = await session.scalar(
                select(func.count())
                .select_from(Invitation)
                .where(
                    Invitation.tenant_id == tenant.id,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                    Invitation.expires_at > now,
                )
            )
            if (pending or 0) >= pending_limit:
                raise InvitationRefused(
                    "invitation_limit_reached", f"at most {pending_limit} pending invitations"
                )
            row = Invitation(
                tenant_id=tenant.id,
                email=email,
                roles=list(roles),
                token_sha256=token_hash(token),
                invited_by=invited_by,
                created_at=now,
                expires_at=now + timedelta(days=ttl_days),
            )
            session.add(row)
            await session.flush()
            return _invitation(row, tenant.slug), token

    async def list(self, tenant: TenantInfo) -> list[InvitationInfo]:
        async with tenant_session(self.engine, tenant.id) as session:
            rows = await session.scalars(
                select(Invitation)
                .where(Invitation.tenant_id == tenant.id)
                .order_by(Invitation.created_at.desc())
            )
            return [_invitation(r, tenant.slug) for r in rows]

    async def revoke(self, tenant: TenantInfo, invitation_id: uuid.UUID) -> InvitationInfo:
        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(Invitation).where(
                    Invitation.tenant_id == tenant.id, Invitation.id == invitation_id
                )
            )
            if row is None:
                raise NotFound("no such invitation")
            if row.accepted_at is None and row.revoked_at is None:
                row.revoked_at = _now()
            return _invitation(row, tenant.slug)

    async def get(self, tenant: TenantInfo, invitation_id: uuid.UUID) -> InvitationInfo:
        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(Invitation).where(
                    Invitation.tenant_id == tenant.id, Invitation.id == invitation_id
                )
            )
            if row is None:
                raise NotFound("no such invitation")
            return _invitation(row, tenant.slug)

    async def accept(self, tenant: TenantInfo, token: str, user: UserInfo) -> tuple[str, ...]:
        """Membership for ``user`` from a pending invitation addressed to their verified e-mail,
        in one transaction (ADR-0033 §3.2). Returns the roles. An existing membership is kept
        and the invitation is consumed (already a member counts as success)."""

        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(Invitation)
                .where(
                    Invitation.tenant_id == tenant.id,
                    Invitation.token_sha256 == token_hash(token),
                )
                .with_for_update()
            )
            if row is None:
                raise InvitationRefused("not_found", "no such invitation")
            if row.accepted_at is not None:
                raise InvitationRefused("invitation_used", "the invitation was already accepted")
            if row.revoked_at is not None:
                raise InvitationRefused("invitation_revoked", "the invitation was revoked")
            if row.expires_at <= _now():
                raise InvitationRefused("invitation_expired", "the invitation has expired")
            if not user.email_verified:
                raise InvitationRefused("email_unverified", "verify your e-mail address first")
            if user.email != row.email:
                raise InvitationRefused(
                    "invitation_email_mismatch", "the invitation is for another e-mail address"
                )
            existing = await session.scalar(
                select(Membership).where(
                    Membership.tenant_id == tenant.id, Membership.user_id == user.id
                )
            )
            if existing is None:
                session.add(
                    Membership(
                        tenant_id=tenant.id,
                        user_id=user.id,
                        roles=list(row.roles),
                        state="active",
                        invited_by=row.invited_by,
                    )
                )
            row.accepted_at, row.accepted_by = _now(), user.id
            return tuple(existing.roles if existing else row.roles)
