"""UMAI gateway: embeddings with input checks (gateway-api 0.5). Synthetic content only."""

from __future__ import annotations

import json

from tests.integration.test_gateway import (  # noqa: F401  (fixtures)
    CARD,
    EMAIL,
    app_yaml,
    make_gateway,
    upstream,
)


def vectors(body: dict) -> dict:
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": [0.1, 0.2]} for i in range(len(inputs))
        ],
        "model": "emb-x",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


def test_texts_are_masked_before_embedding(make_gateway, upstream) -> None:  # noqa: F811
    upstream.reply = vectors
    client = make_gateway()
    response = client.post(
        "/v1/embeddings", json={"model": "gpt-eu", "input": [f"İletişim {EMAIL}", "Toplantı notu"]}
    )
    assert response.status_code == 200
    assert len(response.json()["data"]) == 2
    request = upstream.requests[-1]
    assert str(request.url) == "https://api.openai.com/v1/embeddings"
    assert json.loads(request.content)["input"] == ["İletişim <EMAIL_ADDRESS_1>", "Toplantı notu"]

    response = client.post("/v1/embeddings", json={"model": "gpt-eu", "input": f"Yaz {EMAIL}"})
    assert json.loads(upstream.requests[-1].content)["input"] == "Yaz <EMAIL_ADDRESS_1>"


def test_blocked_text_is_never_embedded(make_gateway, upstream) -> None:  # noqa: F811
    # Embeddings always refuse with 400, whatever the refusal style.
    client = make_gateway(applications=app_yaml(refusal="message"))
    response = client.post("/v1/embeddings", json={"model": "gpt-eu", "input": [f"Kart {CARD}"]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "umai_blocked"
    assert upstream.requests == []


def test_token_arrays_follow_the_route(make_gateway, upstream) -> None:  # noqa: F811
    upstream.reply = vectors
    client = make_gateway()
    response = client.post("/v1/embeddings", json={"model": "gpt-eu", "input": [1, 2, 3]})
    assert response.status_code == 400 and response.json()["error"]["code"] == "unsupported_content"
    client = make_gateway(applications=app_yaml(unscanned_parts="pass_unscanned"))
    response = client.post("/v1/embeddings", json={"model": "gpt-eu", "input": [[1, 2], "metin"]})
    assert response.status_code == 200
    assert response.headers["X-Umai-Unscanned-Parts"] == "1"
