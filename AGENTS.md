# Repository Guidelines

## Project Structure & Module Organization

- `vehicle/` contains the Django project, API apps, and runtime computer-vision pipelines. Shared processing and DB1 delivery code lives in `vehicle/common/`.
- `services/` preserves standalone pipeline packages; API runtime copies live inside Django apps. Confirm which copy your change affects.
- `frontend/src/` contains the React/Vite interface, styles, and assets.
- `contracts/` holds schemas and example payloads; `docs/` records architecture and DB1 alignment. `scripts/` provides smoke checks and integration utilities.
- Tests live in Django apps, pipeline `tests/` directories, `vehicle/common/tests/`, and `tests/db1_integration/`. Generated job artifacts go under `vehicle/media/`.

## Build, Test, and Development Commands

Use Python 3.11. From `vehicle/`:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```

These install dependencies, initialize the database, start the API, and run Django validation/tests.

From `frontend/`, run `npm install`, then `npm run dev` for development, `npm run lint` for ESLint, and `npm run build` for a production build. Set `VITE_API_URL` when the API differs from `http://127.0.0.1:8000`.

## Coding Style & Naming Conventions

Match surrounding code: Python uses four-space indentation, `snake_case` functions/modules, and `PascalCase` classes. React uses two-space indentation, `PascalCase` components, and `camelCase` variables. Existing JSX uses double quotes and semicolons. Follow the frontend ESLint configuration. Keep changes focused; avoid unrelated formatting.

## Testing Guidelines

Use Django tests and Python `unittest`; name test modules `test_*.py` or retain existing Django `tests.py` modules. Add regression coverage for changed behavior. No repository-wide coverage threshold is declared.

From the repository root, run `python -m unittest discover -s tests/db1_integration` for DB1 tests. Real PostgreSQL tests are opt-in: follow that directory's README for PostgreSQL 16 fixture configuration. Skipped fixtures do not establish integration success.

## Commit & Pull Request Guidelines

Available Git history contains only `wip`, so no established message convention is evident. Use concise, imperative commit subjects. PRs should describe behavior changes, list validation commands/results, link relevant issues, and include screenshots for UI changes.

## Security & Configuration

Keep API keys and database credentials in private environment variables. Consult `docs/t1/db1_alignment.md` before changing contracts: existing draft schemas do not establish DB1 approval.
