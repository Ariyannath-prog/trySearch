"""Key → adapter lookup.

Deliberately just a mapping. The *set of enabled engines* lives in the `engines`
table, and reading it is the caller's job - nothing in this package may import
app.db or app.models, so the registry cannot answer "which engines are on".

Adding an engine is: write a module, register it here, insert a table row. There
is no enum, no PROVIDERS list to keep in sync, and no branch on a provider name.
"""


from app.engines.perplexity import PerplexityAdapter
from app.engines.openai import OpenAIAdapter
from app.engines.gemini import GeminiAdapter
from app.engines.anthropic import AnthropicAdapter
from app.engines.xai import XAIAdapter
from app.engines.deepseek import DeepSeekAdapter
from app.engines.meta import MetaAdapter
from app.engines.openrouter import OpenRouterAdapter

_ADAPTERS = {}


def register(adapter_class):
    _ADAPTERS[adapter_class.key] = adapter_class
    return adapter_class


register(PerplexityAdapter)
register(OpenAIAdapter)
register(GeminiAdapter)
register(AnthropicAdapter)
register(XAIAdapter)
register(DeepSeekAdapter)
register(MetaAdapter)
register(OpenRouterAdapter)


def adapter_for(key):
    """Instantiate the adapter for an engines.key, or None if none is registered.

    None rather than a raise: an engines row can be enabled before its module is
    deployed, and that should skip the engine, not break every other one.
    """
    adapter_class = _ADAPTERS.get(key)
    return adapter_class() if adapter_class else None


def registered_keys():
    return sorted(_ADAPTERS)
