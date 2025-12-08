# Repository Guidelines

# Codex / AI Agent Preferences
- Always respond in Simplified Chinese.
- Write all code comments and explanations in Chinese.
- When the user asks in Chinese, reply directly in Chinese.
- Never use English unless the user explicitly requests it.

## Project Structure & Module Organization
- Core Python services live in `api/` (employee/admin/upload servers), with shared helpers in `db.py` and `util_time.py`.
- Data ingestion and scheduling are split across `etl/` (source loaders), `jobs/` (daily builders), and `scorer/` (model calls and scoring flows). Prompts version in `prompts/`, inputs in `input/`, and generated results in `output/`.
- Desktop clients sit in `ui_client/` (employee) and `admin_ui_client/` (admin); static assets and signing bundles are under `assets/`, `apple-cer/`, and `apple-p12/`.
- Scripts for automation/deployment are in `scripts/`; logs persist under `logs/`; automated checks live in `tests/` with sample data in `input/` and `output/`.

## Build, Test, and Development Commands
- Install deps: `python -m pip install -r requirements.txt` (add `requirements-test.txt` for pytest/UI extras).
- Run servers during local testing: `python api/api_server.py`, `python api/admin_api_server.py`, `python api/upload.py`.
- Full test sweep (includes dependency check, quick tests, pytest, UI basics): `python run_tests.py`.
- Targeted checks: `python tests/quick_test.py` for import/API smoke; `pytest tests -v --html=tests/test_report.html --self-contained-html` for detailed reports.
- Typical dataset lifecycle: `python jobs/build_ai_request.py` → `python scorer/run_daily_score.py` to produce new `output/daily/...` files.

## Coding Style & Naming Conventions
- Python: follow PEP 8 with 4-space indents, `snake_case` modules and functions, and type hints where practical. Keep scripts small and composable; favor pure functions inside `etl/`, `jobs/`, and `scorer/`.
- Tests follow `test_*.py` naming in `tests/`; fixtures and samples should live beside the test or under `tests/data/` if expanded.
- Config files such as `.env` stay untracked; use sample values in docs rather than committing secrets.

## Testing Guidelines
- Preferred workflow: start relevant API servers, set `TEST_API_BASE`, `TEST_ADMIN_API_BASE`, and `TEST_UPLOAD_API_BASE`, then run `python run_tests.py`.
- For coverage: `pytest tests --cov=. --cov-report=html` (HTML output at `htmlcov/index.html`).
- UI tests (`tests/test_ui_interactive.py`) expect a display; they auto-skip in headless environments. Capture failures by attaching `tests/test_report.html` in PRs when debugging.

## Commit & Pull Request Guidelines
- Commit messages should describe scope and intent (e.g., `api: add admin health checks`) rather than generic updates seen in history. Group related changes per commit.
- Before opening a PR: ensure tests pass, note any skipped checks, and summarize impacts to ETL jobs, scoring, or client UX.
- PR descriptions should include: change summary, test commands run, linked issue/OKR, and screenshots for UI-facing updates (employee/admin clients). Mention data migrations or new env vars explicitly.

## Security & Configuration Tips
- Store credentials in local `.env` and platform keychains; never commit keys, tokens, or p12/cert passwords. Rotate tokens used in `apple-p12/` and deployment scripts after sharing.
- Validate outputs before uploading: scrub `logs/` and `output/` for sensitive data before distributing artifacts.
