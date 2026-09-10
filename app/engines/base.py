"""The engine adapter contract.

An adapter takes a prompt and returns an EngineResult. It never touches the
database, and it never raises past its own boundary: a provider outage becomes
status='failed' with a message, so one engine going down leaves the run partial
rather than failing it for every other engine.

Nothing in this package may import app.db or app.models. A test enforces that,
because the moment an adapter can write a row it stops being replaceable.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

# What kind of surface the number came from. The UI must say so: Gemini with
# search grounding is a *proxy* for Google AI Overviews, not the thing itself.
SOURCE_TYPES = ('api', 'scraper', 'serp_vendor')

STATUS_OK = 'ok'
STATUS_FAILED = 'failed'
STATUS_EMPTY = 'empty'


@dataclass(frozen=True)
class Citation:
    """One cited source, in the order the provider gave it."""
    position: int
    url: str
    title: str = ''


@dataclass(frozen=True)
class EngineResult:
    """Everything one engine returned for one prompt.

    Frozen: an adapter's output is evidence, and evidence is not edited in place.
    `raw_response` is what gets stored immutably, so extraction can be replayed.
    """
    status: str
    answer_text: str = ''
    citations: tuple = ()
    raw_response: dict = field(default_factory=dict)
    model_version: str = ''
    cost_usd: Decimal = Decimal('0')
    latency_ms: int = 0
    error: str = ''

    @property
    def ok(self):
        return self.status == STATUS_OK

    @classmethod
    def failed(cls, error, *, raw_response=None, latency_ms=0, cost_usd=Decimal('0')):
        return cls(
            status=STATUS_FAILED, error=str(error)[:2000],
            raw_response=raw_response or {}, latency_ms=latency_ms, cost_usd=cost_usd,
        )


@runtime_checkable
class EngineAdapter(Protocol):
    """What every engine must provide.

    Adding an engine is a row in the `engines` table plus a module implementing
    this - never a schema change and never a branch on a provider name.
    """

    key: str
    source_type: str
    adapter_version: str
    supports_citations: bool
    supports_regions: bool

    def run(self, prompt, *, region=None, timeout_s=60) -> EngineResult:
        ...

    def estimate_cost(self, prompt) -> Decimal:
        ...


def guard(fn):
    """Wrap an adapter's run() so nothing escapes its boundary.

    Adapters are written by whoever is integrating a provider, under time
    pressure, against a format that changes without notice. This makes "never
    raises" a property of the interface rather than a rule people remember.
    """
    import functools
    import time

    @functools.wraps(fn)
    def wrapper(self, prompt, *, region=None, timeout_s=60):
        started = time.monotonic()
        try:
            result = fn(self, prompt, region=region, timeout_s=timeout_s)
        except BaseException as error:  # noqa: BLE001 - that is the entire point
            return EngineResult.failed(
                f'{type(error).__name__}: {error}',
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        if not isinstance(result, EngineResult):
            return EngineResult.failed(
                f'{type(self).__name__}.run returned {type(result).__name__}, '
                f'not an EngineResult',
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        return result

    return wrapper
