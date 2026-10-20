"""Gateway API (docs/contracts/gateway-api.md): gateways, the per-call target, the error body.

Provider request and response bodies are not modelled here: the gateway passes them through as
JSON and touches only the fields the contract names (ADR-0034 §2).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from umai_decision_engine.contracts.common import Action, ContractModel, DestinationClass, Phase

CONTRACT_VERSION = "0.3"  # gateway file (0.3: gateways, gateway-api 0.9)

ROUTE_ID = r"^[a-z0-9][a-z0-9._\-]{0,63}$"
TENANT_FILE = r"^[a-z0-9][a-z0-9\-]{0,63}$"
GATEWAY_ID = r"^[a-z0-9][a-z0-9\-]{0,63}$"
# What a caller may send as `model`: logged, so no free text (ADR-0038 §2).
MODEL_NAME = r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}$"
OPENAI_BASE_URL = "https://api.openai.com/v1"

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
    """The target of one call: a gateway resolved with the requested model (ADR-0038 §6).
    Built per call, never stored; ``route_id`` is the gateway's id."""

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


class Provider(ContractModel):
    """A gateway's provider connection."""

    kind: UpstreamKind = "openai"
    base_url: str | None = Field(None, min_length=8, max_length=512)
    region: str | None = Field(None, max_length=32)
    credential: CredentialMode = "pass_through"
    secret: str | None = Field(None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _url_and_secret(self) -> Provider:
        if self.base_url is None:
            if self.kind != "openai":
                raise ValueError(f"provider kind {self.kind} needs a base_url")
            self.base_url = OPENAI_BASE_URL
        if self.credential == "secret" and not self.secret:
            raise ValueError("credential 'secret' needs the name of a provider key in 'secret'")
        if self.credential != "secret" and self.secret is not None:
            raise ValueError("'secret' is only used with credential 'secret'")
        return self


class ModelAlias(ContractModel):
    name: str = Field(pattern=MODEL_NAME)  # what callers send
    model: str = Field(min_length=1, max_length=256)  # sent upstream (e.g. an Azure deployment)


class Gateway(ContractModel):
    """One provider connection, one guardrail and the call settings (ADR-0038)."""

    gateway_id: str = Field(pattern=GATEWAY_ID)
    name: str | None = Field(None, min_length=1, max_length=120)
    enabled: bool = True
    provider: Provider = Field(default_factory=Provider)
    guardrail: str = Field(min_length=1, max_length=128)
    models: list[str] = Field(default_factory=list, max_length=500)
    aliases: list[ModelAlias] = Field(default_factory=list, max_length=500)
    destination_class: DestinationClass = DestinationClass.EXTERNAL_LLM
    stream_mode: StreamMode = "window"
    refusal: RefusalStyle = "error"
    refusal_text: str = Field(DEFAULT_REFUSAL_TEXT, min_length=1, max_length=500)
    unscanned_parts: UnscannedParts = "reject"
    restore_placeholders: bool = True
    timeouts_ms: RouteTimeouts = Field(default_factory=RouteTimeouts)
    # Another gateway of the tenant tried once when this provider is unreachable, times out or
    # answers 429 / 5xx; same guardrail, credential secret or none (gateway-api 0.6, 0.9).
    fallback: str | None = Field(None, pattern=GATEWAY_ID)

    @model_validator(mode="after")
    def _names(self) -> Gateway:
        pattern = re.compile(MODEL_NAME)
        bad = [m for m in self.models if not pattern.match(m)]
        if bad:
            raise ValueError(f"models: {bad[0]!r} is not a model name")
        names = [a.name for a in self.aliases]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate alias names: {', '.join(duplicates)}")
        return self

    def accepts(self, model: str) -> bool:
        return not self.models or model in self.models or any(a.name == model for a in self.aliases)

    def listed_models(self) -> list[str]:
        """Names for ``GET /v1/models``; empty when the provider's own list applies."""

        names = list(self.models)
        names += [a.name for a in self.aliases if a.name not in names]
        return names

    def resolve(self, model: str) -> Route:
        """The call target for ``model``; the caller checks :meth:`accepts` first."""

        upstream_model = next((a.model for a in self.aliases if a.name == model), model)
        provider = self.provider
        return Route(
            route_id=self.gateway_id,
            enabled=self.enabled,
            upstream=Upstream(
                kind=provider.kind,
                base_url=provider.base_url or OPENAI_BASE_URL,
                model=upstream_model,
                region=provider.region,
            ),
            credential=provider.credential,
            secret=provider.secret,
            guardrail=self.guardrail,
            destination_class=self.destination_class,
            stream_mode=self.stream_mode,
            refusal=self.refusal,
            refusal_text=self.refusal_text,
            unscanned_parts=self.unscanned_parts,
            restore_placeholders=self.restore_placeholders,
            timeouts_ms=self.timeouts_ms,
        )


class GatewayFile(ContractModel):
    """One tenant's gateways: ``<gateways_dir>/<tenant_id>.yaml``."""

    gateways: list[Gateway] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids_and_fallbacks(self) -> GatewayFile:
        ids = [g.gateway_id for g in self.gateways]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate gateway ids: {', '.join(duplicates)}")
        by_id = {g.gateway_id: g for g in self.gateways}
        for gateway in self.gateways:
            if gateway.fallback is None:
                continue
            target = by_id.get(gateway.fallback)
            if target is None or target.gateway_id == gateway.gateway_id:
                raise ValueError(
                    f"gateway {gateway.gateway_id}: fallback is not another gateway of this file"
                )
            if target.guardrail != gateway.guardrail:
                raise ValueError(
                    f"gateway {gateway.gateway_id}: fallback must use the same guardrail"
                )
            if target.provider.credential == "pass_through":
                raise ValueError(
                    f"gateway {gateway.gateway_id}: a fallback cannot pass the caller's provider "
                    "key through (it belongs to the first provider); use credential secret or none"
                )
        return self

    def get(self, gateway_id: str) -> Gateway | None:
        return next((g for g in self.gateways if g.gateway_id == gateway_id), None)


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
