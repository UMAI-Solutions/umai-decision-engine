"""Organizations contract (docs/contracts/organizations.md, ADR-0033): organizations,
memberships, invitations and verified domains between the policy service and the console.

Account metadata (e-mail, display name) only; no evaluation content ever appears here."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "0.2"

# Organization id = tenant slug = the identity organization's name (Auth0 allows 1-50 of
# a-z 0-9 _ -); ours is stricter: 3-40, letters, digits and inner hyphens.
ORGANIZATION_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class Role(StrEnum):
    """Tenant roles (ADR-0029 §2). A membership holds one or more."""

    OWNER = "owner"
    ADMIN = "admin"
    POLICY_EDITOR = "policy_editor"
    APPROVER = "approver"
    VIEWER = "viewer"
    BILLING = "billing"


Region = Literal["eu", "us", "sh"]
Edition = Literal["community", "enterprise"]
OrganizationStatus = Literal["provisioning", "active", "suspended", "deleting"]


def check_domain(value: str) -> str:
    """A lower-case DNS name with at least one dot (look-ahead needs Python's ``re``)."""

    if not _DOMAIN.match(value):
        raise ValueError("not a domain name")
    return value


def normalize_email(value: str) -> str:
    """Trimmed and lower-cased; plus-addresses are kept as they are (ADR-0033 §3.2)."""

    email = value.strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise ValueError("not an e-mail address")
    return email


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Roles(_Model):
    roles: list[Role] = Field(min_length=1)

    @field_validator("roles")
    @classmethod
    def _unique(cls, roles: list[Role]) -> list[Role]:
        if len(set(roles)) != len(roles):
            raise ValueError("roles must not repeat")
        return roles


class Organization(_Model):
    id: str = Field(pattern=ORGANIZATION_ID_PATTERN)
    name: str = Field(min_length=2, max_length=100)
    region: Region
    edition: Edition
    status: OrganizationStatus
    created_at: datetime


class MyOrganization(_Roles):
    id: str = Field(pattern=ORGANIZATION_ID_PATTERN)
    name: str = Field(min_length=2, max_length=100)
    region: Region
    status: OrganizationStatus


class UserRef(_Model):
    id: UUID
    email: str
    name: str | None = Field(None, max_length=200)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class Membership(_Roles):
    tenant_id: str
    user: UserRef
    projects: list[str] = Field(default_factory=list)  # empty = all projects
    state: Literal["active", "suspended"]
    invited_by: UUID | None = None
    joined_at: datetime


class Invitation(_Roles):
    id: UUID
    tenant_id: str
    email: str
    state: Literal["pending", "accepted", "revoked", "expired"]
    invited_by: UUID
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class DomainVerification(_Model):
    record_name: str
    record_value: str


class Domain(_Model):
    id: UUID
    tenant_id: str
    domain: str
    status: Literal["pending", "verified", "lapsed"]
    mode: Literal["off", "request", "auto"]
    default_role: Role | None = None
    verification: DomainVerification
    verified_at: datetime | None = None

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        return check_domain(value)

    @model_validator(mode="after")
    def _auto_needs_lower_role(self) -> Domain:
        if self.mode == "auto" and self.default_role in (None, Role.OWNER, Role.ADMIN):
            raise ValueError("auto join needs a default role other than owner or admin")
        return self


# ----------------------------------------------------------------------- requests


class OrganizationCreate(_Model):
    id: str = Field(pattern=ORGANIZATION_ID_PATTERN)
    name: str = Field(min_length=2, max_length=100)
    region: Region
    accept_terms: Literal[True]
    terms_version: str = Field(min_length=1, max_length=40)


class InvitationCreate(_Roles):
    email: str
    ttl_days: int = Field(7, ge=1, le=30)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class MembershipUpdate(_Roles):
    pass


class DomainCreate(_Model):
    domain: str

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        return check_domain(value.strip().lower())


class DomainUpdate(_Model):
    mode: Literal["off", "request", "auto"]
    default_role: Role | None = None


class InvitationAccept(_Model):
    """From the invitation link (0.2): our organization id and the link's single-use secret."""

    organization: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=20, max_length=200)


class OrganizationUpdate(_Model):
    name: str = Field(min_length=2, max_length=100)


ErrorCode = Literal[
    "actor_required",
    "email_required",
    "region_unavailable",
    "role_unavailable",
    "invitation_limit_reached",
    "forbidden",
    "email_unverified",
    "community_limit_reached",
    "invitation_email_mismatch",
    "not_found",
    "id_taken",
    "last_owner",
    "invitation_used",
    "domain_taken",
    "invitation_expired",
    "invitation_revoked",
    "disposable_email_domain",
    "personal_domain",
]
