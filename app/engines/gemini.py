"""Google Gemini engine adapter.

Uses the Gemini generateContent REST API with the runtime provider credential.
Google Search grounding is requested so returned grounding chunks can be
preserved as citations when available.
"""

import os
import time
from decimal import Decimal

from app.engines.base import Citation, EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request


API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash"


def gemini_model():
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def call_gemini(prompt, *, timeout_s=90, api_key=None):
    api_key = api_key or os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise ProviderAPIError("No Google Gemini provider credential is configured.")

    model = gemini_model()

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt}
                ],
            }
        ],
        "tools": [
            {
                "google_search": {}
            }
        ],
    }

    return external_json_request(
        f"{API_ROOT}/{model}:generateContent",
        method="POST",
        payload=payload,
        headers={
            "x-goog-api-key": api_key,
        },
        timeout=timeout_s,
    )


def gemini_answer_text(payload):
    parts = []

    for candidate in (payload or {}).get("candidates") or []:
        content = candidate.get("content") or {}

        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue

            text = part.get("text")

            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    return "\n".join(parts).strip()


def gemini_citations(payload):
    citations = []
    seen = set()

    for candidate in (payload or {}).get("candidates") or []:
        metadata = candidate.get("groundingMetadata") or {}

        for chunk in metadata.get("groundingChunks") or []:
            if not isinstance(chunk, dict):
                continue

            web = chunk.get("web") or {}

            url = web.get("uri")
            title = web.get("title") or ""

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


def call_gemini_answer(prompt):
    """Backward-compatible grounded Gemini helper used by legacy code/tests."""
    return call_gemini(prompt, timeout_s=60)


def call_gemini_text(system_prompt, user_prompt):
    """Backward-compatible ungrounded Gemini helper for onboarding prompt generation."""
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise ProviderAPIError(
            "GEMINI_API_KEY is not configured."
        )

    payload = {
        "systemInstruction": {
            "parts": [
                {"text": system_prompt}
            ]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": user_prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    response = external_json_request(
        f"{API_ROOT}/{gemini_model()}:generateContent",
        method="POST",
        payload=payload,
        headers={
            "x-goog-api-key": api_key,
        },
        timeout=90,
    )

    return gemini_answer_text(response)


class GeminiAdapter:
    """Google Gemini generateContent with Google Search grounding."""

    key = "google_gemini"
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

        payload = call_gemini(
            prompt,
            timeout_s=max(1, timeout_s),
            api_key=credential,
        )

        latency_ms = round(
            (time.monotonic() - started) * 1000
        )

        answer_text = gemini_answer_text(payload or {})
        citations = gemini_citations(payload or {})

        model_version = str(
            (payload or {}).get("modelVersion")
            or gemini_model()
        )

        if not answer_text:
            return EngineResult(
                status="empty",
                answer_text="",
                citations=citations,
                raw_response=payload or {},
                model_version=model_version,
                cost_usd=Decimal("0"),
                latency_ms=latency_ms,
            )

        return EngineResult(
            status="ok",
            answer_text=answer_text,
            citations=citations,
            raw_response=payload or {},
            model_version=model_version,
            cost_usd=Decimal("0"),
            latency_ms=latency_ms,
        )
