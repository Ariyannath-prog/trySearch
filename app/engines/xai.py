"""Grok / xAI Responses API engine adapter."""

import os
import time
from decimal import Decimal

from app.engines.base import Citation, EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request


API_ROOT = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.7"


def xai_model():
    return os.environ.get(
        "XAI_MODEL",
        DEFAULT_MODEL,
    )


def call_xai(
    prompt,
    *,
    timeout_s=90,
    api_key=None,
):
    api_key = api_key or os.environ.get("XAI_API_KEY")

    if not api_key:
        raise ProviderAPIError(
            "No xAI provider credential is configured."
        )

    payload = {
        "model": xai_model(),
        "input": prompt,
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


def xai_answer_text(payload):
    parts = []

    output_text = (payload or {}).get("output_text")

    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    for item in (payload or {}).get("output") or []:
        if not isinstance(item, dict):
            continue

        if item.get("type") != "message":
            continue

        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue

            if content.get("type") not in (
                "output_text",
                "text",
            ):
                continue

            text = content.get("text")

            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    return "\n".join(parts).strip()


def xai_citations(payload):
    citations = []
    seen = set()

    for item in (payload or {}).get("output") or []:
        if not isinstance(item, dict):
            continue

        if item.get("type") != "message":
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

                if not isinstance(url, str) or not url.strip():
                    continue

                url = url.strip()

                if url in seen:
                    continue

                seen.add(url)

                citations.append(
                    Citation(
                        position=len(citations) + 1,
                        url=url,
                        title=str(title)[:2000],
                    )
                )

    return tuple(citations)


class XAIAdapter:
    """Grok through the xAI Responses API."""

    key = "xai"
    source_type = "api"
    adapter_version = "2026.09.1"
    supports_citations = True
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

        payload = call_xai(
            prompt,
            timeout_s=max(1, timeout_s),
            api_key=credential,
        )

        latency_ms = round(
            (time.monotonic() - started) * 1000
        )

        answer_text = xai_answer_text(payload or {})
        citations = xai_citations(payload or {})

        model_version = str(
            (payload or {}).get("model")
            or xai_model()
        )

        raw_response = payload or {}

        if not answer_text:
            return EngineResult(
                status="empty",
                answer_text="",
                citations=citations,
                raw_response=raw_response,
                model_version=model_version,
                cost_usd=Decimal("0"),
                latency_ms=latency_ms,
            )

        return EngineResult(
            status="ok",
            answer_text=answer_text,
            citations=citations,
            raw_response=raw_response,
            model_version=model_version,
            cost_usd=Decimal("0"),
            latency_ms=latency_ms,
        )
