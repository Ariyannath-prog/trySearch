"""Claude / Anthropic Messages API engine adapter."""

import os
import time
from decimal import Decimal

from app.engines.base import EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request


API_ROOT = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"


def anthropic_model():
    return os.environ.get(
        "ANTHROPIC_MODEL",
        DEFAULT_MODEL,
    )


def call_anthropic(
    prompt,
    *,
    timeout_s=90,
    api_key=None,
):
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        raise ProviderAPIError(
            "No Anthropic provider credential is configured."
        )

    payload = {
        "model": anthropic_model(),
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    return external_json_request(
        API_ROOT,
        method="POST",
        payload=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=timeout_s,
    )


def anthropic_answer_text(payload):
    parts = []

    for block in (payload or {}).get("content") or []:
        if not isinstance(block, dict):
            continue

        if block.get("type") != "text":
            continue

        text = block.get("text")

        if isinstance(text, str) and text.strip():
            parts.append(text.strip())

    return "\n".join(parts).strip()


class AnthropicAdapter:
    """Claude through the Anthropic Messages API."""

    key = "anthropic"
    source_type = "api"
    adapter_version = "2026.09.1"
    supports_citations = False
    supports_regions = False

    def estimate_cost(self, prompt):
        return Decimal("0")

    @guard
    def run(
        self,
        prompt,
        *,
        region=None,
        timeout_s=60,
        credential=None,
    ):
        started = time.monotonic()

        payload = call_anthropic(
            prompt,
            timeout_s=max(1, timeout_s),
            api_key=credential,
        )

        latency_ms = round(
            (time.monotonic() - started) * 1000
        )

        answer_text = anthropic_answer_text(payload or {})

        model_version = str(
            (payload or {}).get("model")
            or anthropic_model()
        )

        usage = (payload or {}).get("usage") or {}

        raw_response = payload or {}

        if not answer_text:
            return EngineResult(
                status="empty",
                answer_text="",
                citations=(),
                raw_response=raw_response,
                model_version=model_version,
                cost_usd=Decimal("0"),
                latency_ms=latency_ms,
            )

        return EngineResult(
            status="ok",
            answer_text=answer_text,
            citations=(),
            raw_response=raw_response,
            model_version=model_version,
            cost_usd=Decimal("0"),
            latency_ms=latency_ms,
        )
