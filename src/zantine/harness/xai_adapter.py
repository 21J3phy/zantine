"""xAI adapter (Grok 4.7): xAI's OpenAI-compatible Responses API.

xAI recommends its Responses API (``https://api.x.ai/v1/responses``; Chat
Completions is "legacy") and documents the official ``openai`` SDK with a
``base_url`` override, so this reuses ``OpenAIAdapter``'s request shape:
``reasoning={"effort": ...}`` (Grok 4.7 takes low / medium / high / xhigh and
defaults to ``high``, so ``medium`` is sent explicitly), strict function tools
(xAI treats every tool as strict anyway), ``parallel_tool_calls`` off.

Usage differs from OpenAI's: ``output_tokens_details.reasoning_tokens`` is
reported *on top of* ``output_tokens`` (xAI's examples: prompt + completion +
reasoning = total), and reasoning is billed at the output price, so the
normalized output is their sum. Cached tokens are part of ``input_tokens``, as
on OpenAI; xAI charges no cache write. xAI also returns its own charge,
``usage.cost_in_usd_ticks`` (1e10 ticks = $1), kept as ``provider_cost_usd`` on
each call record so our pricing can be checked against the bill.
"""

from __future__ import annotations

from zantine.harness.openai_adapter import OpenAIAdapter
from zantine.harness.provider import register_provider


class XAIAdapter(OpenAIAdapter):
    provider = "xai"
    key_env = "XAI_API_KEY"
    base_url = "https://api.x.ai/v1"
    reasoning_in_output = False


register_provider("xai")(XAIAdapter.from_env)
