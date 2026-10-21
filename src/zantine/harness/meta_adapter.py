"""Meta adapter (Muse Wonder): Meta's OpenAI-compatible Responses API.

Meta's developer API (``https://api.meta.ai/v1``) serves Muse models through a
Responses API, Chat Completions and a Messages API; its quickstart uses the
official ``openai`` SDK with a ``base_url`` override and ``responses.create``.
So this reuses ``OpenAIAdapter``'s request shape: ``reasoning={"effort": ...}``
(Muse Wonder takes minimal / low / medium / high / xhigh / max; ``none`` is a
400), strict function tools, ``tool_choice="auto"`` (the only value Meta
accepts), ``parallel_tool_calls`` off.

Usage is OpenAI's shape: ``input_tokens`` includes
``input_tokens_details.cached_tokens``, and ``output_tokens`` includes
``output_tokens_details.reasoning_tokens`` (billed as completion tokens).
Caching is implicit, with no cache-write charge listed.

The key is read from ``META_API_KEY`` (Meta's own examples call it
``MODEL_API_KEY``; a lab-specific name avoids clashes in a multi-lab run).
"""

from __future__ import annotations

from zantine.harness.openai_adapter import OpenAIAdapter
from zantine.harness.provider import register_provider


class MetaAdapter(OpenAIAdapter):
    provider = "meta"
    key_env = "META_API_KEY"
    # ``reasoning.effort`` on Muse Wonder (``none`` is a 400).
    effort_settings = ("minimal", "low", "medium", "high", "xhigh", "max")
    base_url = "https://api.meta.ai/v1"


register_provider("meta")(MetaAdapter.from_env)
