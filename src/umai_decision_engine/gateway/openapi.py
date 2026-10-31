"""OpenAPI metadata for the gateway's routes (contract gateway-api, ADR-0054).

The gateway passes provider bodies through and reads them itself (ADR-0034 §2), so FastAPI
sees no request model: the request bodies, the UMAI headers and the error bodies are described
here. Only the fields the gateway reads are named; every other field is the provider's and is
forwarded unchanged (``additionalProperties``). Synthetic values only (AGENTS rule 2).
"""

from __future__ import annotations

from typing import Any

from umai_decision_engine.contracts.gateway import GatewayErrorBody

_PARAMETERS: list[dict[str, Any]] = [
    {
        "name": "X-Umai-Application",
        "in": "header",
        "required": False,
        "description": "The application to call as. Needed only when the key belongs to no "
        "application and the organization has several; a key that belongs to an application "
        "uses that one.",
        "schema": {"type": "string"},
    },
    {
        "name": "X-Umai-Provider-Key",
        "in": "header",
        "required": False,
        "description": "Your provider's API key (e.g. OpenAI's), when the provider is set up "
        "with your own key (`pass_through`, the built-in OpenAI provider). Sent to the provider "
        "only; never stored, logged or echoed.",
        "schema": {"type": "string"},
    },
    {
        "name": "X-Umai-Request-Id",
        "in": "header",
        "required": False,
        "description": "Your id for the call (at most 128 characters); generated when absent "
        "and returned in the response header of the same name.",
        "schema": {"type": "string", "maxLength": 128},
    },
    {
        "name": "X-Umai-Session-Id",
        "in": "header",
        "required": False,
        "description": "Your id for the conversation.",
        "schema": {"type": "string"},
    },
    {
        "name": "X-Umai-Subject-User",
        "in": "header",
        "required": False,
        "description": "Your pseudonymous id for the person; falls back to the body's "
        "`safety_identifier`, then `user`.",
        "schema": {"type": "string"},
    },
    {
        "name": "X-Umai-Subject-Role",
        "in": "header",
        "required": False,
        "description": "The person's role, for policies that use it.",
        "schema": {"type": "string"},
    },
    {
        "name": "X-Umai-Subject-Department",
        "in": "header",
        "required": False,
        "description": "The person's department, for policies that use it.",
        "schema": {"type": "string"},
    },
    {
        "name": "X-Umai-Agent-Id",
        "in": "header",
        "required": False,
        "description": "Your id for the AI agent making the call.",
        "schema": {"type": "string"},
    },
]

_MODEL = {
    "type": "string",
    "description": "The model, as your application sends it. An alias or `@<provider>/<model>` "
    "picks a provider the organization connected; other names go to the default provider.",
    "examples": ["gpt-4.1-mini"],
}

_RESPONSE_HEADERS: dict[str, Any] = {
    "X-Umai-Request-Id": {
        "description": "The call's id; quote it when you contact support.",
        "schema": {"type": "string"},
    },
    "X-Umai-Application": {
        "description": "The application the call ran as.",
        "schema": {"type": "string"},
    },
    "X-Umai-Event-Id": {
        "description": "The recorded decision of the input check.",
        "schema": {"type": "string"},
    },
    "X-Umai-Input-Decision": {
        "description": "The input check's action, e.g. `ALLOW_WITH_MODIFICATIONS`.",
        "schema": {"type": "string"},
    },
    "X-Umai-Output-Decision": {
        "description": "The strictest action of the answer checks (non-streaming answers and "
        "`buffer` streams).",
        "schema": {"type": "string"},
    },
    "X-Umai-Upstream-Error": {
        "description": "`true` when the body is the provider's own error, passed on.",
        "schema": {"type": "string"},
    },
    "X-Umai-Fallback": {
        "description": "Present when the application's fallback target answered.",
        "schema": {"type": "string"},
    },
}

_BLOCKED = {
    "error": {
        "message": "Blocked by UMAI guardrail gr-demo-data-protection: L0: TR_TCKN detected "
        "by policy Kimlik verileri v2",
        "type": "umai_guardrail",
        "code": "umai_blocked",
        "umai": {
            "request_id": "gw-3f9c1e0b",
            "event_id": "ta-195c97496f4748b88442e60f6ed6e1b9",
            "phase": "PRE_LLM",
            "action": "BLOCK",
            "entities": ["TR_TCKN"],
            "matched": ["identity-data"],
        },
    }
}


def _gateway_error(code: str, message: str) -> dict[str, Any]:
    return {"value": {"error": {"message": message, "type": "umai_gateway", "code": code}}}


def _error(description: str, examples: dict[str, Any] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"model": GatewayErrorBody, "description": description}
    if examples:
        response["content"] = {"application/json": {"examples": examples}}
    return response


_ADMISSION: dict[int | str, dict[str, Any]] = {
    401: _error(
        "`invalid_credential`: missing, unknown, expired or revoked UMAI key.",
        {
            "invalid_credential": _gateway_error(
                "invalid_credential", "missing or invalid API credential"
            )
        },
    ),
    403: _error(
        "`ip_not_allowed`, `insufficient_scope`, `application_mismatch` or `provider_not_allowed`.",
        {
            "ip_not_allowed": _gateway_error(
                "ip_not_allowed", "this key may not be used from this address"
            )
        },
    ),
    404: _error("`unknown_application`, `unknown_provider` or `model_not_allowed`."),
    429: {
        **_error("`rate_limited`, `too_many_failed_attempts` or `quota_exceeded`."),
        "headers": {
            "Retry-After": {
                "description": "Seconds to wait before the next attempt.",
                "schema": {"type": "integer"},
            }
        },
    },
}

_UPSTREAM: dict[int | str, dict[str, Any]] = {
    500: _error("`provider_misconfigured`: the provider's key is not set up on UMAI's side."),
    502: _error("`upstream_unreachable` or `upstream_invalid_response`."),
    504: _error("`upstream_timeout`: the provider did not answer in time."),
}


def _call_responses(answer: str, *, streams: bool) -> dict[int | str, dict[str, Any]]:
    content: dict[str, Any] = {"application/json": {"schema": {"type": "object"}}}
    if streams:
        content["text/event-stream"] = {"schema": {"type": "string"}}
    return {
        200: {
            "description": answer,
            "headers": _RESPONSE_HEADERS,
            "content": content,
        },
        400: _error(
            "`umai_blocked` / `umai_step_up_required` (the guardrail refused; `type: "
            "umai_guardrail`), or `invalid_request`, `application_required`, "
            "`provider_key_required`, `unsupported_content`.",
            {
                "umai_blocked": {"value": _BLOCKED},
                "provider_key_required": _gateway_error(
                    "provider_key_required",
                    "this provider needs your provider key in the X-Umai-Provider-Key header",
                ),
            },
        ),
        **_ADMISSION,
        413: _error("`request_too_large` (body bytes) or `input_too_large` (characters to scan)."),
        **_UPSTREAM,
    }


def _body(description: str, schema: dict[str, Any], examples: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameters": _PARAMETERS,
        "requestBody": {
            "required": True,
            "description": description,
            "content": {"application/json": {"schema": schema, "examples": examples}},
        },
    }


_TEXT_PARTS = {
    "description": "A string, or an array of content parts. `text` parts are checked; other "
    "parts (images, audio, files) are refused unless the application forwards them unscanned.",
}

CHAT_COMPLETIONS: dict[str, Any] = {
    "summary": "Chat Completions, checked by your guardrail",
    "description": """OpenAI's Chat Completions, with your guardrail on the way in and out. The
gateway checks every message it forwards (history included), masks or refuses, sends the call
to the provider, then checks the answer and its tool calls. Values it masked in your messages
are put back in the answer for you.

A refusal is HTTP 400 with `code: umai_blocked` (the OpenAI SDKs raise `BadRequestError`), or,
when the application is set to answer with a message, HTTP 200 with
`finish_reason: "content_filter"`. Streams (`"stream": true`) are checked while they flow.

Every field not named here is forwarded unchanged. Requires the `gateway` scope.""",
    "responses": _call_responses(
        "The provider's answer (a `chat.completion`, or `chat.completion.chunk` events when "
        "streaming), after the answer checks.",
        streams=True,
    ),
    "openapi_extra": _body(
        "An OpenAI Chat Completions request.",
        {
            "type": "object",
            "required": ["model", "messages"],
            "properties": {
                "model": _MODEL,
                "messages": {
                    "type": "array",
                    "description": "The conversation. `system`, `developer`, `user`, "
                    "`assistant` (with tool-call arguments) and `tool` messages are checked.",
                    "items": {
                        "type": "object",
                        "required": ["role"],
                        "properties": {
                            "role": {"type": "string"},
                            "content": _TEXT_PARTS,
                        },
                        "additionalProperties": True,
                    },
                },
                "stream": {"type": "boolean", "description": "Stream the answer as events."},
                "n": {"type": "integer", "description": "Each choice is checked on its own."},
            },
            "additionalProperties": True,
        },
        {
            "masked": {
                "summary": "A prompt with an identity number",
                "value": {
                    "model": "gpt-4.1-mini",
                    "messages": [
                        {
                            "role": "user",
                            "content": "10000000146 numaralı müşterinin kredi limitini özetle.",
                        }
                    ],
                },
            }
        },
    ),
}

RESPONSES: dict[str, Any] = {
    "summary": "Responses API, checked by your guardrail",
    "description": """OpenAI's Responses API with the same checks, masking, restoration and
refusals as Chat Completions. `instructions`, `input` text and tool outputs are checked before
the call; output text and tool calls after it. With a message refusal the response has
`status: "incomplete"` and `incomplete_details.reason: "content_filter"`.

Items the provider keeps (`previous_response_id`, `item_reference`) and what hosted tools read
inside the provider are not checked by UMAI. Requires the `gateway` scope.""",
    "responses": _call_responses(
        "The provider's response object (or Responses events when streaming), after the "
        "answer checks.",
        streams=True,
    ),
    "openapi_extra": _body(
        "An OpenAI Responses request.",
        {
            "type": "object",
            "required": ["model", "input"],
            "properties": {
                "model": _MODEL,
                "instructions": {"type": "string", "description": "Checked as a system message."},
                "input": {
                    "description": "A string, or an array of input items; message text, tool "
                    "calls and tool outputs are checked.",
                },
                "stream": {"type": "boolean", "description": "Stream the answer as events."},
            },
            "additionalProperties": True,
        },
        {
            "masked": {
                "summary": "A prompt with an IBAN",
                "value": {
                    "model": "gpt-4.1-mini",
                    "input": "TR330006100519786457841326 hesabına gelen son ödemeleri listele.",
                },
            }
        },
    ),
}

EMBEDDINGS: dict[str, Any] = {
    "summary": "Embeddings, with the input checked and masked",
    "description": """OpenAI's embeddings with an input check: each string is checked and masked
before it is embedded, so a search index never stores vectors of the values your guardrail
masks. A refusal is always HTTP 400 `umai_blocked`. Token arrays cannot be checked and follow
the application's setting for unscanned content. Requires the `gateway` scope.""",
    "responses": _call_responses("The provider's embeddings, unchanged.", streams=False),
    "openapi_extra": _body(
        "An OpenAI embeddings request.",
        {
            "type": "object",
            "required": ["model", "input"],
            "properties": {
                "model": _MODEL,
                "input": {
                    "description": "A string or an array of strings; each one is checked.",
                },
            },
            "additionalProperties": True,
        },
        {
            "texts": {
                "summary": "Two passages for a search index",
                "value": {
                    "model": "text-embedding-3-small",
                    "input": [
                        "Müşteri hizmetleri hafta içi 09:00-18:00 arası açıktır.",
                        "İade talebi için 10000000146 numaralı başvuruyu kullanın.",
                    ],
                },
            }
        },
    ),
}

MODELS: dict[str, Any] = {
    "summary": "List the models your application may use",
    "description": """The application's model names and aliases when it has a list; otherwise
the provider's own list. Never counts toward the monthly quota. Requires the `gateway`
scope.""",
    "responses": {
        200: {
            "description": "An OpenAI model list.",
            "headers": {"X-Umai-Application": _RESPONSE_HEADERS["X-Umai-Application"]},
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {
                        "object": "list",
                        "data": [
                            {
                                "id": "gpt-4.1-mini",
                                "object": "model",
                                "created": 0,
                                "owned_by": "umai",
                            }
                        ],
                    },
                }
            },
        },
        400: _error("`application_required` or `provider_key_required`."),
        **_ADMISSION,
        **_UPSTREAM,
    },
    "openapi_extra": {"parameters": _PARAMETERS[:2]},
}
