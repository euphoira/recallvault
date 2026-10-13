from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Scope(StrictModel):
    namespace: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    user_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class Turn(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=8000)


class IngestRequest(Scope):
    session_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    turns: list[Turn] = Field(min_length=1, max_length=32)
    idempotency_key: str = Field(min_length=1, max_length=100)

    @field_validator("turns")
    @classmethod
    def unique_turns(cls, turns):
        if len({t.id for t in turns}) != len(turns):
            raise ValueError("Turn IDs must be unique within a session")
        if sum(len(t.text) for t in turns) > 32000:
            raise ValueError("Conversation exceeds 32000 characters")
        return turns


class Candidate(StrictModel):
    kind: Literal["fact", "preference", "episode"]
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    text: str = Field(min_length=1, max_length=2000)
    source_turn_id: str = Field(min_length=1, max_length=80)
    source_quote: str = Field(min_length=1, max_length=2000)
    ttl_seconds: int | None = Field(default=None, ge=1, le=31536000, strict=True)


class CandidateBatch(StrictModel):
    candidates: list[Candidate] = Field(max_length=12)


class CommitRequest(Scope):
    session_id: str = Field(min_length=1, max_length=80)
    candidate: Candidate
    expected_revision: int | None = Field(default=None, ge=1, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=100)


class RecallRequest(Scope):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10, strict=True)


class AskRequest(Scope):
    question: str = Field(min_length=1, max_length=1000)


class Citation(StrictModel):
    memory_id: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=1, strict=True)
    quote: str = Field(min_length=1, max_length=2000)


class Answer(StrictModel):
    answer: str = Field(min_length=1, max_length=12000)
    citations: list[Citation] = Field(max_length=10)


class RecallArgs(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10, strict=True)


class GetArgs(StrictModel):
    memory_id: str = Field(min_length=1, max_length=80)
