# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Django + React vehicle computer-vision platform.
- `vehicle/` contains `manage.py`, backend dependencies, and Django apps for license plates, vehicle colour, and classification/manufacturer analysis.
- `vehicle/vehicle/` holds settings and root URL configuration; `vehicle/api/` provides shared API functionality.
- Pipeline packages inside each Django app are the API runtime implementations. `services/` preserves standalone pipelines and their documentation; consider both locations when changing shared behaviour.
- `frontend/src/` contains React components and CSS; `frontend/public/` and `frontend/src/assets/` contain web assets.
- Backend tests live in each app's `tests.py`; generated job artifacts go under `vehicle/media/`.

## Build, Test, and Development Commands

From `vehicle/`, use Python 3.11:
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```
These commands install dependencies, prepare the database, start the API on port 8000, validate configuration, and run backend tests.

From `frontend/`:
```bash
npm install
npm run dev
npm run lint
npm run build
npm run preview
```
These install dependencies, start Vite, run ESLint, build production assets, and preview the build.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions/modules, and `PascalCase` classes. Match nearby JavaScript style: two-space indentation, `camelCase` functions/variables, and `PascalCase` React components. ESLint checks JavaScript/JSX, including React Hooks and refresh rules. No repository-wide Python formatter is configured.

## Testing Guidelines

Use Django `TestCase` with `test_*` methods in app `tests.py` files. Follow existing API tests: mock expensive inference calls, use temporary media directories, and verify upload validation and response behaviour. Run a focused suite with `manage.py test license_plate_service`. No coverage threshold or frontend test runner is configured; run lint/build and manually verify affected UI flows.

## Commit & Pull Request Guidelines

The available Git history contains only `wip`, so no established commit convention exists. Use concise, imperative messages describing the change. PRs should explain behaviour, list validation commands/results, link relevant issues, and include screenshots for UI changes.

## Security & Configuration Tips

Keep API keys and camera credentials out of commits. Set `VITE_API_URL` for a nondefault backend. Inference requires service-specific model weights; consult the relevant service README before running pipelines. Avoid committing generated media, local databases, or Python caches.
