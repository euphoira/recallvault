import httpx
import pytest

from recallvault.app import create_app


@pytest.fixture
async def client(service):
    app = create_app(service.settings, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8013") as client:
        yield client


async def test_complete_browser_workflow(client):
    scope = {"namespace": "demo", "user_id": "alice"}
    seed = await client.post("/api/demo/seed", json=scope)
    assert seed.status_code == 200 and seed.json()["created"] == 3
    recall = await client.post("/api/recall", json={**scope, "query": "concise"})
    memory = recall.json()["hits"][0]["memory"]
    assert (await client.get(f"/api/memories/{memory['id']}", params=scope)).status_code == 200
    answer = await client.post("/api/ask", json={**scope, "question": "concise"})
    assert answer.json()["citations"]
    assert (await client.get("/api/summary", params=scope)).json()["active"] == 3
    assert (await client.delete(f"/api/memories/{memory['id']}", params=scope)).status_code == 200
    assert (await client.get(f"/api/memories/{memory['id']}", params=scope)).status_code == 404
    assert (await client.post("/api/demo/seed", json=scope)).json()["created"] == 0
    history = await client.get("/api/memories", params={**scope, "include_inactive": "true"})
    assert any(m["status"] == "forgotten" and m["text"] is None for m in history.json()["memories"])


async def test_ingest_commit_correction_conflict(client):
    scope = {"namespace": "demo", "user_id": "alice"}
    await client.post("/api/demo/seed", json=scope)
    req = {**scope, "session_id": "new", "idempotency_key": "new", "turns": [
        {"id": "1", "role": "user", "text": "I prefer detailed answers."}]}
    candidates = (await client.post("/api/ingest", json=req)).json()["candidates"]
    commit = {**scope, "session_id": "new", "idempotency_key": "new", "candidate": candidates[0]}
    assert (await client.post("/api/memories", json=commit)).status_code == 409
    assert (await client.post("/api/memories", json={**commit, "expected_revision": 1})).json()["memory"]["revision"] == 2


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "evil@127.0.0.1", "localhost/evil"])
async def test_host_guard(client, host):
    assert (await client.get("/api/health", headers={"Host": host})).status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:8013"])
async def test_origin_guard(client, origin):
    assert (await client.post("/api/demo/seed", json={"namespace": "n", "user_id": "u"},
                              headers={"Origin": origin})).status_code == 403


async def test_same_origin_allowed_and_security_headers(client):
    result = await client.get("/api/health", headers={"Origin": "http://127.0.0.1:8013"})
    assert result.status_code == 200 and result.json()["mode"] == "demo"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in result.headers["content-security-policy"]


async def test_body_limit(client):
    result = await client.post("/api/ingest", content=b"x" * 262145)
    assert result.status_code == 413


@pytest.mark.parametrize("payload", [{}, {"namespace": "n", "user_id": "u", "query": ""},
    {"namespace": "n", "user_id": "u", "query": "x", "limit": 100},
    {"namespace": "n", "user_id": "u", "query": "x", "admin": True}])
async def test_invalid_request_rejected(client, payload):
    assert (await client.post("/api/recall", json=payload)).status_code == 422


async def test_wrong_scope_cannot_read_delete_memory(client):
    seed = (await client.post("/api/demo/seed", json={"namespace": "n", "user_id": "alice"})).json()
    memory_id = seed["memories"][0]["id"]
    wrong = {"namespace": "n", "user_id": "bob"}
    assert (await client.get(f"/api/memories/{memory_id}", params=wrong)).status_code == 404
    assert (await client.delete(f"/api/memories/{memory_id}", params=wrong)).status_code == 404


async def test_trace_endpoint(client):
    scope = {"namespace": "n", "user_id": "u"}
    await client.post("/api/recall", json={**scope, "query": "hello"})
    assert (await client.get("/api/traces", params=scope)).json()["traces"][0]["query"] == "hello"


async def test_root_and_openapi(client):
    assert (await client.get("/")).status_code == 200
    assert "/api/ask" in (await client.get("/openapi.json")).json()["paths"]
    docs = await client.get("/docs")
    assert docs.status_code == 200 and "RecallVault API reference" in docs.text
    assert "<script" not in docs.text and "https://cdn" not in docs.text


@pytest.mark.parametrize("endpoint", ["/api/summary", "/api/memories", "/api/traces"])
async def test_malformed_scope_query_rejected(client, endpoint):
    assert (await client.get(endpoint, params={"namespace": "../evil", "user_id": "alice"})).status_code == 400
