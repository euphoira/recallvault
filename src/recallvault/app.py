from pathlib import Path
from html import escape
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models import AskRequest, CommitRequest, IngestRequest, RecallRequest, Scope
from .provider import ProviderError
from .service import Service
from .store import Conflict, Missing


class LocalGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        try:
            parsed = urlsplit("http://" + host)
            allowed = parsed.hostname in {"127.0.0.1", "localhost", "::1"} and not parsed.username
            allowed = allowed and not parsed.path and not parsed.query and not parsed.fragment
        except ValueError:
            allowed = False
        origin = headers.get("origin")
        if not allowed or (origin and origin not in {"http://" + host, "https://" + host}):
            return await JSONResponse({"detail": "Only localhost same-origin requests are allowed"}, 403)(
                scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > 262144:
                return await JSONResponse({"detail": "Request body exceeds 256 KiB"}, 413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        received = False

        async def bounded_receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; "
                     b"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
                ]
            await send(message)
        await self.app(scope, bounded_receive, guarded_send)


def create_app(settings=None, service=None):
    settings = settings or Settings.from_env()
    service = service or Service(settings)
    app = FastAPI(title="RecallVault", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.service = service
    app.add_middleware(LocalGuard)

    @app.exception_handler(Conflict)
    async def conflict(_request: Request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(Missing)
    async def missing(_request: Request, exc):
        return JSONResponse({"detail": str(exc)}, 404)

    @app.exception_handler(ProviderError)
    async def provider_error(_request: Request, exc):
        return JSONResponse({"detail": str(exc)}, 502)

    @app.exception_handler(ValueError)
    async def invalid(_request: Request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(TimeoutError)
    async def timeout(_request: Request, _exc):
        return JSONResponse({"detail": "Total operation time budget exceeded"}, 504)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": settings.mode, "embeddings": bool(settings.embedding_model)}

    @app.get("/api/summary")
    def summary(namespace: str, user_id: str):
        return {**service.store.summary(Scope(namespace=namespace, user_id=user_id)), "mode": settings.mode}

    @app.get("/api/memories")
    def memories(namespace: str, user_id: str, include_inactive: bool = False):
        return {"memories": service.store.list(Scope(namespace=namespace, user_id=user_id), include_inactive)}

    @app.get("/api/memories/{memory_id}")
    def memory(memory_id: str, namespace: str, user_id: str):
        return service.store.get(Scope(namespace=namespace, user_id=user_id), memory_id)

    @app.post("/api/ingest")
    async def ingest(req: IngestRequest):
        return await service.ingest(req)

    @app.post("/api/memories")
    async def commit(req: CommitRequest):
        return await service.commit(req)

    @app.post("/api/recall")
    async def recall(req: RecallRequest):
        return await service.recall(req)

    @app.post("/api/ask")
    async def ask(req: AskRequest):
        return await service.ask(req)

    @app.delete("/api/memories/{memory_id}")
    async def forget(memory_id: str, namespace: str, user_id: str):
        return await service.forget(Scope(namespace=namespace, user_id=user_id), memory_id)

    @app.post("/api/demo/seed")
    async def seed(req: Scope):
        return await service.seed(req)

    @app.get("/api/traces")
    def traces(namespace: str, user_id: str):
        return {"traces": service.store.traces(Scope(namespace=namespace, user_id=user_id))}

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def api_reference():
        sections = []
        for path, methods in app.openapi()["paths"].items():
            for method, operation in methods.items():
                parameters = operation.get("parameters", [])
                details = ", ".join(p["name"] + (" (required)" if p.get("required") else "") for p in parameters)
                sections.append("<article><h2><code>" + escape(method.upper() + " " + path) + "</code></h2>"
                                + "<p>" + escape(operation.get("summary", "")) + "</p>"
                                + ("<p>Parameters: " + escape(details) + "</p>" if details else "")
                                + "<pre>" + escape(json_for_docs(operation.get("requestBody", {}))) + "</pre></article>")
        return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' "
                "content='width=device-width,initial-scale=1'><title>RecallVault API reference</title>"
                "<body><main><h1>RecallVault API reference</h1><p>Local, single-process API. "
                "All private operations require namespace and user_id. Scope filters are not authentication.</p>"
                "<p><a href='/'>Memory workbench</a> · <a href='/openapi.json'>Complete OpenAPI schema</a></p>"
                "<p>POST requests use application/json. Errors use a detail field. 409 indicates a revision or "
                "idempotency conflict; 404 indicates missing scoped/current memory. Maximum body: 256 KiB.</p>"
                + "".join(sections) + "</main></body></html>")

    web = Path(__file__).parent / "web"
    if web.is_dir():
        app.mount("/assets", StaticFiles(directory=str(web)), name="assets")

    @app.get("/")
    def index():
        if (web / "index.html").is_file():
            return FileResponse(web / "index.html")
        return JSONResponse({"name": "RecallVault", "api": "/docs"})

    return app


def json_for_docs(value):
    import json
    return json.dumps(value, indent=2) if value else ""
