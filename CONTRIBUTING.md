## Contributing

This project uses `uv` for environment and dependency management.

### Local Setup

1. Install dependencies:
   - `uv sync`
2. Create local environment file:
   - `copy .env.example .env`
3. Start the app:
   - `uv run messageviewer`

### Required Verification Workflow

Run these commands in this exact order before submitting changes:

1. Format:
   - `uv run black --preview --line-length 140 source tests`
2. Lint:
   - `uv run ruff check --preview --fix source tests`
3. Type check:
   - `uv run mypy source tests`
4. Tests:
   - `uv run pytest`

### Notes

- Keep new endpoint contracts typed with Pydantic models.
- Prefer adding unit tests for utility functions and endpoint smoke tests for API behavior.
- Avoid committing secrets (`.env` stays local).
