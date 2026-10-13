import pytest
from pydantic import ValidationError

from recallvault.config import Settings
from recallvault.models import Candidate, IngestRequest, RecallRequest, Scope, Turn
from recallvault.retrieval import bm25, cosine, fuse, tokenize, validate_vector


def test_bilingual_tokenizer():
    tokens = tokenize("Concise 答案中文")
    assert "concise" in tokens and "中文" in tokens and "答" in tokens


def test_bm25_ranks_specific_match():
    memories = [{"id": "1", "key": "x", "text": "I prefer concise answers"},
                {"id": "2", "key": "y", "text": "I prefer Chinese replies"}]
    assert bm25("concise", memories)[0][0]["id"] == "1"
    assert bm25("missing", memories) == []


def test_empty_index():
    assert bm25("hello", []) == []
    assert bm25("!?", [{"id": "1", "key": "x", "text": "hello"}]) == []


def test_stopword_only_query_does_not_match_every_memory():
    assert bm25("what do I", [{"id": "1", "key": "x", "text": "I am preparing for interviews"}]) == []


def test_rrf_prefers_consensus_and_tracks_channels():
    a, b = {"id": "a"}, {"id": "b"}
    result = fuse([(a, 8), (b, 2)], [(b, .9)], 2)
    assert result[0]["memory"] == b
    assert result[0]["channels"] == ["bm25", "semantic"]


@pytest.mark.parametrize("vector,dimensions", [([1], 2), ([0, 0], 2), ([float("nan"), 1], 2),
    ([float("inf"), 1], 2), ([True, 1], 2), (["1", 1], 2), (None, 2), ([1e308, 1e308], 2)])
def test_bad_embeddings(vector, dimensions):
    with pytest.raises((ValueError, OverflowError)):
        validate_vector(vector, dimensions)


def test_cosine_unit_normalization():
    vector = validate_vector([3, 4], 2)
    assert cosine(vector, vector) == pytest.approx(1)
    with pytest.raises(ValueError):
        cosine([1], [1, 2])


@pytest.mark.parametrize("namespace", ["", "../other", "a/b", "x y", "x" * 65])
def test_invalid_scope(namespace):
    with pytest.raises(ValidationError):
        Scope(namespace=namespace, user_id="alice")


@pytest.mark.parametrize("ttl", [0, -1, 31536001, True, "5"])
def test_invalid_ttl(ttl):
    with pytest.raises(ValidationError):
        Candidate(kind="fact", key="x", text="x", source_turn_id="1", source_quote="x", ttl_seconds=ttl)


def test_duplicate_turn_ids_rejected():
    with pytest.raises(ValidationError):
        IngestRequest(namespace="n", user_id="u", session_id="s", idempotency_key="r",
                      turns=[Turn(id="1", role="user", text="a"), Turn(id="1", role="assistant", text="b")])


def test_total_conversation_bound():
    with pytest.raises(ValidationError):
        IngestRequest(namespace="n", user_id="u", session_id="s", idempotency_key="r",
                      turns=[Turn(id=str(i), role="user", text="a" * 8000) for i in range(5)])


@pytest.mark.parametrize("limit", [0, 11, True, "2"])
def test_recall_budget(limit):
    with pytest.raises(ValidationError):
        RecallRequest(namespace="n", user_id="u", query="q", limit=limit)


@pytest.mark.parametrize("kwargs", [{"mode": "unknown"}, {"mode": "live"},
    {"mode": "live", "api_key": "x"}, {"embedding_model": "embed"},
    {"base_url": "http://untrusted.example/v1"}, {"base_url": "https://x:y@example.com"},
    {"base_url": "https://example.com?key=x"}, {"embedding_dimensions": 0}, {"max_tool_calls": 0}])
def test_settings_boundaries(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)
