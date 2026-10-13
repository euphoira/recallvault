import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database: str = ".recallvault/memory.db"
    mode: str = "demo"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    embedding_model: str = ""
    embedding_dimensions: int = 1536
    max_tool_calls: int = 6
    total_timeout: float = 45.0

    def __post_init__(self):
        if self.mode not in {"demo", "live"}:
            raise ValueError("RECALLVAULT_MODE must be demo or live")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid model base URL")
        if parsed.query or parsed.fragment:
            raise ValueError("Model base URL cannot include query or fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model endpoints require HTTPS")
        if (self.mode == "live" or self.embedding_model) and not self.api_key:
            raise ValueError("Model API key is required for live mode or embeddings")
        if self.mode == "live" and not self.model:
            raise ValueError("Choose a Chat Completions function-calling model explicitly")
        if not 1 <= self.embedding_dimensions <= 4096:
            raise ValueError("Embedding dimensions must be between 1 and 4096")
        if not 1 <= self.max_tool_calls <= 12 or not 1 <= self.total_timeout <= 120:
            raise ValueError("Invalid execution budget")

    @classmethod
    def from_env(cls):
        return cls(
            database=os.getenv("RECALLVAULT_DATABASE", ".recallvault/memory.db"),
            mode=os.getenv("RECALLVAULT_MODE", "demo"),
            api_key=os.getenv("RECALLVAULT_API_KEY", ""),
            base_url=os.getenv("RECALLVAULT_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("RECALLVAULT_MODEL", ""),
            embedding_model=os.getenv("RECALLVAULT_EMBEDDING_MODEL", ""),
            embedding_dimensions=int(os.getenv("RECALLVAULT_EMBEDDING_DIMENSIONS", "1536")),
        )

    def make_parent(self):
        Path(self.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
