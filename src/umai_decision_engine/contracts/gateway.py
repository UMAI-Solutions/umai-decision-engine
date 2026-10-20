"""Gateway API (docs/contracts/gateway-api.md): applications, providers, the per-call target,
the error body.

Provider request and response bodies are not modelled here: the gateway passes them through as
JSON and touches only the fields the contract names (ADR-0034 §2).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from umai_decision_engine.contracts.common import Action, ContractModel, DestinationClass, Phase

CONTRACT_VERSION = "0.4"  # applications file (0.4: applications and providers, gateway-api 0.10)

ROUTE_ID = r"^[a-z0-9][a-z0-9._\-]{0,63}$"
TENANT_FILE = r"^[a-z0-9][a-z0-9\-]{0,63}$"
APPLICATION_ID = r"^[a-z0-9][a-z0-9\-]{0,63}$"
PROVIDER_ID = r"^[a-z0-9][a-z0-9\-]{0,63}$"
# What a caller may send as `model`: an optional @provider/ prefix, then a model name. Logged,
# so no free text (ADR-0038 §2, ADR-0039 §3).
MODEL_REF = r"^(@[a-z0-9][a-z0-9\-]{0,63}/)?[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}$"
DEFAULT_PROVIDER_ID = "openai"
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
    """The target of one call: an application resolved with the requested model (ADR-0039).
    Built per call, never stored; ``route_id`` is the application's id."""

    route_id: str = Field(pattern=ROUTE_ID)
    enabled: bool = True
    upstream: Upstream
    provider_id: str | None = Field(None, pattern=PROVIDER_ID)
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


class GatewayDefaults(ContractModel):
    """The AI gateway's call settings; an application may override each (gateway-api 0.10)."""

    stream_mode: StreamMode = "window"
    refusal: RefusalStyle = "error"
    refusal_text: str = Field(DEFAULT_REFUSAL_TEXT, min_length=1, max_length=500)
    unscanned_parts: UnscannedParts = "reject"
    restore_placeholders: bool = True
    timeouts_ms: RouteTimeouts = Field(default_factory=RouteTimeouts)


class Provider(ContractModel):
    """Where the AI gateway sends model calls (ADR-0039 §3)."""

    provider_id: str = Field(pattern=PROVIDER_ID)
    name: str | None = Field(None, min_length=1, max_length=120)
    enabled: bool = True
    kind: UpstreamKind = "openai"
    base_url: str | None = Field(None, min_length=8, max_length=512)
    region: str | None = Field(None, max_length=32)
    credential: CredentialMode = "pass_through"
    secret: str | None = Field(None, min_length=1, max_length=64)
    default: bool = False

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


# Present in every organization unless a provider with this id is defined (Q56): applications
# that send their own OpenAI key need no setup.
BUILTIN_OPENAI = Provider(provider_id=DEFAULT_PROVIDER_ID, name="OpenAI", kind="openai")


class ModelAlias(ContractModel):
    name: str = Field(pattern=MODEL_REF)  # what the application sends
    model: str = Field(
        pattern=MODEL_REF
    )  # the target: a model of the default provider, or @provider/model


class Application(ContractModel):
    """The protected unit: one guardrail on every path, keys, gateway options (ADR-0039)."""

    application_id: str = Field(pattern=APPLICATION_ID)
    name: str | None = Field(None, min_length=1, max_length=120)
    enabled: bool = True
    guardrail: str = Field(min_length=1, max_length=128)
    # Gateway options. Empty lists mean "any".
    providers: list[str] = Field(default_factory=list, max_length=100)
    models: list[str] = Field(default_factory=list, max_length=500)
    aliases: list[ModelAlias] = Field(default_factory=list, max_length=500)
    # Tried once when the first provider is unreachable, times out or answers 429 / 5xx; a
    # provider with credential secret or none (the caller's key belongs to the first provider).
    fallback: str | None = Field(None, pattern=MODEL_REF)
    destination_class: DestinationClass = DestinationClass.EXTERNAL_LLM
    # Overrides of the gateway defaults; None means "the gateway's".
    stream_mode: StreamMode | None = None
    refusal: RefusalStyle | None = None
    refusal_text: str | None = Field(None, min_length=1, max_length=500)
    unscanned_parts: UnscannedParts | None = None
    restore_placeholders: bool | None = None
    timeouts_ms: RouteTimeouts | None = None

    @model_validator(mode="after")
    def _names(self) -> Application:
        pattern = re.compile(MODEL_REF)
        bad = [m for m in self.models if not pattern.match(m)]
        if bad:
            raise ValueError(f"models: {bad[0]!r} is not a model name")
        names = [a.name for a in self.aliases]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate alias names: {', '.join(duplicates)}")
        bad_ids = [p for p in self.providers if not re.match(PROVIDER_ID, p)]
        if bad_ids:
            raise ValueError(f"providers: {bad_ids[0]!r} is not a provider id")
        return self

    def accepts(self, model: str) -> bool:
        return not self.models or model in self.models or any(a.name == model for a in self.aliases)

    def listed_models(self) -> list[str]:
        """Names for ``GET /v1/models``; empty when the provider's own list applies."""

        names = list(self.models)
        names += [a.name for a in self.aliases if a.name not in names]
        return names


class ResolveError(ValueError):
    """A model reference this application cannot use; mapped to a gateway error."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def split_model_ref(ref: str) -> tuple[str | None, str]:
    """``@provider/model`` → (provider, model); a bare name → (None, name)."""

    if ref.startswith("@") and "/" in ref:
        provider, _, model = ref[1:].partition("/")
        return provider, model
    return None, ref


class ApplicationFile(ContractModel):
    """One tenant's applications, the AI gateway's providers and defaults:
    ``<applications_dir>/<tenant_id>.yaml`` (gateway-api 0.10)."""

    gateway: GatewayDefaults = Field(default_factory=GatewayDefaults)
    providers: list[Provider] = Field(default_factory=list)
    applications: list[Application] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ApplicationFile:
        for kind, ids in (
            ("provider", [p.provider_id for p in self.providers]),
            ("application", [a.application_id for a in self.applications]),
        ):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            if duplicates:
                raise ValueError(f"duplicate {kind} ids: {', '.join(duplicates)}")
        if sum(1 for p in self.providers if p.default) > 1:
            raise ValueError("more than one provider is marked default")
        if self.default_provider() is None:
            raise ValueError("no default provider: mark an enabled provider as default")
        known = self.effective_providers()
        for app in self.applications:
            unknown = [p for p in app.providers if p not in known]
            if unknown:
                raise ValueError(f"application {app.application_id}: no provider {unknown[0]}")
            targets = [a.model for a in app.aliases] + ([app.fallback] if app.fallback else [])
            for ref in targets:
                provider, _ = split_model_ref(ref)
                if provider is not None and provider not in known:
                    raise ValueError(f"application {app.application_id}: no provider {provider}")
            if app.fallback:
                try:
                    target = self.resolve(app, app.fallback, check_models=False)
                except ResolveError as exc:
                    raise ValueError(f"application {app.application_id}: fallback: {exc}") from None
                if target.credential == "pass_through":
                    raise ValueError(
                        f"application {app.application_id}: a fallback cannot pass the caller's "
                        "provider key through (it belongs to the first provider); use a provider "
                        "with credential secret or none"
                    )
        return self

    # ------------------------------------------------------------------ lookups

    def effective_providers(self) -> dict[str, Provider]:
        providers = {p.provider_id: p for p in self.providers}
        providers.setdefault(BUILTIN_OPENAI.provider_id, BUILTIN_OPENAI)
        return providers

    def default_provider(self) -> Provider | None:
        marked = next((p for p in self.providers if p.default), None)
        if marked is not None:
            return marked if marked.enabled else None
        openai = self.effective_providers()[BUILTIN_OPENAI.provider_id]
        return openai if openai.enabled else None

    def application(self, application_id: str) -> Application | None:
        return next((a for a in self.applications if a.application_id == application_id), None)

    # ------------------------------------------------------------------ resolution

    def resolve(self, app: Application, model: str, *, check_models: bool = True) -> Route:
        """The call target for ``model`` sent by ``app`` (ADR-0039 §3)."""

        if check_models and not app.accepts(model):
            raise ResolveError(
                404, "model_not_allowed", "this application does not offer that model"
            )
        target = next((a.model for a in app.aliases if a.name == model), model)
        provider_id, upstream_model = split_model_ref(target)
        if provider_id is None:
            provider = self.default_provider()
            if provider is None:
                raise ResolveError(404, "unknown_provider", "there is no default provider")
        else:
            provider = self.effective_providers().get(provider_id)
            if provider is None or not provider.enabled:
                raise ResolveError(
                    404, "unknown_provider", "no such provider for this organization"
                )
        if app.providers and provider.provider_id not in app.providers:
            raise ResolveError(
                403, "provider_not_allowed", "this application may not use that provider"
            )
        defaults = self.gateway
        return Route(
            route_id=app.application_id,
            enabled=app.enabled,
            upstream=Upstream(
                kind=provider.kind,
                base_url=provider.base_url or OPENAI_BASE_URL,
                model=upstream_model,
                region=provider.region,
            ),
            provider_id=provider.provider_id,
            credential=provider.credential,
            secret=provider.secret,
            guardrail=app.guardrail,
            destination_class=app.destination_class,
            stream_mode=app.stream_mode or defaults.stream_mode,
            refusal=app.refusal or defaults.refusal,
            refusal_text=app.refusal_text or defaults.refusal_text,
            unscanned_parts=app.unscanned_parts or defaults.unscanned_parts,
            restore_placeholders=(
                defaults.restore_placeholders
                if app.restore_placeholders is None
                else app.restore_placeholders
            ),
            timeouts_ms=app.timeouts_ms or defaults.timeouts_ms,
        )

    def models_provider(self, app: Application) -> Provider | None:
        """The provider whose model list ``GET /v1/models`` shows for ``app``: the default if
        the application may use it, else its first allowed provider."""

        default = self.default_provider()
        if default is not None and (not app.providers or default.provider_id in app.providers):
            return default
        providers = self.effective_providers()
        return next((providers[p] for p in app.providers if providers[p].enabled), None)


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
