import pytest

from recallvault.config import Settings
from recallvault.models import Candidate, CommitRequest, IngestRequest, Scope, Turn
from recallvault.service import Service
from recallvault.store import Store


@pytest.fixture
def scope():
    return Scope(namespace="team", user_id="alice")


@pytest.fixture
def clock():
    return [1700000000.0]


@pytest.fixture
def store(tmp_path, clock):
    return Store(tmp_path / "memory.db", clock=lambda: clock[0])


@pytest.fixture
def service(tmp_path, store):
    return Service(Settings(database=str(tmp_path / "memory.db")), store=store)


@pytest.fixture
def commit_memory(store, scope):
    def make(text="I prefer concise answers.", key="response.style", expected=None, request="r1", session="s1",
             ttl=None, role="user", user_scope=None):
        owner = user_scope or scope
        store.save_session(IngestRequest(**owner.model_dump(), session_id=session, idempotency_key=session,
                                        turns=[Turn(id="t1", role=role, text=text)]))
        return store.commit(CommitRequest(**owner.model_dump(), session_id=session, idempotency_key=request,
            expected_revision=expected, candidate=Candidate(kind="preference", key=key, text=text,
                source_turn_id="t1", source_quote=text, ttl_seconds=ttl)))
    return make
