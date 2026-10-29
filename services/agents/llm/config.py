"""The only place model names and prices live. Agents ask for a tier, never a model.

Prices: USD per 1M tokens, standard tier, prompts up to 272K input tokens, from
https://developers.openai.com/api/docs/pricing (checked 2026-10-29). Update the date when they change.
"""

PROVIDER = "openai"
PRICES_CHECKED = "2026-10-29"

MODELS = {
    "specialist": "gpt-6-luna",  # "most efficient model for focused, high-volume tasks"; specialist explanations
    "strong": "gpt-6.1-sol",  # "complex ... professional work"; planner, judge, debate
}

REASONING_EFFORT = {"specialist": "low", "strong": "medium"}

# model -> (input, cached input, output) USD per 1M tokens. Cache-write tokens are billed as normal input
# (the pricing page lists no separate rate); reasoning tokens are billed as output.
PRICES = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),  # fallback for the judge if Sol proves too costly
}

MAX_TRANSIENT_RETRIES = 3  # rate limits, timeouts, 5xx
MAX_REPAIR_ATTEMPTS = 1  # re-ask once with the validation error when the output does not fit the schema
TIMEOUT_SECONDS = 120


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    """(input - cached) x input price + cached x cached price + output x output price, per 1M tokens."""
    if model not in PRICES:
        raise KeyError(f"no price for model {model!r}; add it to services/agents/llm/config.py")
    p_in, p_cached, p_out = PRICES[model]
    return ((input_tokens - cached_tokens) * p_in + cached_tokens * p_cached + output_tokens * p_out) / 1_000_000
