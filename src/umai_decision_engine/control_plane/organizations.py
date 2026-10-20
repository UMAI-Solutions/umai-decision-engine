"""Organizations, memberships and invitations in the policy service (ADR-0033, contract
organizations 0.2). The tenant store is the source of truth; until the identity-service mirror
exists (ADR-0033 amendment of 2026-10-19), invitations are our own single-use links.

Every method gets an already authorized actor (``authz``); the rules here are the ones the
permission matrix cannot express: verified e-mail, the last owner, who may grant ownership,
the Community limits, the invitation's addressee."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from umai_decision_engine.contracts.organizations import (
    Invitation,
    InvitationCreate,
    Membership,
    MyOrganization,
    Organization,
    OrganizationCreate,
    Role,
    UserRef,
    normalize_email,
)
from umai_decision_engine.control_plane.authz import Actor, assignable_roles
from umai_decision_engine.db.organizations import (
    InvitationInfo,
    InvitationRefused,
    InvitationRepository,
    LastOwner,
    MemberInfo,
    MembershipRepository,
    OwnedLimit,
    OwnerOnly,
    UserInfo,
    UserRepository,
)
from umai_decision_engine.db.repositories import Conflict, NotFound, TenantInfo, TenantRepository

log = logging.getLogger("umai.control_plane.organizations")

COMMUNITY = "community"
RESERVED_IDS = frozenset(
    {"admin", "api", "app", "auth", "console", "default", "help", "login", "root", "support",
     "system", "tenants", "umai", "www"}
)  # fmt: skip
# Disposable e-mail providers refused at organization creation (ADR-0033 §4). Data, extended
# by the setting UMAI_DE_DISPOSABLE_EMAIL_DOMAINS; a starting list, not a complete one.
DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {"10minutemail.com", "dispostable.com", "getnada.com", "guerrillamail.com",
     "maildrop.cc", "mailinator.com", "mintemail.com", "sharklasers.com", "temp-mail.org",
     "tempmail.com", "throwawaymail.com", "trashmail.com", "yopmail.com"}
)  # fmt: skip


class OrganizationError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def organization_view(tenant: TenantInfo) -> dict[str, Any]:
    return Organization(
        id=tenant.slug,
        name=tenant.name,
        region=tenant.region,  # type: ignore[arg-type]
        edition=tenant.edition,  # type: ignore[arg-type]
        status=tenant.status,  # type: ignore[arg-type]
        created_at=tenant.created_at,
    ).model_dump(mode="json")


def member_view(member: MemberInfo) -> dict[str, Any]:
    return Membership(
        tenant_id=member.tenant_slug,
        user=UserRef(id=member.user.id, email=member.user.email, name=member.user.name),
        roles=[Role(r) for r in member.roles],
        state=member.state,  # type: ignore[arg-type]
        invited_by=member.invited_by,
        joined_at=member.joined_at,
    ).model_dump(mode="json")


def invitation_view(invitation: InvitationInfo) -> dict[str, Any]:
    return Invitation(
        id=invitation.id,
        tenant_id=invitation.tenant_slug,
        email=invitation.email,
        roles=[Role(r) for r in invitation.roles],
        state=invitation.state,  # type: ignore[arg-type]
        invited_by=invitation.invited_by,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        revoked_at=invitation.revoked_at,
    ).model_dump(mode="json")


class OrganizationService:
    def __init__(
        self,
        tenants: TenantRepository,
        users: UserRepository,
        memberships: MembershipRepository,
        invitations: InvitationRepository,
        *,
        region: str,
        default_tenant: str,
        max_owned_community: int,
        max_pending_invitations: int,
        disposable_domains: frozenset[str] = DISPOSABLE_EMAIL_DOMAINS,
        starter: Any = None,  # callable(tenant, actor_record) -> int | None
    ) -> None:
        self.tenants = tenants
        self.users = users
        self.memberships = memberships
        self.invitations = invitations
        self.region = region
        self.reserved = RESERVED_IDS | {default_tenant}
        self.max_owned_community = max_owned_community
        self.max_pending_invitations = max_pending_invitations
        self.disposable_domains = disposable_domains
        self.starter = starter

    # ----------------------------------------------------------- people

    async def person(self, actor: Actor) -> UserInfo:
        """The acting person in our store, refreshed from the assertion's claims."""

        if actor.kind != "person" or not actor.sub:
            raise OrganizationError(403, "actor_required", "this is done by a person")
        if not actor.email:
            raise OrganizationError(422, "email_required", "the assertion carries no e-mail")
        try:
            # Stored only as the contract can show it again (a member list never fails on it).
            email = normalize_email(actor.email)
        except ValueError:
            raise OrganizationError(
                422, "email_invalid", "the assertion's e-mail is not an e-mail address"
            ) from None
        return await self.users.upsert(actor.sub, email, actor.email_verified, actor.name)

    async def roles_of(self, actor: Actor) -> frozenset[Role]:
        """The person's roles in the assertion's tenant, from the store (ADR-0033 §1)."""

        if actor.tenant_id is None or not actor.sub:
            return frozenset()
        tenant = await self.tenants.get(actor.tenant_id)
        user = await self.users.get(actor.sub)
        if tenant is None or user is None or tenant.status != "active":
            return frozenset()
        known = {r.value for r in Role}
        return frozenset(Role(r) for r in await self.memberships.roles(tenant, user) if r in known)

    async def mine(self, actor: Actor) -> list[dict[str, Any]]:
        user = await self.person(actor)
        return [
            MyOrganization(
                id=m.slug,
                name=m.name,
                region=m.region,  # type: ignore[arg-type]
                status=m.status,  # type: ignore[arg-type]
                roles=[Role(r) for r in m.roles],
            ).model_dump(mode="json")
            for m in await self.memberships.mine(user)
        ]

    # ----------------------------------------------------------- organizations

    async def create(self, actor: Actor, body: OrganizationCreate) -> dict[str, Any]:
        user = await self.person(actor)
        if not user.email_verified:
            raise OrganizationError(403, "email_unverified", "verify your e-mail address first")
        domain = user.email.rsplit("@", 1)[-1]
        if domain in self.disposable_domains:
            raise OrganizationError(
                422, "disposable_email_domain", "use an address that is not a disposable one"
            )
        if body.region != self.region:
            raise OrganizationError(
                422, "region_unavailable", f"this console creates organizations in {self.region}"
            )
        if body.id in self.reserved:
            raise OrganizationError(409, "id_taken", f"{body.id} is reserved")
        try:
            # Tenant, owner membership, accepted terms and the Community limit: one transaction.
            tenant = await self.memberships.create_with_owner(
                body.id,
                body.name,
                self.region,
                edition=COMMUNITY,
                owner=user,
                owned_limit=self.max_owned_community,
                terms_version=body.terms_version,
            )
        except Conflict as exc:
            raise OrganizationError(409, "id_taken", f"{body.id} is taken") from exc
        except OwnedLimit:
            raise OrganizationError(
                403,
                "community_limit_reached",
                f"you already own {self.max_owned_community} Community organization(s)",
            ) from None
        log.info(
            "organization.created tenant=%s owner=%s terms=%s",
            tenant.slug,
            user.id,
            body.terms_version,
        )
        starter = None
        if self.starter is not None:
            try:
                # File writes and signing: off the event loop.
                starter = await asyncio.to_thread(
                    self.starter, tenant, {"type": "person", "sub": user.subject}
                )
            except Exception:  # the organization exists; the starter guardrail is reported missing
                log.exception("organization.starter_failed tenant=%s", tenant.slug)
        return {"organization": organization_view(tenant), "starter_guardrail": starter}

    async def get(self, tenant: TenantInfo) -> dict[str, Any]:
        return organization_view(tenant)

    async def rename(self, tenant: TenantInfo, name: str) -> dict[str, Any]:
        return organization_view(await self.tenants.rename(tenant, name))

    # ----------------------------------------------------------- members

    async def members(self, tenant: TenantInfo) -> list[dict[str, Any]]:
        return [member_view(m) for m in await self.memberships.list(tenant)]

    def _check_assignable(self, roles: list[Role]) -> None:
        allowed = assignable_roles(rbac=False)  # entitlements: ADR-0019 (Community roles, A14)
        refused = sorted(r.value for r in roles if r not in allowed)
        if refused:
            raise OrganizationError(
                422, "role_unavailable", f"roles not available here: {', '.join(refused)}"
            )

    async def set_roles(
        self, tenant: TenantInfo, actor: Actor, user_id: uuid.UUID, roles: list[Role]
    ) -> dict[str, Any]:
        self._check_assignable(roles)
        await self._change(tenant, actor, user_id, [r.value for r in roles])
        return member_view(
            next(m for m in await self.memberships.list(tenant) if m.user.id == user_id)
        )

    async def remove(self, tenant: TenantInfo, actor: Actor, user_id: uuid.UUID) -> None:
        await self._change(tenant, actor, user_id, None)

    async def _change(
        self, tenant: TenantInfo, actor: Actor, user_id: uuid.UUID, roles: list[str] | None
    ) -> None:
        """Roles or removal; the ownership rules are checked on the locked memberships, so a
        concurrent grant cannot slip past them."""

        try:
            await self.memberships.change(
                tenant, user_id, roles, may_change_owners=Role.OWNER in actor.roles
            )
        except NotFound as exc:
            raise OrganizationError(404, "not_found", "no such member") from exc
        except OwnerOnly as exc:
            raise OrganizationError(403, "forbidden", str(exc)) from exc
        except LastOwner as exc:
            raise OrganizationError(409, "last_owner", str(exc)) from exc

    async def leave(self, tenant: TenantInfo, actor: Actor) -> None:
        user = await self.person(actor)
        try:
            # Leaving touches only the person's own membership, ownership included.
            await self.memberships.change(tenant, user.id, None, may_change_owners=True)
        except NotFound as exc:
            raise OrganizationError(404, "not_found", "you are not a member") from exc
        except LastOwner as exc:
            raise OrganizationError(409, "last_owner", str(exc)) from exc

    # ----------------------------------------------------------- invitations

    async def invite(
        self, tenant: TenantInfo, actor: Actor, body: InvitationCreate
    ) -> dict[str, Any]:
        self._check_assignable(body.roles)
        if Role.OWNER in body.roles and Role.OWNER not in actor.roles:
            raise OrganizationError(403, "forbidden", "only an owner invites an owner")
        inviter = await self.person(actor)
        try:
            invitation, token = await self.invitations.create(
                tenant,
                body.email,
                [r.value for r in body.roles],
                inviter.id,
                body.ttl_days,
                self.max_pending_invitations,
            )
        except InvitationRefused as exc:
            raise OrganizationError(409, exc.code, str(exc)) from exc
        log.info("invitation.created tenant=%s invitation=%s", tenant.slug, invitation.id)
        return {"invitation": invitation_view(invitation), "token": token}

    async def list_invitations(self, tenant: TenantInfo) -> list[dict[str, Any]]:
        return [invitation_view(i) for i in await self.invitations.list(tenant)]

    async def revoke(self, tenant: TenantInfo, invitation_id: uuid.UUID) -> dict[str, Any]:
        try:
            return invitation_view(await self.invitations.revoke(tenant, invitation_id))
        except NotFound as exc:
            raise OrganizationError(404, "not_found", str(exc)) from exc

    async def resend(
        self, tenant: TenantInfo, actor: Actor, invitation_id: uuid.UUID
    ) -> dict[str, Any]:
        """A new link for the same address and roles; the old link stops working."""

        try:
            old = await self.invitations.get(tenant, invitation_id)
        except NotFound as exc:
            raise OrganizationError(404, "not_found", str(exc)) from exc
        if old.state == "accepted":
            raise OrganizationError(409, "invitation_used", "the invitation was already accepted")
        await self.invitations.revoke(tenant, invitation_id)
        return await self.invite(
            tenant, actor, InvitationCreate(email=old.email, roles=[Role(r) for r in old.roles])
        )

    async def accept(self, actor: Actor, organization: str, token: str) -> dict[str, Any]:
        user = await self.person(actor)
        tenant = await self.tenants.get(organization)
        if tenant is None or tenant.status != "active":
            raise OrganizationError(404, "not_found", "no such invitation")
        try:
            roles = await self.invitations.accept(tenant, token, user)
        except InvitationRefused as exc:
            status = {
                "not_found": 404,
                "invitation_used": 409,
                "invitation_revoked": 410,
                "invitation_expired": 410,
            }.get(exc.code, 403)
            raise OrganizationError(status, exc.code, str(exc)) from exc
        log.info("invitation.accepted tenant=%s user=%s", tenant.slug, user.id)
        return MyOrganization(
            id=tenant.slug,
            name=tenant.name,
            region=tenant.region,  # type: ignore[arg-type]
            status=tenant.status,  # type: ignore[arg-type]
            roles=[Role(r) for r in roles],
        ).model_dump(mode="json")
