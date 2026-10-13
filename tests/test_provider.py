import asyncio
import json

import httpx
import pytest

from recallvault.config import Settings
from recallvault.provider import Provider, ProviderError, json_object


def provider_for(handler, **extra):
    settings = Settings(mode="live", api_key="test-secret", model="test-model", **extra)
    return Provider(settings, httpx.MockTransport(handler))


def response(message=None, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": message or {"role": "assistant", "content": "{}"}}]}


async def test_completion_sends_protocol():
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=response())
    result = await provider_for(handler).completion([{"role": "user", "content": "hello"}])
    assert result["content"] == "{}"
    payload = json.loads(seen[0].content)
    assert payload["model"] == "test-model" and payload["max_tokens"] == 2000
    assert seen[0].url.path == "/v1/chat/completions"


@pytest.mark.parametrize("finish", ["length", "content_filter", None, "unknown"])
async def test_incomplete_completions_rejected(finish):
    with pytest.raises(ProviderError):
        await provider_for(lambda _: httpx.Response(200, json=response(finish=finish))).completion([])


@pytest.mark.parametrize("message", [{"role": "assistant", "content": None},
    {"role": "user", "content": "{}"}, {"role": "assistant", "content": "{}", "refusal": "no"},
    {"role": "assistant", "content": []}])
async def test_invalid_messages_rejected(message):
    with pytest.raises(ProviderError):
        await provider_for(lambda _: httpx.Response(200, json=response(message))).completion([])


async def test_matching_tool_envelope():
    message = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call1", "type": "function", "function": {"name": "recall_memory", "arguments": '{"query":"x"}'}}]}
    result = await provider_for(lambda _: httpx.Response(200, json=response(message, "tool_calls"))).completion([])
    assert result["tool_calls"][0]["id"] == "call1"


async def test_duplicate_tool_ids_rejected():
    call = {"id": "c", "type": "function", "function": {"name": "x", "arguments": "{}"}}
    message = {"role": "assistant", "content": None, "tool_calls": [call, call]}
    with pytest.raises(ProviderError):
        await provider_for(lambda _: httpx.Response(200, json=response(message, "tool_calls"))).completion([])


async def test_transient_http_retry_is_bounded():
    attempts = []
    def handler(_):
        attempts.append(1)
        return httpx.Response(503)
    with pytest.raises(ProviderError, match="HTTP 503"):
        await provider_for(handler).completion([])
    assert len(attempts) == 2


async def test_auth_error_sanitized_and_not_retried():
    attempts = []
    def handler(_):
        attempts.append(1)
        return httpx.Response(401, text="SECRET test-secret body")
    with pytest.raises(ProviderError) as error:
        await provider_for(handler).completion([])
    assert "secret" not in str(error.value) and len(attempts) == 1


async def test_redirect_not_followed():
    with pytest.raises(ProviderError, match="HTTP 307"):
        await provider_for(lambda _: httpx.Response(307, headers={"Location": "https://evil.test"})).completion([])


async def test_response_byte_budget():
    with pytest.raises(ProviderError, match="512 KiB"):
        await provider_for(lambda _: httpx.Response(200, content=b"x" * 512001)).completion([])


async def test_slow_drip_response_has_attempt_wall_clock_deadline(monkeypatch):
    monkeypatch.setattr("recallvault.provider.REQUEST_TIMEOUT", 0.03)
    attempts = []
    class SlowDrip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):
                await asyncio.sleep(0.01)
                yield b" "
    def handler(_):
        attempts.append(1)
        return httpx.Response(200, stream=SlowDrip())
    with pytest.raises(ProviderError, match="failed"):
        await asyncio.wait_for(provider_for(handler).completion([]), timeout=2)
    assert len(attempts) == 2


async def test_invalid_json_response():
    with pytest.raises(ProviderError, match="invalid JSON"):
        await provider_for(lambda _: httpx.Response(200, content=b"<html>oops")).completion([])


async def test_embedding_protocol_and_normalization():
    requests = []
    def handler(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json={"model": "embed-v1", "data": [{"index": 0, "embedding": [3, 4]}]})
    result = await provider_for(handler, embedding_model="embed-v1", embedding_dimensions=2).embedding("text")
    assert result == [.6, .8]
    assert requests[0]["dimensions"] == 2 and requests[0]["encoding_format"] == "float"


@pytest.mark.parametrize("body", [
    {"model": "wrong", "data": [{"index": 0, "embedding": [1, 0]}]},
    {"model": "embed-v1", "data": [{"index": 1, "embedding": [1, 0]}]},
    {"model": "embed-v1", "data": [{"index": 0, "embedding": [1]}]},
    {"model": "embed-v1", "data": [{"index": 0, "embedding": [0, 0]}]},
    {"model": "embed-v1", "data": []},
])
async def test_embedding_protocol_errors(body):
    with pytest.raises(ProviderError):
        await provider_for(lambda _: httpx.Response(200, json=body),
                           embedding_model="embed-v1", embedding_dimensions=2).embedding("text")


@pytest.mark.parametrize("raw", ["[]", "```json\n{}\n```", "null", "garbage"])
def test_strict_json_object(raw):
    with pytest.raises(ProviderError):
        json_object(raw)
