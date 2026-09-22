"""DeepSeek Chat Completions API engine adapter."""

import os
import time
from decimal import Decimal

from app.engines.base import EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request


API_ROOT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"


def deepseek_model():
    return os.environ.get(
        "DEEPSEEK_MODEL",
        DEFAULT_MODEL,
    )


def call_deepseek(
    prompt,
    *,
    timeout_s=90,
    api_key=None,
):
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")

    if not api_key:
        raise ProviderAPIError(
            "No DeepSeek provider credential is configured."
        )

    payload = {
        "model": deepseek_model(),
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
        },
        timeout=timeout_s,
    )


def deepseek_answer_text(payload):
    choices = (payload or {}).get("choices") or []

    if not choices:
        return ""

    message = choices[0].get("message") or {}
    text = message.get("content")

    if isinstance(text, str):
        return text.strip()

    return ""


class DeepSeekAdapter:
    """DeepSeek through the Chat Completions API."""

    key = "deepseek"
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

        payload = call_deepseek(
            prompt,
            timeout_s=max(1, timeout_s),
            api_key=credential,
        )

        latency_ms = round(
            (time.monotonic() - started) * 1000
        )

        answer_text = deepseek_answer_text(payload or {})

        model_version = str(
            (payload or {}).get("model")
            or deepseek_model()
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
