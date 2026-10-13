# RecallVault API contract (v1)

Local server http://127.0.0.1:8013. JSON everywhere. Scope is `{namespace: "demo", user_id: "alice"}` and is REQUIRED for all private operations. Browser sends same-origin fetch. All JSON errors use `{detail: string | array}`. POST bodies up to 256 KiB.

- `GET /api/health` -> `{status, mode: "demo"|"live", embeddings: bool}`.
- `GET /api/summary?namespace=demo&user_id=alice` -> `{active, expired, superseded, forgotten, sessions, mode}`.
- `GET /api/memories?namespace=demo&user_id=alice&include_inactive=true` -> `{memories: [{id, namespace, user_id, kind, key, text, revision, status, expires_at, created_at, source_quote, source_turn_id, session_id}]}`. Forgotten rows expose metadata but text/source_quote=null.
- `GET /api/memories/{id}?namespace=demo&user_id=alice` -> same memory (404 for forgotten/wrong scope).
- `POST /api/ingest` body `{namespace,user_id,session_id,turns:[{id,role:"user"|"assistant",text}],idempotency_key}` -> `{session_id,candidates:[{kind:"fact"|"preference"|"episode",key,text,source_turn_id,source_quote,ttl_seconds:null|integer}],mode}`. Stores bounded conversation for extraction, DOES NOT commit memories; candidates are editable and source validated on commit.
- `POST /api/memories` body `{namespace,user_id,session_id,candidate:{kind,key,text,source_turn_id,source_quote,ttl_seconds},expected_revision:null|integer,idempotency_key}` -> `{memory, duplicate:bool}`. A key existing in active scope requires expected_revision and returns 409 on absent/stale revision; successful correction supersedes previous revision. New key needs null expected_revision. Every citation must match a USER source turn exactly. Root UI may commit each ingest candidate automatically, then expose correction confirmation on 409.
- `POST /api/recall` body `{namespace,user_id,query,limit:5}` -> `{trace_id,hits:[{memory:<memory>,score,channels:["bm25"|"semantic"]}],strategy:"bm25"|"rrf"}`. BM25 default, never fake semantic vectors.
- `POST /api/ask` body `{namespace,user_id,question}` -> `{answer,citations:[{memory_id,revision,quote}],trace_id,mode,tool_calls}`. Demo returns extractive cited response. Live function-call citations must match actually recalled current memories.
- `DELETE /api/memories/{id}?namespace=demo&user_id=alice` -> `{id,status:"forgotten"}`. Wipes scoped key history/source/index/vector/cached contents and trace content, retaining tombstone metadata. Repeating yields same response.
- `POST /api/demo/seed` body `{namespace,user_id}` -> `{created,memories}`. Seeds three memories once per scope, never resurrects forgotten ones. UI can run initially; idempotent.
- `GET /api/traces?namespace=demo&user_id=alice` -> `{traces:[{id,kind,query,created_at,details}]}`. Forget sanitizes traces in scope.

Demo fixtures include the exact user statements `I prefer concise answers.`, `My timezone is Asia/Shanghai.`, `I am preparing for an AI engineer interview.`. Demo extraction also recognizes user sentences beginning `I prefer `, `My timezone is `, `I am preparing for ` and Chinese `我喜欢` / `我的时区是` / `我正在准备`; unrecognized inputs yield no candidates (source form allows manually adding grounded candidate). Candidates never claim that an exact quotation proves the truth of a paraphrase.
