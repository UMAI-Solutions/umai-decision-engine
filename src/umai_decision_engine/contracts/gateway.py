"""Gateway API (docs/contracts/gateway-api.md): routes and the gateway's error body.

Provider request and response bodies are not modelled here: the gateway passes them through as
JSON and touches only the fields the contract names (ADR-0034 §2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from umai_decision_engine.contracts.common import Action, ContractModel, DestinationClass, Phase

CONTRACT_VERSION = "0.2"

ROUTE_ID = r"^[a-z0-9][a-z0-9._\-]{0,63}$"
TENANT_FILE = r"^[a-z0-9][a-z0-9\-]{0,63}$"

UpstreamKind = Literal["openai", "azure_openai", "openai_compatible"]
# pass_through: the caller sends its provider key (X-Umai-Provider-Key) per call.
# secret: a provider key the deployment holds in settings (UMAI_DE_GATEWAY_PROVIDER_KEYS).
# none: no credential upstream (self-hosted model servers).
CredentialMode = Literal["pass_through", "secret", "none"]
StreamMode = Literal["buffer", "window", "after"]
RefusalStyle = Literal["error", "message"]
UnscannedParts = Literal["reject", "pass_unscanned"]

DEFAULT_REFUSAL_TEXT = "This request was blocked by your organization's AI policy."


class Upstream(ContractModel):
    kind: UpstreamKind
    base_url: str = Field(min_length=8, max_length=512)
    model: str = Field(min_length=1, max_length=256)
    region: str | None = Field(None, max_length=32)


class RouteTimeouts(ContractModel):
    connect: int = Field(5_000, ge=100, le=60_000)
    first_byte: int = Field(120_000, ge=1_000, le=600_000)
    total: int = Field(600_000, ge=1_000, le=3_600_000)


class Route(ContractModel):
    route_id: str = Field(pattern=ROUTE_ID)
    enabled: bool = True
    upstream: Upstream
    credential: CredentialMode = "pass_through"
    secret: str | None = Field(None, min_length=1, max_length=64)
    guardrail: str = Field(min_length=1, max_length=128)
    destination_class: DestinationClass = DestinationClass.EXTERNAL_LLM
    stream_mode: StreamMode = "window"
    refusal: RefusalStyle = "error"
    refusal_text: str = Field(DEFAULT_REFUSAL_TEXT, min_length=1, max_length=500)
    unscanned_parts: UnscannedParts = "reject"
    restore_placeholders: bool = True
    timeouts_ms: RouteTimeouts = Field(default_factory=RouteTimeouts)

    @model_validator(mode="after")
    def _secret_matches_credential(self) -> Route:
        if self.credential == "secret" and not self.secret:
            raise ValueError("credential 'secret' needs the name of a provider key in 'secret'")
        if self.credential != "secret" and self.secret is not None:
            raise ValueError("'secret' is only used with credential 'secret'")
        return self


class RouteFile(ContractModel):
    """One tenant's routes: ``<gateway_routes_dir>/<tenant_id>.yaml``."""

    routes: list[Route] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> RouteFile:
        ids = [r.route_id for r in self.routes]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate route ids: {', '.join(duplicates)}")
        return self


class UmaiDecisionInfo(ContractModel):
    """Why the gateway refused: ids, types and actions only, never content."""

    request_id: str
    event_id: str | None = None
    phase: Phase
    action: Action
    entities: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)


class GatewayError(ContractModel):
    message: str
    type: Literal["umai_guardrail", "umai_gateway"]
    param: str | None = None
    code: str
    umai: UmaiDecisionInfo | None = None


class GatewayErrorBody(ContractModel):
    """OpenAI-shaped error body for refusals and gateway errors."""

    error: GatewayError
