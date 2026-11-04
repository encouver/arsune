# Run Arsune locally

The first build is a local operator tool. The interface is at http://127.0.0.1:3540 and the API documentation is at http://127.0.0.1:8540/api/docs. Model mode stays unavailable until its server settings are valid.

## Docker setup

Use Docker with Compose. From the repository root:

```sh
docker compose -p arsune up --build -d
docker compose -p arsune ps
```

Select **Site with faults** in the interface. This scans the controlled site and adds a labeled false claim. Select **Corrected site** to create a new report after all fixture faults are removed. Both buttons use scripted mode and do not make paid model calls.

Compose binds the interface and service ports to the local machine. It uses separate Arsune database, queue, and evidence volumes. In container reports, the demo address is `http://demo:8541`; the browser on your machine can open the same fixture at http://127.0.0.1:8541/faulty/.

To stop the stack and retain data:

```sh
docker compose -p arsune stop
```

## Development setup

Use Python 3.12 or later, uv, Node 22, npm, and Docker Compose. Do not run the full container stack and the host processes on the same ports at the same time.

From the repository root, install dependencies and start only the data services:

```sh
uv sync --frozen
uv run playwright install --with-deps chromium
npm ci --prefix frontend
docker compose -p arsune up -d postgres redis
uv run alembic upgrade head
```

Run each process below in a separate terminal at the repository root:

```sh
uv run uvicorn app.main:app --host 127.0.0.1 --port 8540
```

```sh
uv run python -m app.demo
```

```sh
uv run python -m app.workers.coordinator
```

```sh
uv run celery -A app.workers.queue:app worker --concurrency=1 --loglevel=INFO
```

```sh
npm --prefix frontend run dev
```

The host setup stores evidence in `.data/evidence`. PostgreSQL listens on port 55432 and Redis on port 56379. The API creates a job and a pending action in one transaction. The coordinator sends pending work to the queue and recovers expired worker claims.

## Model settings

For local model work, create `.env` from `.env.example` if `.env` does not already exist. Set the model endpoint, key, model name, output-token cap, cost ceiling, and input/output token prices. Keep the key in `.env`; this file is excluded from Git. Restart the API and worker after settings change.

NEAR AI uses the base URL `https://cloud-api.near.ai/v1`. Get the model name, supported features, and current token prices from the service before testing. [NEAR AI quickstart](https://docs.near.ai/cloud/quickstart), [model catalog information](https://docs.near.ai/cloud/models)

Model mode runs three separate specialist requests. Each has at most one third of the job cost ceiling. The adapter checks a conservative input-size and output-token cost estimate before each request. Prices must match the selected model; this is a local request budget, not a provider account spending limit.

The worker saves a reservation before a paid request. It saves successful role output for reuse. If a worker stops while a request may have been charged, the job stops instead of making the same request again. A new paid job requires a new operator action. No live model call has been verified yet.

## Checks

With the Arsune PostgreSQL service running and the schema migrated:

```sh
uv run pytest -q
uv run ruff check backend scripts
npm --prefix frontend run build
npm --prefix frontend run typecheck
```

Engine tests start their own controlled site on port 18541. They use actual Chromium for browser checks and a stub transport for model failures and budgets. A stub test does not prove a live model connection.

With the API, demo, coordinator, worker, and interface running:

```sh
cd frontend
npx playwright install chromium
npm run test:e2e
```

For worker recovery, first stop the normal Arsune worker. Keep the API, demo, coordinator, PostgreSQL, and Redis running. From the repository root:

```sh
uv run python scripts/verify_recovery.py
```

This script starts its own worker, stops it after saved observations, and starts a replacement. It checks retained evidence, a higher worker generation, unique findings, and a repeated queue message that completes without changing the report. It then stops only the worker it started. Restart the normal worker after this check. Do not run this test while another Arsune worker can claim the same jobs.

If Chromium is installed in a custom folder, set `PLAYWRIGHT_BROWSERS_PATH` for Python browser tests and workers. Frontend tests can use an existing Chromium executable through `ARSUNE_CHROME_PATH`.
