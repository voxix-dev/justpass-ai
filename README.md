# Homework Agents

Send a photo of your homework, AI agents do it, you review the result.

Design and decisions: https://claude.ai/artifact/898ZKpME1bCdonGAw38sgy

## Setup
```
uv sync
uv run pre-commit install
uv run pytest
uv run uvicorn api.main:app --reload
```

Rules for contributors and their AI assistants: [AGENTS.md](AGENTS.md).
