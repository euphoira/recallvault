"""Deterministic integration fixtures, not measurements of a hosted model."""

from tempfile import TemporaryDirectory
from pathlib import Path

from .config import Settings
from .models import AskRequest, Candidate, CommitRequest, IngestRequest, RecallRequest, Scope, Turn
from .service import Service
from .store import Conflict, Store


async def evaluate():
    with TemporaryDirectory(prefix="recallvault-eval-") as directory:
        now = [1700000000.0]
        settings = Settings(database=str(Path(directory) / "eval.db"))
        service = Service(settings, store=Store(settings.database, clock=lambda: now[0]))
        scope = Scope(namespace="eval", user_id="alice")
        base = scope.model_dump()
        results = []

        async def record(name, check):
            try:
                await check()
                results.append({"name": name, "passed": True})
            except Exception as exc:
                results.append({"name": name, "passed": False, "error": type(exc).__name__ + ": " + str(exc)})

        async def seed():
            assert (await service.seed(scope))["created"] == 3
        await record("seed_grounded_memories", seed)

        async def cross_session():
            result = await service.ask(AskRequest(**base, question="concise answers"))
            assert result["citations"] and "concise" in result["answer"]
        await record("cross_session_recall", cross_session)

        async def isolation():
            assert not (await service.recall(RecallRequest(namespace="eval", user_id="bob", query="concise")))["hits"]
        await record("user_scope_isolation", isolation)

        async def no_evidence():
            result = await service.ask(AskRequest(**base, question="quantum extraterrestrial"))
            assert not result["citations"] and "do not have" in result["answer"]
        await record("unsupported_question_abstains", no_evidence)

        async def correction():
            req = IngestRequest(**base, session_id="correct", idempotency_key="correct", turns=[
                Turn(id="t1", role="user", text="I prefer detailed answers.")])
            candidate = Candidate.model_validate((await service.ingest(req))["candidates"][0])
            commit = CommitRequest(**base, session_id="correct", candidate=candidate, idempotency_key="correct")
            try:
                await service.commit(commit)
                raise AssertionError("Unversioned correction unexpectedly accepted")
            except Conflict:
                pass
            commit.expected_revision = 1
            result = await service.commit(commit)
            assert result["memory"]["revision"] == 2
            assert not (await service.recall(RecallRequest(**base, query="concise")))["hits"]
        await record("explicit_revision_correction", correction)

        async def idempotent():
            assert (await service.seed(scope))["created"] == 0
            assert len(service.store.list(scope)) == 3
        await record("seed_is_idempotent", idempotent)

        async def expiry():
            req = IngestRequest(**base, session_id="expiry", idempotency_key="expiry", turns=[
                Turn(id="t1", role="user", text="Launch code expires soon.")])
            await service.ingest(req)
            await service.commit(CommitRequest(**base, session_id="expiry", idempotency_key="expiry",
                candidate=Candidate(kind="episode", key="temporary.launch", text="Launch code expires soon.",
                                    source_turn_id="t1", source_quote="Launch code expires soon.", ttl_seconds=1)))
            now[0] += 2
            assert not (await service.recall(RecallRequest(**base, query="Launch code")))["hits"]
        await record("ttl_excludes_expired_memory", expiry)

        async def forgetting():
            memory = next(m for m in service.store.list(scope) if m["key"] == "response.style")
            await service.forget(scope, memory["id"])
            await service.seed(scope)
            assert not (await service.recall(RecallRequest(**base, query="answers")))["hits"]
            assert all(m["text"] is None for m in service.store.list(scope, True) if m["key"] == "response.style")
        await record("forget_history_without_reseed", forgetting)

        async def chinese():
            req = IngestRequest(**base, session_id="zh", idempotency_key="zh", turns=[
                Turn(id="t1", role="user", text="我喜欢使用中文交流。")])
            candidate = Candidate.model_validate((await service.ingest(req))["candidates"][0])
            await service.commit(CommitRequest(**base, session_id="zh", candidate=candidate, idempotency_key="zh"))
            assert (await service.recall(RecallRequest(**base, query="中文")))["hits"]
        await record("chinese_lexical_retrieval", chinese)

        async def forged_source():
            candidate = Candidate(kind="fact", key="fake", text="Invented", source_turn_id="t1", source_quote="Invented")
            try:
                await service.commit(CommitRequest(**base, session_id="zh", candidate=candidate, idempotency_key="fake"))
            except ValueError:
                return
            raise AssertionError("Forged source accepted")
        await record("forged_source_rejected", forged_source)
        return {"mode": "offline-fixture", "passed": sum(r["passed"] for r in results),
                "total": len(results), "results": results}
