import httpx


class OpenAICompatProvider:
    """Minimal async client for any OpenAI-compatible /chat/completions endpoint.

    One adapter covers OpenAI, Kimi (Moonshot), GLM (Zhipu), or a local proxy
    such as Meridian — just change base_url / api_key / model in config.
    Native Claude and Gemini adapters can be added alongside this later.
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def chat(self, messages: list, tools: list = None):
        """Return (message, usage). usage is the provider's token-count dict
        (prompt_tokens / completion_tokens / total_tokens), or {} if absent —
        it feeds the dashboard's usage/cost tracking."""
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"], data.get("usage") or {}
