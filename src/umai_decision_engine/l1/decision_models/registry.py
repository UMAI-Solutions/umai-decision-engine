"""Decision-model providers by name, built from settings (ADR-0040).

A bundle's ``l1b.provider`` names an entry of ``UMAI_DE_DECISION_PROVIDERS``; keys come from
``UMAI_DE_DECISION_PROVIDER_KEYS``. One shared async HTTP client serves every provider; it never
follows redirects and every call passes its own timeout.
"""

from __future__ import annotations

import httpx

from umai_decision_engine.l1.decision_models.base import DecisionModel
from umai_decision_engine.l1.decision_models.fake import FakeDecisionModel
from umai_decision_engine.settings import Settings


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
                client = httpx.AsyncClient(follow_redirects=False, transport=transport)
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

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
