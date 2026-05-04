"""Integration test: validate the inference endpoint is reachable and well-formed.

Skipped automatically when OPENAI_BASE_URL is not set, so it never blocks CI
on a machine that hasn't configured a serve endpoint.

Run manually (or in the 'serve' CI job) after deploying modal/serve.py:

    OPENAI_BASE_URL=https://your-org--...modal.run \
    OPENAI_API_KEY=modal \
    OPENCODE_MODEL=qwen2.5-coder-32b \
    pytest tests/integration/test_serve_reachable.py -v -m serve

Checks
------
1. GET  /v1/models   — endpoint is up, returns HTTP 200
2.      model list   — response contains the model name from OPENCODE_MODEL
3. POST /v1/chat/completions — minimal request completes (non-streaming)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

# Read at collection time — test is skipped entirely when unset.
_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
_API_KEY = os.environ.get("OPENAI_API_KEY", "modal")
_MODEL = os.environ.get("OPENCODE_MODEL", "")

pytestmark = pytest.mark.serve


def _need_serve_env() -> None:
    if not _BASE_URL:
        pytest.skip("OPENAI_BASE_URL not set — skipping serve reachability tests")
    if not _MODEL:
        pytest.skip("OPENCODE_MODEL not set — skipping serve reachability tests")


def _get(path: str) -> dict:
    url = f"{_BASE_URL}/v1/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_API_KEY}"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


def _post(path: str, body: dict) -> dict:
    url = f"{_BASE_URL}/v1/{path.lstrip('/')}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read())


# ──────────────────────────────────────────────────────────────── tests


@pytest.mark.serve
def test_models_endpoint_returns_200():
    """GET /v1/models returns HTTP 200 — confirms the server is up."""
    _need_serve_env()
    try:
        data = _get("models")
    except urllib.error.HTTPError as exc:
        pytest.fail(f"GET /v1/models returned HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        pytest.fail(f"Cannot reach {_BASE_URL}/v1/models: {exc.reason}")

    assert "data" in data, f"Unexpected /v1/models response shape: {data}"


@pytest.mark.serve
def test_models_endpoint_contains_expected_model():
    """Model list includes OPENCODE_MODEL — confirms the right weights are loaded."""
    _need_serve_env()
    try:
        data = _get("models")
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        pytest.fail(f"Cannot reach /v1/models: {exc}")

    model_ids = [m.get("id", "") for m in data.get("data", [])]
    assert any(_MODEL in mid for mid in model_ids), (
        f"Expected model {_MODEL!r} not found in /v1/models.\nAvailable models: {model_ids}"
    )


@pytest.mark.serve
def test_chat_completion_returns_valid_response():
    """POST /v1/chat/completions with a minimal prompt returns a well-formed response."""
    _need_serve_env()
    try:
        response = _post(
            "chat/completions",
            {
                "model": _MODEL,
                "messages": [{"role": "user", "content": "Reply with just the word PONG."}],
                "max_tokens": 16,
                "temperature": 0,
            },
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace") if hasattr(exc, "read") else ""
        pytest.fail(f"POST /v1/chat/completions returned HTTP {exc.code}: {body[:300]}")
    except urllib.error.URLError as exc:
        pytest.fail(f"Cannot reach /v1/chat/completions: {exc.reason}")

    assert "choices" in response, f"Missing 'choices' in response: {response}"
    assert len(response["choices"]) > 0, "Empty choices list"
    choice = response["choices"][0]
    assert "message" in choice, f"Missing 'message' in choice: {choice}"
    content = choice["message"].get("content", "")
    assert content, "Empty content in response message"
