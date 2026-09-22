"""Meta Model API adapter for Muse Spark."""

import math
import os
import time
from decimal import Decimal

from app.engines.base import Citation, EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request


API_ROOT = "https://api.meta.ai/v1/responses"
DEFAULT_MODEL = "muse-spark-1.3"

INPUT_PRICE_PER_MILLION = Decimal("1.25")
OUTPUT_PRICE_PER_MILLION = Decimal("4.25")


def meta_model():
    return os.environ.get("META_MODEL", DEFAULT_MODEL)


def meta_search_context():
    value = os.environ.get("META_SEARCH_CONTEXT", "medium").strip().lower()
    return value if value in {"low", "medium", "high"} else "medium"


def call_meta_response(prompt, *, api_key=None, timeout_s=90):
    api_key = api_key or os.environ.get("META_API_KEY")

    if not api_key:
        raise ProviderAPIError(
            "No Meta provider credential is configured."
        )

    payload = {
        "model": meta_model(),
        "input": prompt,
        "tools": [
            {
                "type": "web_search",
                "search_context_size": meta_search_context(),
            }
        ],
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


def meta_answer_text(payload):
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

    if isinstance(output_text, str):
        return output_text.strip()

    return ""


def meta_answer_citations(payload):
    citations = []

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
                    citations.append(
                        {
                            "url": url.strip(),
                            "title": str(title)[:2000],
                        }
                    )

    return citations


def meta_cost(payload):
    usage = (payload or {}).get("usage") or {}

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    if not isinstance(input_tokens, (int, float)):
        return Decimal("0")

    if not isinstance(output_tokens, (int, float)):
        return Decimal("0")

    return (
        Decimal(str(input_tokens))
        * INPUT_PRICE_PER_MILLION
        / Decimal("1000000")
        + Decimal(str(output_tokens))
        * OUTPUT_PRICE_PER_MILLION
        / Decimal("1000000")
    )


def meta_estimated_cost(prompt):
    estimated_input_tokens = max(
        1,
        math.ceil(len(prompt or "") / 4),
    )

    estimated_output_tokens = 1000

    return (
        Decimal(estimated_input_tokens)
        * INPUT_PRICE_PER_MILLION
        / Decimal("1000000")
        + Decimal(estimated_output_tokens)
        * OUTPUT_PRICE_PER_MILLION
        / Decimal("1000000")
    )


class MetaAdapter:
    """Meta Model API / Muse Spark through the Responses API."""

    key = "meta"
    source_type = "api"
    adapter_version = "2026.09.1"
    supports_citations = True
    supports_regions = False

    def estimate_cost(self, prompt):
        return meta_estimated_cost(prompt)

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

        payload = call_meta_response(
            prompt,
            api_key=credential,
            timeout_s=max(1, timeout_s),
        )

        latency_ms = round(
            (time.monotonic() - started) * 1000
        )

        answer_text = meta_answer_text(payload or {})
        raw_citations = meta_answer_citations(payload or {})

        citations = tuple(
            Citation(
                position=index,
                url=item["url"],
                title=item["title"],
            )
            for index, item in enumerate(
                raw_citations,
                start=1,
            )
        )

        model_version = str(
            (payload or {}).get("model")
            or meta_model()
        )

        cost_usd = meta_cost(payload or {})
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
