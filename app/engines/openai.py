"""OpenAI Responses API engine with hosted web search."""

import math
import os
import time
from decimal import Decimal

from app.engines.base import Citation, EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request


API_ROOT = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6-luna"
INPUT_PRICE_PER_MILLION = Decimal("0.20")
OUTPUT_PRICE_PER_MILLION = Decimal("1.20")


def openai_model():
    return os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)


def openai_search_context():
    value = os.environ.get("OPENAI_SEARCH_CONTEXT", "medium").strip().lower()
    return value if value in {"low", "medium", "high"} else "medium"


def call_openai_response(prompt, *, region=None, api_key=None):
    """Call the OpenAI Responses API.

    The runtime credential is preferred. The environment fallback exists only
    for backwards-compatible local development and isolated tests.
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ProviderAPIError("No OpenAI provider credential is configured.")

    search_tool = {
        "type": "web_search",
        "search_context_size": openai_search_context(),
    }

    if region and isinstance(region, str) and len(region) == 2 and region.isalpha():
        search_tool["user_location"] = {
            "type": "approximate",
            "country": region.upper(),
        }

    payload = {
        "model": openai_model(),
        "tools": [search_tool],
        "input": prompt,
    }

    return external_json_request(
        API_ROOT,
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=90,
    )


def openai_answer_text(payload):
    """Extract assistant text from Responses API output items."""
    parts = []

    for item in (payload or {}).get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue

        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") != "output_text":
                continue

            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    if parts:
        return "\n".join(parts).strip()

    output_text = (payload or {}).get("output_text")
    return output_text.strip() if isinstance(output_text, str) else ""


def openai_answer_citations(payload):
    """Extract URL citation annotations from Responses API message content."""
    citations = []

    for item in (payload or {}).get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue

        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue

            for annotation in content.get("annotations") or []:
                if not isinstance(annotation, dict):
                    continue
                if annotation.get("type") != "url_citation":
                    continue

                url = annotation.get("url")
                title = annotation.get("title") or ""

                nested = annotation.get("url_citation")
                if isinstance(nested, dict):
                    url = nested.get("url") or url
                    title = nested.get("title") or title

                if isinstance(url, str) and url.strip():
                    citations.append({
                        "url": url.strip(),
                        "title": str(title)[:2000],
                    })

    return citations


def openai_cost(payload):
    """Calculate model-token cost from provider usage when available."""
    usage = (payload or {}).get("usage") or {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    if not isinstance(input_tokens, (int, float)) or not isinstance(
        output_tokens, (int, float)
    ):
        return Decimal("0")

    return (
        Decimal(str(input_tokens)) * INPUT_PRICE_PER_MILLION / Decimal("1000000")
        + Decimal(str(output_tokens)) * OUTPUT_PRICE_PER_MILLION / Decimal("1000000")
    )


def openai_estimated_cost(prompt):
    """Approximate one response cost using a simple character/token heuristic."""
    estimated_input_tokens = max(1, math.ceil(len(prompt or "") / 4))
    estimated_output_tokens = 1000

    return (
        Decimal(estimated_input_tokens) * INPUT_PRICE_PER_MILLION / Decimal("1000000")
        + Decimal(estimated_output_tokens) * OUTPUT_PRICE_PER_MILLION / Decimal("1000000")
    )


class OpenAIAdapter:
    """OpenAI Responses API with hosted web search."""

    key = "openai"
    source_type = "api"
    adapter_version = "2026.09.1"
    supports_citations = True
    supports_regions = True

    def estimate_cost(self, prompt):
        return openai_estimated_cost(prompt)

    @guard
    def run(self, prompt, *, region=None, timeout_s=60, credential=None):
        started = time.monotonic()

        payload = call_openai_response(
            prompt,
            region=region,
            api_key=credential,
        )

        latency_ms = round((time.monotonic() - started) * 1000)
        answer_text = openai_answer_text(payload or {})
        raw_citations = openai_answer_citations(payload or {})

        citations = tuple(
            Citation(
                position=index,
                url=item["url"],
                title=item["title"],
            )
            for index, item in enumerate(raw_citations, start=1)
        )

        cost_usd = openai_cost(payload or {})
        model_version = str(
            (payload or {}).get("model") or openai_model()
        )

        raw_response = payload or {}

        if not answer_text:
            return EngineResult(
                status="empty",
                answer_text="",
                citations=citations,
                raw_response=raw_response,
                model_version=model_version,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            )

        return EngineResult(
            status="ok",
            answer_text=answer_text,
            citations=citations,
            raw_response=raw_response,
            model_version=model_version,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
