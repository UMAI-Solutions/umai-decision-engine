"""Console sessions contract (docs/contracts/console-sessions.md, ADR-0024 §2 amendment): one
record per console sign-in, which the policy service checks on every call made for the person
and which the person can list and end.

The session id itself is a secret of the console's cookie: it appears in requests (the
assertion's ``session_id``, :class:`SessionCheck`) and never in a response."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "0.1"

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{32,128}$"  # 256 random bits, base64url, from the console
# "<browser> on <system>", made by the console from the User-Agent; never the user agent itself.
DEVICE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 .()/-]{0,63}$"

SessionState = Literal["active", "ended", "unknown"]
RevokeReason = Literal["signed_out", "revoked", "signed_out_elsewhere", "limit"]


class ConsoleSession(BaseModel):
    """A live session as the person sees it (``GET /v1/me/sessions``)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID  # our record id, never the session id
    device: str | None = Field(None, pattern=DEVICE_PATTERN)
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool = False


class SessionStart(BaseModel):
    """``POST /v1/me/sessions``: the session id is the assertion's ``session_id``."""

    model_config = ConfigDict(extra="forbid")

    device: str | None = Field(None, pattern=DEVICE_PATTERN)


class SessionCheck(BaseModel):
    """``POST /v1/sessions/check``, by the console middleware with its service credential."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    subject: str = Field(min_length=1, max_length=255)
    tenant_id: str | None = Field(None, min_length=1, max_length=128)  # the active organization


class SessionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: SessionState
    # With tenant_id and an active session: still an active member of an active organization?
    member: bool | None = None
