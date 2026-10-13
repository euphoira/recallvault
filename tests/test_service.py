import asyncio
import json

import httpx
import pytest

from recallvault.config import Settings
from recallvault.evals import evaluate
from recallvault.models import Answer, AskRequest, Candidate, CommitRequest, IngestRequest, RecallRequest, Turn
from recallvault.provider import Provider, ProviderError
from recallvault.service import Service, extract_demo


async def test_demo_seed_repeated_and_forgotten_not_resurrected(service, scope):
    assert (await service.seed(scope))["created"] == 3
    for memory in service.store.list(scope):
        await service.forget(scope, memory["id"])
    assert (await service.seed(scope))["created"] == 0
    assert not service.store.list(scope)


async def test_seed_preserves_existing_custom_key(service, scope, commit_memory):
    custom = commit_memory(text="I prefer Chinese answers.")["memory"]
    assert (await service.seed(scope))["created"] == 2
    assert service.store.get(scope, custom["id"])["text"] == "I prefer Chinese answers."


async def test_seed_does_not_restore_key_forgotten_before_first_seed(service, scope, commit_memory):
    custom = commit_memory()["memory"]
    await service.forget(scope, custom["id"])
    assert (await service.seed(scope))["created"] == 2
    assert not any(m["key"] == "response.style" for m in service.store.list(scope))


@pytest.mark.parametrize("text,key", [("I prefer concise answers.", "response.style"),
    ("My timezone is Asia/Shanghai.", "profile.timezone"), ("I am preparing for interviews.", "activity.preparation"),
    ("我喜欢中文回答。", "response.style"), ("我的时区是上海。", "profile.timezone"),
    ("我正在准备面试。", "activity.preparation")])
def test_grounded_demo_patterns(text, key):
    result = extract_demo([Turn(id="1", role="user", text=text)])
    assert result[0].text == text and result[0].source_quote == text and result[0].key == key


def test_demo_does_not_invent_from_unknown_or_assistant():
    assert extract_demo([Turn(id="1", role="user", text="what about the weather?")]) == []
    assert extract_demo([Turn(id="1", role="assistant", text="I prefer concise answers.")]) == []


async def test_demo_ask_cites_current_source(service, scope):
    await service.seed(scope)
    result = await service.ask(AskRequest(**scope.model_dump(), question="concise answers"))
    assert result["mode"] == "demo" and result["tool_calls"] == 1
    assert result["citations"][0]["quote"] == "I prefer concise answers."


async def test_natural_preference_question_does_not_recall_interview_from_pronoun(service, scope):
    await service.seed(scope)
    result = await service.ask(AskRequest(**scope.model_dump(), question="What answers do I prefer?"))
    assert len(result["citations"]) == 1
    assert result["answer"] == "I prefer concise answers."


async def test_unknown_question_abstains(service, scope):
    result = await service.ask(AskRequest(**scope.model_dump(), question="unknown"))
    assert result["citations"] == [] and "do not have" in result["answer"]


@pytest.mark.parametrize("kind", ["unseen", "revision", "quote", "uncited"])
def test_forged_answer_rejected(service, scope, commit_memory, kind):
    memory = commit_memory()["memory"]
    citations = [{"memory_id": memory["id"], "revision": 1, "quote": memory["text"]}]
    seen = {memory["id"]: memory}
    if kind == "unseen":
        seen = {}
    elif kind == "revision":
        citations[0]["revision"] = 99
    elif kind == "quote":
        citations[0]["quote"] = "Invented answer"
    else:
        citations = []
    with pytest.raises(ValueError):
        service.verify_answer(scope, Answer(answer="answer", citations=citations), seen)


async def test_forget_clears_ask_citation_trace(service, scope):
    await service.seed(scope)
    result = await service.ask(AskRequest(**scope.model_dump(), question="concise"))
    await service.forget(scope, result["citations"][0]["memory_id"])
    assert all(t["query"] is None and t["details"] == {} for t in service.store.traces(scope))


async def test_live_function_loop_tool_ids_and_citations(tmp_path, store, scope, commit_memory):
    memory = commit_memory()["memory"]
    requests = []
    def handler(req):
        payload = json.loads(req.content)
        requests.append(payload)
        if len(requests) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "recall-1", "type": "function",
                "function": {"name": "recall_memory", "arguments": '{"query":"concise"}'}}]}
            finish = "tool_calls"
        else:
            assert payload["messages"][-1]["tool_call_id"] == "recall-1"
            output = json.loads(payload["messages"][-1]["content"])
            assert output["hits"][0]["memory"]["id"] == memory["id"]
            message = {"role": "assistant", "content": json.dumps({"answer": memory["text"], "citations": [
                {"memory_id": memory["id"], "revision": 1, "quote": memory["text"]}]})}
            finish = "stop"
        return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": message}]})
    settings = Settings(database=str(tmp_path / "memory.db"), mode="live", model="test", api_key="test")
    service = Service(settings, store, Provider(settings, httpx.MockTransport(handler)))
    result = await service.ask(AskRequest(**scope.model_dump(), question="answer preference"))
    assert result["mode"] == "live" and result["tool_calls"] == 1 and len(requests) == 2


@pytest.mark.parametrize("name,args", [("erase_all", {}), ("get_memory", {"memory_id": "unseen"}),
    ("recall_memory", {"query": "x", "user_id": "someone_else"})])
async def test_model_cannot_change_scope_or_use_unexposed_tools(tmp_path, store, scope, name, args):
    def handler(_):
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}}]})
    settings = Settings(database=str(tmp_path / "memory.db"), mode="live", model="test", api_key="test")
    service = Service(settings, store, Provider(settings, httpx.MockTransport(handler)))
    with pytest.raises(ValueError):
        await service.ask(AskRequest(**scope.model_dump(), question="x"))


async def test_live_cannot_skip_retrieval(tmp_path, store, scope):
    class Fake:
        async def completion(self, *_):
            return {"role": "assistant", "content": '{"answer":"made up", "citations":[]}'}
    settings = Settings(database=str(tmp_path / "memory.db"), mode="live", model="test", api_key="test")
    with pytest.raises(ProviderError, match="must recall"):
        await Service(settings, store, Fake()).ask(AskRequest(**scope.model_dump(), question="x"))


async def test_whole_operation_timeout(tmp_path, store, scope):
    class Slow:
        async def completion(self, *_):
            await asyncio.sleep(2)
    settings = Settings(database=str(tmp_path / "memory.db"), mode="live", model="test", api_key="test", total_timeout=1)
    with pytest.raises(TimeoutError):
        await Service(settings, store, Slow()).ask(AskRequest(**scope.model_dump(), question="x"))


@pytest.mark.parametrize("operation", ["ingest", "recall", "ask", "commit", "forget", "seed"])
async def test_operation_budget_includes_waiting_for_lock(tmp_path, store, scope, operation):
    settings = Settings(database=str(tmp_path / "memory.db"), total_timeout=1)
    service = Service(settings, store)
    base = scope.model_dump()
    requests = {
        "ingest": lambda: service.ingest(IngestRequest(**base, session_id="s", idempotency_key="r",
            turns=[Turn(id="1", role="user", text="I prefer concise answers.")])),
        "recall": lambda: service.recall(RecallRequest(**base, query="concise")),
        "ask": lambda: service.ask(AskRequest(**base, question="concise")),
        "commit": lambda: service.commit(CommitRequest(**base, session_id="s", idempotency_key="r",
            candidate=Candidate(kind="preference", key="response.style", text="concise", source_turn_id="1",
                                source_quote="concise"))),
        "forget": lambda: service.forget(scope, "missing"),
        "seed": lambda: service.seed(scope),
    }
    async with service.lock:
        task = asyncio.create_task(requests[operation]())
        try:
            await asyncio.sleep(1.1)
            assert task.done(), "Operation deadline must expire while still waiting for the lock"
            with pytest.raises(TimeoutError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert store.summary(scope)["sessions"] == 0


async def test_live_extraction_validates_source_before_persist(tmp_path, store, scope):
    class Fake:
        async def completion(self, *_):
            return {"content": json.dumps({"candidates": [{"kind": "fact", "key": "fake", "text": "fake",
                "source_turn_id": "1", "source_quote": "not in source"}]})}
    settings = Settings(database=str(tmp_path / "memory.db"), mode="live", model="test", api_key="test")
    req = IngestRequest(**scope.model_dump(), session_id="s", idempotency_key="i", turns=[
        Turn(id="1", role="user", text="I prefer concise answers.")])
    with pytest.raises(ValueError):
        await Service(settings, store, Fake()).ingest(req)
    assert store.summary(scope)["sessions"] == 0


async def test_semantic_real_adapter_cache_and_fusion(tmp_path, store, scope, commit_memory):
    commit_memory()
    class Embeddings:
        calls = 0
        async def embedding(self, _):
            self.calls += 1
            return [1., 0.]
    settings = Settings(database=str(tmp_path / "memory.db"), api_key="test",
                        embedding_model="embed-v1", embedding_dimensions=2)
    provider = Embeddings()
    service = Service(settings, store, provider)
    first = await service.recall(RecallRequest(**scope.model_dump(), query="concise"))
    second = await service.recall(RecallRequest(**scope.model_dump(), query="short"))
    assert first["strategy"] == "rrf" and first["hits"][0]["channels"] == ["bm25", "semantic"]
    assert second["hits"][0]["channels"] == ["semantic"] and provider.calls == 3


async def test_expiry_during_embedding_not_returned(tmp_path, store, scope, clock, commit_memory):
    commit_memory(ttl=1)
    class Embeddings:
        async def embedding(self, _):
            clock[0] += 2
            return [1., 0.]
    settings = Settings(database=str(tmp_path / "memory.db"), api_key="test",
                        embedding_model="embed", embedding_dimensions=2)
    result = await Service(settings, store, Embeddings()).recall(RecallRequest(**scope.model_dump(), query="concise"))
    assert result["hits"] == []


async def test_forget_waits_for_inflight_recall_and_scrubs_its_results(tmp_path, store, scope, commit_memory):
    memory = commit_memory()["memory"]
    started, release = asyncio.Event(), asyncio.Event()
    class Embeddings:
        async def embedding(self, _):
            started.set()
            await release.wait()
            return [1., 0.]
    settings = Settings(database=str(tmp_path / "memory.db"), api_key="test",
                        embedding_model="embed", embedding_dimensions=2)
    service = Service(settings, store, Embeddings())
    recall = asyncio.create_task(service.recall(RecallRequest(**scope.model_dump(), query="concise answers")))
    await started.wait()
    forget = asyncio.create_task(service.forget(scope, memory["id"]))
    await asyncio.sleep(0)
    assert not forget.done()
    release.set()
    await recall
    await forget
    assert all(trace["query"] is None and trace["details"] == {} for trace in store.traces(scope))
    assert store.vector(memory, "embed", 2) is None
    assert not store.list(scope)


@pytest.mark.parametrize("mutation", ["expire", "correct", "forget"])
def test_final_citation_rechecks_current_state(service, store, scope, clock, commit_memory, mutation):
    memory = commit_memory(ttl=1)["memory"]
    if mutation == "expire":
        clock[0] += 2
    elif mutation == "correct":
        commit_memory(text="I prefer detailed answers.", session="s2", request="r2", expected=1)
    else:
        store.forget(scope, memory["id"])
    answer = Answer(answer=memory["text"], citations=[{"memory_id": memory["id"], "revision": 1,
                                                    "quote": memory["text"]}])
    with pytest.raises(ValueError):
        service.verify_answer(scope, answer, {memory["id"]: memory})


async def test_tool_loop_call_budget_is_enforced(tmp_path, store, scope):
    class Infinite:
        calls = 0
        async def completion(self, *_):
            self.calls += 1
            return {"role": "assistant", "content": None, "tool_calls": [{"id": str(self.calls),
                "type": "function", "function": {"name": "recall_memory", "arguments": '{"query":"x"}'}}]}
    settings = Settings(database=str(tmp_path / "memory.db"), mode="live", model="test", api_key="test", max_tool_calls=2)
    provider = Infinite()
    with pytest.raises(ProviderError, match="budget"):
        await Service(settings, store, provider).ask(AskRequest(**scope.model_dump(), question="x"))
    assert provider.calls == 3


async def test_offline_evaluation_fixtures():
    result = await evaluate()
    assert result["passed"] == result["total"] == 10
