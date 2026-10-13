import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .models import CommitRequest, IngestRequest, Scope
from .retrieval import validate_vector


class Conflict(ValueError):
    pass


class Missing(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    namespace TEXT, user_id TEXT, id TEXT, turns TEXT NOT NULL,
                    request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at REAL,
                    PRIMARY KEY(namespace,user_id,id), UNIQUE(namespace,user_id,request_key));
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, namespace TEXT NOT NULL, user_id TEXT NOT NULL,
                    kind TEXT NOT NULL, key TEXT NOT NULL, text TEXT, revision INTEGER NOT NULL,
                    status TEXT NOT NULL, expires_at REAL, created_at REAL NOT NULL,
                    source_quote TEXT, source_turn_id TEXT, session_id TEXT, fingerprint TEXT,
                    UNIQUE(namespace,user_id,key,revision));
                CREATE INDEX IF NOT EXISTS scoped_memory ON memories(namespace,user_id,status);
                CREATE TABLE IF NOT EXISTS requests (
                    namespace TEXT, user_id TEXT, request_key TEXT, fingerprint TEXT, memory_id TEXT,
                    PRIMARY KEY(namespace,user_id,request_key));
                CREATE TABLE IF NOT EXISTS memory_sources (
                    memory_id TEXT, session_id TEXT, PRIMARY KEY(memory_id,session_id),
                    FOREIGN KEY(memory_id) REFERENCES memories(id));
                CREATE TABLE IF NOT EXISTS vectors (
                    memory_id TEXT PRIMARY KEY, revision INTEGER, model TEXT, dimensions INTEGER,
                    vector TEXT NOT NULL, FOREIGN KEY(memory_id) REFERENCES memories(id));
                CREATE TABLE IF NOT EXISTS traces (
                    id TEXT PRIMARY KEY, namespace TEXT, user_id TEXT, kind TEXT,
                    query TEXT, details TEXT, created_at REAL);
                CREATE TABLE IF NOT EXISTS seed_flags (
                    namespace TEXT, user_id TEXT, PRIMARY KEY(namespace,user_id));
            """)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def public(self, row):
        result = dict(row)
        result.pop("fingerprint", None)
        if result["status"] == "active" and result["expires_at"] is not None:
            if result["expires_at"] <= self.clock():
                result["status"] = "expired"
        result["created_at"] = iso(result["created_at"])
        result["expires_at"] = iso(result["expires_at"])
        return result

    def save_session(self, req: IngestRequest):
        fingerprint = digest(req.model_dump(exclude={"idempotency_key"}))
        with self.connection(True) as db:
            by_id = db.execute("SELECT * FROM sessions WHERE namespace=? AND user_id=? AND id=?",
                               (req.namespace, req.user_id, req.session_id)).fetchone()
            by_key = db.execute("SELECT * FROM sessions WHERE namespace=? AND user_id=? AND request_key=?",
                                (req.namespace, req.user_id, req.idempotency_key)).fetchone()
            for existing in (by_id, by_key):
                if existing and (existing["id"] != req.session_id
                                 or existing["request_key"] != req.idempotency_key
                                 or existing["fingerprint"] != fingerprint or existing["turns"] == "[]"):
                    raise Conflict("Session or idempotency key already used; use a new session")
            if by_id:
                return
            count = db.execute("SELECT COUNT(*) FROM sessions WHERE namespace=? AND user_id=?",
                               (req.namespace, req.user_id)).fetchone()[0]
            if count >= 2000:
                raise Conflict("Local scope session limit reached (2000)")
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (
                req.namespace, req.user_id, req.session_id,
                canonical([t.model_dump() for t in req.turns]), req.idempotency_key, fingerprint, self.clock()))

    @staticmethod
    def grounded(candidate, turns):
        turn = next((t for t in turns if t["id"] == candidate.source_turn_id and t["role"] == "user"), None)
        if not turn or candidate.source_quote not in turn["text"]:
            raise ValueError("Memory requires an exact quote from a user source turn")

    def commit(self, req: CommitRequest):
        c = req.candidate
        request_hash = digest(req.model_dump(exclude={"idempotency_key"}))
        content_hash = digest(c.model_dump(include={"kind", "key", "text", "ttl_seconds"}))
        with self.connection(True) as db:
            previous = db.execute("SELECT * FROM requests WHERE namespace=? AND user_id=? AND request_key=?",
                                  (req.namespace, req.user_id, req.idempotency_key)).fetchone()
            if previous:
                if previous["fingerprint"] != request_hash:
                    raise Conflict("Idempotency key reused with different content")
                row = db.execute("SELECT * FROM memories WHERE id=?", (previous["memory_id"],)).fetchone()
                if row["status"] == "forgotten":
                    raise Conflict("This request refers to forgotten memory; it cannot be replayed")
                return {"memory": self.public(row), "duplicate": True}
            session = db.execute("SELECT turns FROM sessions WHERE namespace=? AND user_id=? AND id=?",
                                 (req.namespace, req.user_id, req.session_id)).fetchone()
            if not session:
                raise Missing("Source session not found in this scope")
            self.grounded(c, json.loads(session["turns"]))
            old = db.execute("SELECT * FROM memories WHERE namespace=? AND user_id=? AND key=? "
                             "ORDER BY revision DESC LIMIT 1", (req.namespace, req.user_id, c.key)).fetchone()
            if old and old["status"] != "forgotten" and old["fingerprint"] == content_hash:
                if self.public(old)["status"] == "active":
                    db.execute("INSERT INTO requests VALUES (?,?,?,?,?)", (
                        req.namespace, req.user_id, req.idempotency_key, request_hash, old["id"]))
                    db.execute("INSERT OR IGNORE INTO memory_sources VALUES (?,?)", (old["id"], req.session_id))
                    return {"memory": self.public(old), "duplicate": True}
            if old and old["status"] != "forgotten":
                if req.expected_revision != old["revision"]:
                    raise Conflict(f"Correction requires current expected_revision={old['revision']}")
            elif req.expected_revision is not None:
                raise Conflict("No current memory exists for the expected revision")
            count = db.execute("SELECT COUNT(*) FROM memories WHERE namespace=? AND user_id=? "
                               "AND status='active'", (req.namespace, req.user_id)).fetchone()[0]
            if count >= 1000 and (not old or old["status"] != "active"):
                raise Conflict("Local scope active memory limit reached (1000)")
            if old and old["status"] != "forgotten":
                db.execute("UPDATE memories SET status='superseded' WHERE id=?", (old["id"],))
                db.execute("DELETE FROM vectors WHERE memory_id=?", (old["id"],))
            memory_id, now = str(uuid.uuid4()), self.clock()
            expires = now + c.ttl_seconds if c.ttl_seconds is not None else None
            db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                memory_id, req.namespace, req.user_id, c.kind, c.key, c.text,
                old["revision"] + 1 if old else 1, "active", expires, now,
                c.source_quote, c.source_turn_id, req.session_id, content_hash))
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?)", (
                req.namespace, req.user_id, req.idempotency_key, request_hash, memory_id))
            db.execute("INSERT INTO memory_sources VALUES (?,?)", (memory_id, req.session_id))
            row = db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            return {"memory": self.public(row), "duplicate": False}

    def list(self, scope: Scope, include_inactive=False):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM memories WHERE namespace=? AND user_id=? "
                              "ORDER BY created_at DESC,id", (scope.namespace, scope.user_id)).fetchall()
        memories = [self.public(r) for r in rows]
        return memories if include_inactive else [m for m in memories if m["status"] == "active"]

    def get(self, scope: Scope, memory_id, active_only=True):
        with self.connection() as db:
            row = db.execute("SELECT * FROM memories WHERE namespace=? AND user_id=? AND id=?",
                             (scope.namespace, scope.user_id, memory_id)).fetchone()
        if not row or row["status"] == "forgotten":
            raise Missing("Memory not found")
        result = self.public(row)
        if active_only and result["status"] != "active":
            raise Missing("Memory is no longer active")
        return result

    def forget(self, scope: Scope, memory_id):
        with self.connection(True) as db:
            row = db.execute("SELECT * FROM memories WHERE namespace=? AND user_id=? AND id=?",
                             (scope.namespace, scope.user_id, memory_id)).fetchone()
            if not row:
                raise Missing("Memory not found")
            if row["status"] == "forgotten":
                return {"id": memory_id, "status": "forgotten"}
            records = db.execute("SELECT id,session_id FROM memories WHERE namespace=? AND user_id=? AND key=?",
                                 (scope.namespace, scope.user_id, row["key"])).fetchall()
            for record in records:
                db.execute("DELETE FROM vectors WHERE memory_id=?", (record["id"],))
                related = db.execute("SELECT session_id FROM memory_sources WHERE memory_id=?",
                                     (record["id"],)).fetchall()
                for source in related:
                    db.execute("UPDATE sessions SET turns='[]' WHERE namespace=? AND user_id=? AND id=?",
                               (scope.namespace, scope.user_id, source["session_id"]))
                db.execute("DELETE FROM memory_sources WHERE memory_id=?", (record["id"],))
                db.execute("UPDATE sessions SET turns='[]' WHERE namespace=? AND user_id=? AND id=?",
                           (scope.namespace, scope.user_id, record["session_id"]))
            db.execute("UPDATE memories SET status='forgotten',text=NULL,source_quote=NULL,"
                       "source_turn_id=NULL,session_id=NULL,fingerprint=NULL,expires_at=NULL "
                       "WHERE namespace=? AND user_id=? AND key=?", (scope.namespace, scope.user_id, row["key"]))
            db.execute("UPDATE traces SET query=NULL,details='{}' WHERE namespace=? AND user_id=?",
                       (scope.namespace, scope.user_id))
        return {"id": memory_id, "status": "forgotten"}

    def trace(self, scope: Scope, kind, query, details):
        trace_id = str(uuid.uuid4())
        with self.connection(True) as db:
            db.execute("INSERT INTO traces VALUES (?,?,?,?,?,?,?)", (
                trace_id, scope.namespace, scope.user_id, kind, query, canonical(details), self.clock()))
            db.execute("DELETE FROM traces WHERE namespace=? AND user_id=? AND id NOT IN "
                       "(SELECT id FROM traces WHERE namespace=? AND user_id=? ORDER BY created_at DESC LIMIT 200)",
                       (scope.namespace, scope.user_id, scope.namespace, scope.user_id))
        return trace_id

    def traces(self, scope):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM traces WHERE namespace=? AND user_id=? ORDER BY created_at DESC",
                              (scope.namespace, scope.user_id)).fetchall()
        return [{"id": r["id"], "kind": r["kind"], "query": r["query"],
                 "created_at": iso(r["created_at"]), "details": json.loads(r["details"])} for r in rows]

    def summary(self, scope):
        counts = {"active": 0, "expired": 0, "superseded": 0, "forgotten": 0}
        for memory in self.list(scope, True):
            counts[memory["status"]] += 1
        with self.connection() as db:
            counts["sessions"] = db.execute("SELECT COUNT(*) FROM sessions WHERE namespace=? AND user_id=?",
                                            (scope.namespace, scope.user_id)).fetchone()[0]
        return counts

    def claim_seed(self, scope):
        with self.connection(True) as db:
            return db.execute("INSERT OR IGNORE INTO seed_flags VALUES (?,?)",
                              (scope.namespace, scope.user_id)).rowcount == 1

    def vector(self, memory, model, dimensions):
        with self.connection() as db:
            row = db.execute("SELECT * FROM vectors WHERE memory_id=? AND revision=? AND model=? AND dimensions=?",
                             (memory["id"], memory["revision"], model, dimensions)).fetchone()
        return validate_vector(json.loads(row["vector"]), dimensions) if row else None

    def put_vector(self, memory, model, dimensions, vector):
        if not isinstance(model, str) or not model or len(model) > 200:
            raise ValueError("Invalid embedding model identity")
        if type(dimensions) is not int or not 1 <= dimensions <= 4096:
            raise ValueError("Invalid embedding dimensions")
        vector = validate_vector(vector, dimensions)
        with self.connection(True) as db:
            row = db.execute("SELECT status,revision,expires_at FROM memories WHERE id=?", (memory["id"],)).fetchone()
            if row and row["status"] == "active" and row["revision"] == memory["revision"]:
                if row["expires_at"] is None or row["expires_at"] > self.clock():
                    db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)", (
                        memory["id"], memory["revision"], model, dimensions, canonical(vector)))
