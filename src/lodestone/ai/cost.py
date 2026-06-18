"""Token -> USD cost estimation for the dashboard's usage tracking.

Cost is computed once, at the moment a call is recorded, and stored on the
ai_usage row. That keeps the dashboard a pure reader: it never needs a price
table, and historical rows keep the price that applied when they were made even
if you later change pricing.

Prices are USD per 1,000,000 tokens, split input/output. Override or extend per
model via config:

    ai:
      pricing:
        gpt-4o-mini: { input: 0.15, output: 0.60 }
        my-proxy-model: { input: 0.0, output: 0.0 }

Token counts on the dashboard are always exact regardless of pricing — only the
cost column depends on these numbers. Unknown models cost 0 until you add them.
"""

# Approximate list prices, overridable via config. Kept short on purpose: it is
# a convenience default, not a maintained price feed.
DEFAULT_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}


def resolve_pricing(model: str, config_pricing: dict = None) -> dict:
    """Merge built-in defaults with config overrides for one model."""
    price = dict(DEFAULT_PRICING.get(model, {}))
    if config_pricing and model in (config_pricing or {}):
        override = config_pricing[model] or {}
        price.update({k: override[k] for k in ("input", "output") if k in override})
    return price


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int,
             config_pricing: dict = None) -> float:
    """Estimated USD cost for one call. Returns 0.0 for unpriced models."""
    price = resolve_pricing(model, config_pricing)
    if not price:
        return 0.0
    inp = float(price.get("input", 0.0)) * (int(prompt_tokens) / 1_000_000)
    out = float(price.get("output", 0.0)) * (int(completion_tokens) / 1_000_000)
    return round(inp + out, 6)
