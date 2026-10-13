import argparse
import asyncio
import json
import sys

from dotenv import load_dotenv

from .app import create_app
from .config import Settings
from .evals import evaluate
from .models import AskRequest, Scope
from .service import Service


async def demo(settings):
    service = Service(settings)
    scope = Scope(namespace="demo", user_id="alice")
    seeded = await service.seed(scope)
    answer = await service.ask(AskRequest(**scope.model_dump(), question="concise answers"))
    return {"mode": settings.mode, "created": seeded["created"], "answer": answer}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser(description="RecallVault local agent memory workbench")
    parser.add_argument("command", choices=["serve", "demo", "eval"])
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    if args.command == "eval":
        result = asyncio.run(evaluate())
        print(json.dumps(result, indent=2, ensure_ascii=False))
        raise SystemExit(0 if result["passed"] == result["total"] else 1)
    settings = Settings.from_env()
    if args.command == "serve":
        import uvicorn
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, workers=1)
    else:
        print(json.dumps(asyncio.run(demo(settings)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
