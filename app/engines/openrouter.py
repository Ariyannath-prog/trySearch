"""OpenRouter Chat Completions API engine adapter.

OpenRouter is a routing layer over many underlying models, addressed by slug
(e.g. "deepseek/deepseek-chat", "openai/gpt-4o-mini"). This adapter is a
separate registry entry from app/engines/deepseek.py and does not modify it -
the native DeepSeek integration keeps calling api.deepseek.com directly.
"""

import os
import time
from decimal import Decimal

from app.engines.base import EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request


API_ROOT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-chat"

# OpenRouter's docs ask for these to attribute traffic in its dashboard. Not
# required for the API to function, just good citizenship - fixed values,
# not a secret, so no env var is needed for them.
ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://trysearch.aevix.xyz",
    "X-Title": "trySearch",
}


def openrouter_model():
    return os.environ.get(
        "OPENROUTER_MODEL",
        DEFAULT_MODEL,
    )


def call_openrouter(
    prompt,
    *,
    timeout_s=90,
    api_key=None,
):
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")

    if not api_key:
        raise ProviderAPIError(
            "No OpenRouter provider credential is configured."
        )

    payload = {
        "model": openrouter_model(),
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": False,
    }

    return external_json_request(
        API_ROOT,
        method="POST",
        payload=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            **ATTRIBUTION_HEADERS,
        },
        timeout=timeout_s,
    )


def openrouter_answer_text(payload):
    choices = (payload or {}).get("choices") or []

    if not choices:
        return ""

    message = choices[0].get("message") or {}
    text = message.get("content")

    if isinstance(text, str):
        return text.strip()

    return ""


class OpenRouterAdapter:
    """Any OpenRouter-routed model, through its Chat Completions API."""

    key = "openrouter"
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

        payload = call_openrouter(
            prompt,
            timeout_s=max(1, timeout_s),
            api_key=credential,
        )

        latency_ms = round(
            (time.monotonic() - started) * 1000
        )

        answer_text = openrouter_answer_text(payload or {})

        model_version = str(
            (payload or {}).get("model")
            or openrouter_model()
        )

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
