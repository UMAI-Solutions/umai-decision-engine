"""Organizations contract rules (docs/contracts/organizations.md, ADR-0033)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from umai_decision_engine.contracts.organizations import (
    Domain,
    DomainCreate,
    InvitationCreate,
    MembershipUpdate,
    OrganizationCreate,
    Role,
    normalize_email,
)

_DOMAIN = {
    "id": "4d3c2b1a-0f9e-4d8c-b7a6-958473625140",
    "tenant_id": "acme-security",
    "domain": "acme.example",
    "status": "verified",
    "verification": {"record_name": "_umai-verification.acme.example", "record_value": "x"},
}


@pytest.mark.parametrize("value", ["acme", "acme-security", "a1b", "x" * 40])
def test_organization_id_accepts(value: str) -> None:
    OrganizationCreate(id=value, name="Acme", region="eu", accept_terms=True, terms_version="1")


@pytest.mark.parametrize("value", ["ab", "-acme", "acme-", "Acme", "acme_sec", "x" * 41, "açme"])
def test_organization_id_rejects(value: str) -> None:
    with pytest.raises(ValidationError):
        OrganizationCreate(id=value, name="Acme", region="eu", accept_terms=True, terms_version="1")


def test_terms_must_be_accepted() -> None:
    with pytest.raises(ValidationError):
        OrganizationCreate(
            id="acme", name="Acme", region="eu", accept_terms=False, terms_version="1"
        )


def test_email_is_normalised_but_plus_addresses_kept() -> None:
    assert normalize_email("  Grace+Ops@Example.COM ") == "grace+ops@example.com"
    invitation = InvitationCreate(email="Grace@Example.com", roles=["viewer"])
    assert invitation.email == "grace@example.com" and invitation.ttl_days == 7


@pytest.mark.parametrize("bad", ["", "grace", "grace@", "@example.com", "a b@example.com"])
def test_email_rejects(bad: str) -> None:
    with pytest.raises(ValidationError):
        InvitationCreate(email=bad, roles=["viewer"])


@pytest.mark.parametrize("ttl", [0, 31])
def test_invitation_lifetime_is_1_to_30_days(ttl: int) -> None:
    with pytest.raises(ValidationError):
        InvitationCreate(email="g@example.com", roles=["viewer"], ttl_days=ttl)


def test_roles_must_be_non_empty_and_unique() -> None:
    with pytest.raises(ValidationError):
        MembershipUpdate(roles=[])
    with pytest.raises(ValidationError):
        MembershipUpdate(roles=["admin", "admin"])
    assert MembershipUpdate(roles=["admin", "billing"]).roles == [Role.ADMIN, Role.BILLING]


@pytest.mark.parametrize("role", [None, "owner", "admin"])
def test_auto_join_never_grants_owner_or_admin(role: str | None) -> None:
    with pytest.raises(ValidationError):
        Domain.model_validate({**_DOMAIN, "mode": "auto", "default_role": role})


def test_auto_join_with_a_lower_role() -> None:
    domain = Domain.model_validate({**_DOMAIN, "mode": "auto", "default_role": "viewer"})
    assert domain.default_role is Role.VIEWER


def test_domain_create_lowercases_and_validates() -> None:
    assert DomainCreate(domain=" Acme.Example ").domain == "acme.example"
    with pytest.raises(ValidationError):
        DomainCreate(domain="not a domain")
