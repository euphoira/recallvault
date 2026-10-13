# Contributing

Use Python 3.11 or 3.12, create a virtual environment and run `python -m pip install -e '.[dev]'`.

Before submitting a pull request:

```bash
python -m ruff check src tests
python -m pytest -q
recallvault eval
python -m build
```

Changes to write/forget/retrieval behavior should include a boundary-focused test. Use temporary databases and HTTP mocks; tests must not require external credentials or paid API calls. Document migration implications when changing persisted schemas. Keep offline deterministic behavior explicitly distinguished from live model output. Avoid claims about real-world model accuracy without a reproducible benchmark and dataset.

Do not commit `.env`, `.recallvault*`, SQLite databases, traces, private conversations or generated build environments. This project intentionally supports one local process; propose architecture changes explicitly before adding distributed workers or network deployment.
