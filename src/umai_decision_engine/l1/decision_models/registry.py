"""Decision-model providers by name, built from settings (ADR-0040).

A bundle's ``l1b.provider`` names an entry of ``UMAI_DE_DECISION_PROVIDERS``; keys come from
``UMAI_DE_DECISION_PROVIDER_KEYS``. One shared async HTTP client serves every provider; it never
follows redirects and every call passes its own timeout. Idle connections are kept for
``UMAI_DE_DECISION_KEEPALIVE_S`` (httpx would close them after 5 s), and :meth:`warm_up` opens
them at startup, so the first decisions do not pay for a new connection (ADR-0040 amendment
2026-10-26).
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from umai_decision_engine.l1.decision_models.base import DecisionModel
from umai_decision_engine.l1.decision_models.fake import FakeDecisionModel
from umai_decision_engine.settings import Settings

logger = logging.getLogger("umai.de.l1b")


class DecisionModels:
    def __init__(
        self, models: dict[str, DecisionModel], client: httpx.AsyncClient | None = None
    ) -> None:
        self._models = models
        self._client = client

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> DecisionModels:
        models: dict[str, DecisionModel] = {}
        client: httpx.AsyncClient | None = None
        for name, config in settings.decision_providers.items():
            if config.adapter == "fake":
                models[name] = FakeDecisionModel(name)
                continue
            from umai_decision_engine.l1.decision_models.openai_decisions import (
                OpenAIDecisions,
            )

            if client is None:
                client = httpx.AsyncClient(
                    follow_redirects=False,
                    transport=transport,
                    # httpx's connection limits, with our keep-alive instead of its 5 s
                    limits=httpx.Limits(
                        max_connections=100,
                        max_keepalive_connections=20,
                        keepalive_expiry=settings.decision_keepalive_s,
                    ),
                )
            key = settings.decision_provider_keys.get(name)
            models[name] = OpenAIDecisions(
                name,
                client,
                base_url=config.base_url or "https://api.openai.com/v1",
                model=config.model or "gpt-6-luna",
                api_key=key.get_secret_value() if key else None,
            )
        return cls(models, client)

    @classmethod
    def answering_for_all(cls, model: DecisionModel) -> DecisionModels:
        """One model under every provider name (golden tests, offline development)."""

        registry = cls({})
        registry._any = model
        return registry

    _any: DecisionModel | None = None

    def get(self, name: str | None) -> DecisionModel | None:
        if self._any is not None:
            return self._any
        return self._models.get(name) if name else None

    async def warm_up(self, timeout_s: float) -> dict[str, str]:
        """Warm every provider in parallel before the process takes traffic. A failure is
        logged and changes nothing else: the first decision then opens its own connection, and
        a provider that is really down meets the bundle's failure policy there."""

        async def one(name: str, model: DecisionModel) -> str:
            started = time.perf_counter()
            outcome = await model.warm_up(timeout_s=timeout_s)
            log = logger.info if outcome in ("ok", "none") else logger.warning
            log(
                "l1b.warm_up provider=%s adapter=%s outcome=%s latency_ms=%s",
                name,
                model.adapter,
                outcome,
                round((time.perf_counter() - started) * 1000, 3),
            )
            return outcome

        names = list(self._models)
        outcomes = await asyncio.gather(*(one(n, self._models[n]) for n in names))
        return dict(zip(names, outcomes, strict=True))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
