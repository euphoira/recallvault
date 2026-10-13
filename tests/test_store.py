import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from recallvault.models import Candidate, CommitRequest, IngestRequest, Scope, Turn
from recallvault.store import Conflict, Missing, Store


def test_commit_and_exact_source(store, scope, commit_memory):
    result = commit_memory()
    assert result["memory"]["revision"] == 1
    assert store.get(scope, result["memory"]["id"])["source_quote"] == "I prefer concise answers."


def test_same_request_idempotent(commit_memory, store, scope):
    first, second = commit_memory(), commit_memory()
    assert second["duplicate"]
    assert first["memory"]["id"] == second["memory"]["id"]
    assert len(store.list(scope)) == 1


def test_exact_content_duplicate_new_request(commit_memory):
    first = commit_memory()
    second = commit_memory(request="r2")
    assert second["duplicate"] and first["memory"]["id"] == second["memory"]["id"]


def test_different_idempotent_payload_rejected(commit_memory):
    commit_memory()
    with pytest.raises(Conflict, match="Idempotency"):
        commit_memory(text="I prefer verbose answers.", session="s2", expected=1)


@pytest.mark.parametrize("revision", [None, 2, 42])
def test_correction_requires_current_revision(commit_memory, revision):
    commit_memory()
    with pytest.raises(Conflict, match="expected_revision=1"):
        commit_memory(text="I prefer verbose answers.", session="s2", request="r2", expected=revision)


def test_correction_supersedes_old_revision(commit_memory, store, scope):
    old = commit_memory()["memory"]
    new = commit_memory(text="I prefer detailed answers.", session="s2", request="r2", expected=1)["memory"]
    assert new["revision"] == 2
    assert [m["id"] for m in store.list(scope)] == [new["id"]]
    with pytest.raises(Missing):
        store.get(scope, old["id"])
    assert store.get(scope, old["id"], active_only=False)["status"] == "superseded"


def test_optimistic_race_only_one_writer_wins(commit_memory, store, scope):
    commit_memory()
    def change(index):
        try:
            return commit_memory(text=f"I prefer answer style {index}.", session=f"s{index}",
                                 request=f"r{index}", expected=1)
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [2, 3]))
    assert sum(result is not None for result in results) == 1
    assert len(store.list(scope)) == 1
    assert store.list(scope)[0]["revision"] == 2


@pytest.mark.parametrize("other", [Scope(namespace="other", user_id="alice"), Scope(namespace="team", user_id="bob")])
def test_scope_isolation(commit_memory, store, other):
    memory = commit_memory()["memory"]
    assert store.list(other) == []
    with pytest.raises(Missing):
        store.get(other, memory["id"])
    with pytest.raises(Missing):
        store.forget(other, memory["id"])


def test_ttl_boundary(commit_memory, store, scope, clock):
    memory = commit_memory(ttl=5)["memory"]
    clock[0] += 4.999
    assert store.get(scope, memory["id"])["status"] == "active"
    clock[0] += .001
    assert not store.list(scope)
    assert store.summary(scope)["expired"] == 1
    with pytest.raises(Missing):
        store.get(scope, memory["id"])


def test_expired_duplicate_does_not_silently_extend_ttl(commit_memory, clock):
    commit_memory(ttl=1)
    clock[0] += 2
    with pytest.raises(Conflict):
        commit_memory(ttl=1, request="new")


def test_assistant_statement_cannot_ground_memory(commit_memory):
    with pytest.raises(ValueError, match="user source"):
        commit_memory(role="assistant")


@pytest.mark.parametrize("turn_id,quote", [("t2", "I prefer concise answers."), ("t1", "invented"), ("t1", "CONCISE")])
def test_forged_sources_rejected(store, scope, turn_id, quote):
    store.save_session(IngestRequest(**scope.model_dump(), session_id="s", idempotency_key="s",
                                    turns=[Turn(id="t1", role="user", text="I prefer concise answers.")]))
    with pytest.raises(ValueError, match="exact quote"):
        store.commit(CommitRequest(**scope.model_dump(), session_id="s", idempotency_key="r",
            candidate=Candidate(kind="fact", key="x", text="value", source_turn_id=turn_id, source_quote=quote)))


def test_forgotten_history_vectors_sources_and_traces_removed(commit_memory, store, scope):
    old = commit_memory()["memory"]
    store.put_vector(old, "model", 2, [1, 0])
    new = commit_memory(text="I prefer detailed answers.", session="s2", request="r2", expected=1)["memory"]
    store.put_vector(new, "model", 2, [0, 1])
    store.trace(scope, "ask", "concise answers", {"quote": old["text"]})
    store.forget(scope, new["id"])
    assert not store.list(scope)
    assert all(m["text"] is None and m["source_quote"] is None for m in store.list(scope, True))
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0
        assert all(row[0] == "[]" for row in db.execute("SELECT turns FROM sessions"))
    assert all(t["query"] is None and t["details"] == {} for t in store.traces(scope))
    assert store.forget(scope, new["id"])["status"] == "forgotten"


def test_forget_does_not_change_other_scope(commit_memory, store, scope):
    other = Scope(namespace="team", user_id="bob")
    mine = commit_memory()["memory"]
    theirs = commit_memory(user_scope=other)["memory"]
    store.forget(scope, mine["id"])
    assert store.get(other, theirs["id"])["text"]


def test_forget_clears_deduplicated_source_session_copies(commit_memory, store, scope):
    memory = commit_memory()["memory"]
    duplicate = commit_memory(session="s2", request="r2")
    assert duplicate["duplicate"]
    store.forget(scope, memory["id"])
    with store.connection() as db:
        assert all(row[0] == "[]" for row in db.execute("SELECT turns FROM sessions"))
        assert db.execute("SELECT COUNT(*) FROM memory_sources").fetchone()[0] == 0


def test_forgotten_commit_replay_rejected(commit_memory, store, scope):
    memory = commit_memory()["memory"]
    store.forget(scope, memory["id"])
    with pytest.raises(Conflict):
        commit_memory()


def test_explicit_new_session_can_relearn_forgotten_key(commit_memory, store, scope):
    memory = commit_memory()["memory"]
    store.forget(scope, memory["id"])
    new = commit_memory(text="I prefer Chinese answers.", session="s2", request="r2")["memory"]
    assert new["revision"] == 2 and new["text"] == "I prefer Chinese answers."


def test_repeating_old_forget_does_not_erase_explicit_relearn(commit_memory, store, scope):
    memory = commit_memory()["memory"]
    store.forget(scope, memory["id"])
    new = commit_memory(text="I prefer Chinese answers.", session="s2", request="r2")["memory"]
    store.forget(scope, memory["id"])
    assert store.get(scope, new["id"])["text"] == new["text"]


def test_vector_never_restores_forgotten_snapshot(commit_memory, store, scope):
    memory = commit_memory()["memory"]
    store.forget(scope, memory["id"])
    store.put_vector(memory, "model", 2, [1, 0])
    assert store.vector(memory, "model", 2) is None


def test_vector_cache_checks_model_and_dimension(commit_memory, store):
    memory = commit_memory()["memory"]
    store.put_vector(memory, "v1", 2, [1, 0])
    assert store.vector(memory, "v1", 2) == [1, 0]
    assert store.vector(memory, "v2", 2) is None
    assert store.vector(memory, "v1", 3) is None


@pytest.mark.parametrize("vector,model,dimensions", [([1, 0], "", 2), ([1, 0], "v1", 3),
    ([float("nan"), 0], "v1", 2), ([1, 0], "v1", 5000), ([0, 0], "v1", 2)])
def test_vector_store_validates_independently(commit_memory, store, vector, model, dimensions):
    memory = commit_memory()["memory"]
    with pytest.raises(ValueError):
        store.put_vector(memory, model, dimensions, vector)


def test_corrupt_vector_cache_is_rejected(commit_memory, store):
    memory = commit_memory()["memory"]
    with store.connection(True) as db:
        db.execute("INSERT INTO vectors VALUES (?,?,?,?,?)", (memory["id"], 1, "v1", 2, '["bad",0]'))
    with pytest.raises(ValueError):
        store.vector(memory, "v1", 2)


def test_trace_retention_bounded(store, scope, clock):
    for index in range(210):
        clock[0] += 1
        store.trace(scope, "recall", str(index), {})
    assert len(store.traces(scope)) == 200


def test_database_reopen_persists_data(commit_memory, store, scope):
    memory = commit_memory()["memory"]
    reopened = Store(store.path)
    assert reopened.get(scope, memory["id"])["text"] == memory["text"]


def test_session_idempotency_conflict(store, scope):
    req = IngestRequest(**scope.model_dump(), session_id="s", idempotency_key="a",
                        turns=[Turn(id="1", role="user", text="hello")])
    store.save_session(req)
    req.turns[0].text = "changed"
    with pytest.raises(Conflict):
        store.save_session(req)


@pytest.mark.parametrize("session_id,request_key,text", [("a", "key-b", "Text A"),
    ("b", "key-a", "Text B"), ("a", "new-key", "Text A"), ("new-session", "key-a", "Text A")])
def test_session_and_request_key_collisions_checked_independently(store, scope, session_id, request_key, text):
    for sid, key, source in (("a", "key-a", "Text A"), ("b", "key-b", "Text B")):
        store.save_session(IngestRequest(**scope.model_dump(), session_id=sid, idempotency_key=key,
                                        turns=[Turn(id="1", role="user", text=source)]))
    with pytest.raises(Conflict):
        store.save_session(IngestRequest(**scope.model_dump(), session_id=session_id, idempotency_key=request_key,
                                        turns=[Turn(id="1", role="user", text=text)]))
    assert store.summary(scope)["sessions"] == 2


def test_logical_content_dedupes_different_turns_and_quotes_and_forgets_both_sources(store, scope):
    results = []
    for index, quote in enumerate(["I prefer concise answers.", "concise answers"]):
        sid, tid = f"session-{index}", f"turn-{index}"
        store.save_session(IngestRequest(**scope.model_dump(), session_id=sid, idempotency_key=sid,
            turns=[Turn(id=tid, role="user", text="I prefer concise answers.")]))
        results.append(store.commit(CommitRequest(**scope.model_dump(), session_id=sid, idempotency_key=sid,
            candidate=Candidate(kind="preference", key="response.style", text="I prefer concise answers.",
                                source_turn_id=tid, source_quote=quote))))
    assert results[1]["duplicate"]
    assert results[0]["memory"]["id"] == results[1]["memory"]["id"]
    assert results[1]["memory"]["revision"] == 1
    store.forget(scope, results[0]["memory"]["id"])
    with store.connection() as db:
        assert all(row[0] == "[]" for row in db.execute("SELECT turns FROM sessions"))


def test_deduplication_does_not_extend_existing_ttl(commit_memory, clock):
    original = commit_memory(ttl=10)["memory"]
    clock[0] += 5
    duplicate = commit_memory(ttl=10, session="s2", request="r2")
    assert duplicate["duplicate"]
    assert duplicate["memory"]["expires_at"] == original["expires_at"]


def test_new_ttl_requires_explicit_revision(commit_memory):
    commit_memory(ttl=10)
    with pytest.raises(Conflict):
        commit_memory(ttl=20, session="s2", request="r2")


def test_trace_json_safely_roundtrips(store, scope):
    store.trace(scope, "ask", '"; DROP TABLE memories; --', {"unicode": "中文"})
    assert json.dumps(store.traces(scope), ensure_ascii=False).find("中文") >= 0
