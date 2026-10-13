import asyncio
import json

import httpx

from .retrieval import validate_vector


REQUEST_TIMEOUT = 15.0


class ProviderError(ValueError):
    pass


class Provider:
    """Bounded Chat Completions and embeddings adapter. No secrets enter error messages."""

    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    async def post(self, path, payload):
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        for attempt in range(2):
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT), httpx.AsyncClient(
                    transport=self.transport, timeout=REQUEST_TIMEOUT, follow_redirects=False
                ) as client:
                    async with client.stream("POST", self.settings.base_url.rstrip("/") + path,
                                             headers=headers, json=payload) as response:
                        if response.status_code in {429, 502, 503, 504} and attempt == 0:
                            await asyncio.sleep(0.1)
                            continue
                        if response.status_code != 200:
                            raise ProviderError(f"Model endpoint returned HTTP {response.status_code}")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 512000:
                                raise ProviderError("Model response exceeded 512 KiB")
                        result = json.loads(data)
                        if not isinstance(result, dict):
                            raise ProviderError("Invalid model response envelope")
                        return result
            except (httpx.HTTPError, json.JSONDecodeError, UnicodeError, TimeoutError) as exc:
                if isinstance(exc, (httpx.TransportError, TimeoutError)) and attempt == 0:
                    await asyncio.sleep(0.1)
                    continue
                raise ProviderError("Model endpoint failed or returned invalid JSON") from None
        raise ProviderError("Model retry budget exhausted")

    async def completion(self, messages, tools=None):
        payload = {"model": self.settings.model, "messages": messages, "temperature": 0,
                   "max_tokens": 2000}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        result = await self.post("/chat/completions", payload)
        choices = result.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderError("Expected one model choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("refusal"):
            raise ProviderError("Model refusal or invalid assistant message")
        calls = message.get("tool_calls") or []
        if message.get("content") is not None and not isinstance(message["content"], str):
            raise ProviderError("Assistant content must be text or null")
        reason = choice.get("finish_reason")
        if reason not in {"stop", "tool_calls"} or (bool(calls) != (reason == "tool_calls")):
            raise ProviderError("Truncated or inconsistent model completion")
        if not isinstance(calls, list) or len(calls) > self.settings.max_tool_calls:
            raise ProviderError("Invalid tool call budget")
        seen = set()
        for call in calls:
            if not isinstance(call, dict) or call.get("type") != "function":
                raise ProviderError("Invalid tool call type")
            call_id = call.get("id")
            function = call.get("function")
            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 100 or call_id in seen:
                raise ProviderError("Invalid or duplicate tool call ID")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ProviderError("Invalid tool function")
            if not isinstance(function.get("arguments"), str) or len(function["arguments"]) > 8000:
                raise ProviderError("Invalid tool arguments")
            seen.add(call_id)
        if not calls and (not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise ProviderError("Missing final model content")
        return {"role": "assistant", "content": message.get("content"), **({"tool_calls": calls} if calls else {})}

    async def embedding(self, text):
        s = self.settings
        result = await self.post("/embeddings", {"model": s.embedding_model, "input": text,
                                                "encoding_format": "float", "dimensions": s.embedding_dimensions})
        data = result.get("data")
        if result.get("model") != s.embedding_model:
            raise ProviderError("Embedding model identity mismatch; configure exact returned model version")
        if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
                or type(data[0].get("index")) is not int or data[0]["index"] != 0):
            raise ProviderError("Invalid embedding response")
        try:
            return validate_vector(data[0].get("embedding"), s.embedding_dimensions)
        except (ValueError, OverflowError) as exc:
            raise ProviderError(str(exc)) from None


def json_object(text):
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ProviderError("Model output must be a JSON object without markdown fences") from None
