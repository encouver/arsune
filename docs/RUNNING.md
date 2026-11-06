# Run Arsune locally

The first build is a local operator tool. The interface is at http://127.0.0.1:3540 and the API documentation is at http://127.0.0.1:8540/api/docs. Model mode stays unavailable until its server settings are valid.

The local demo starts at http://127.0.0.1:3540/demo. It links to real website and incident checks, using scripted mode and local examples. No model key is required.

For the website demo, select **Site with faults**, read the confirmed findings and rejected false claim, then select **Compare with corrected demo**. For the incident demo, load the known incident and run the investigation, then repeat with the unrelated timeout control. The control must not support a failed-migration explanation from deployment timing alone. Each run creates a real job and saved report.

## Docker setup

Use Docker with Compose. From the repository root:

```sh
docker compose -p arsune up --build -d
docker compose -p arsune ps
```

Select **Site with faults** in the interface. This scans the controlled site and adds a labeled false claim. Select **Corrected site** to create a new report after all fixture faults are removed. Both buttons use scripted mode and do not make paid model calls.

On a completed report, select **Check again and compare** for a linked scan of the same address. For the controlled faulty site, **Compare with corrected demo** uses the known corrected fixture and records that address mapping. Follow **Before report** to inspect the preserved baseline. The browser address includes the job identifier, so a refresh can reopen the saved report.

Select **Download repair report** to save a readable HTML report with available embedded screenshots. Missing or oversized evidence is identified in the file. Downloading does not publish the report or send it to anyone. JSON remains available for technical use.

Existing installations must run `uv run alembic upgrade head` before starting the new API and worker. Migration `0002` adds the baseline link and comparison mapping. Migration `0003` adds incident jobs and immutable source records. Existing website jobs and history remain in place.

The interface lists recent checks and opens saved reports with `/?job=<id>`. The metadata endpoint is `GET /api/jobs?limit=10`; pass its `next_cursor` as `cursor` to fetch older records. Limits are 1 through 50. This history belongs to the local operator; the application has no customer account separation.

Scripted price checks need no model key. They support named visible price cards with explicit ISO currency and monthly or yearly terms. Check the report's price coverage before using the result: excluded offers are not passing checks. The isolated `/prices/{case}/{page}` demo routes support repeatable price tests.

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

## Incident investigation

Open http://127.0.0.1:3540/incidents. Select **Load synthetic example**, then **Investigate incident**. The example contains a failed migration and matching missing-column errors. The report shows observed facts and a supported explanation. It does not claim that the cause was reproduced or that a service was repaired.

For a supplied bundle, select `events.ndjson` and `deployments.json`. Enter a description, service, and start and end timestamps with explicit UTC offsets, for example `2026-11-05T10:00:00Z`. The time window must be greater than zero and at most 24 hours. The combined files must be at most 10 MB, with at most 5,000 events and 20 deployments. Unknown fields and duplicate IDs are rejected. Blank event records are invalid. An empty deployment array can be used when deployment evidence is missing.

Each event is one JSON object per line. Required fields are `id`, `timestamp`, `service`, `level`, and `message`. Optional fields are `request_id`, `error_code`, `revision`, and `column`. All values are text. Supported levels are `debug`, `info`, `warning`, `error`, and `critical`.

The deployment file is a JSON array. Each record requires `id`, `timestamp`, `service`, `revision`, and `migration_status`. The optional `migration_column` identifies the affected column. Supported migration states are `failed`, `completed`, and `not_required`. The current explanation rule uses the exact event error code `missing_column` and matching revision and column fields. It does not infer a general cause from arbitrary message text.

Use the source buttons in the report to open original records. Event references use physical line numbers; deployment references use record IDs and array positions. The report states selected and excluded record counts and display limits. Recent history opens saved incident reports through `/incidents?job=<id>`.

The API accepts `POST /api/incidents` with six text fields: `description`, `service`, `start`, `end`, `events_text`, and `deployments_text`. Job progress and reports use `GET /api/jobs/{id}`. Source evidence uses `GET /api/incidents/{id}/evidence/{filename}?record_id=<id>`. No source update route is provided.

On a completed incident, **Download incident report** saves a self-contained HTML file. It includes the timeline, result groups, limits, missing evidence, suggested next checks, accepted file hashes, and cited source records. It can be opened offline. Before it returns the file, the export verifies the stored source hashes and runs the scripted checks again on the accepted records. A saved report that differs in anything the file shows returns 409. The time window uses the normalized UTC times. Exact record text is the same in the file and in the source viewer. The export does not include every log record or publish the report.

The export endpoint is `GET /api/incidents/{id}/report`. Unknown jobs and malformed job IDs return 404, website jobs return 422, and unfinished jobs, invalid source evidence, or a saved report that does not match the fresh check return 409. The export limits count escaped HTML bytes. A cited record above 64,000 bytes, or a record beyond the 512,000-byte combined evidence limit, is omitted, and the file states the number of omitted citations. Evidence for supported explanations receives this budget first, then observed facts, rejected explanations, could-not-verify results, and the timeline. The engine limits bound the rest of the file, so a valid completed job exports at less than 2.6 MB. The 4,000,000-byte output limit is only a safeguard.

This flow uses scripted checks only. No API key, production log connection, or paid model call is needed. The fixture bundles in `backend/tests/fixtures/incidents/` cover known, corrected, unrelated, incomplete, invalid, and instruction-like inputs.

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

To stop and resume a comparison job after a baseline recheck has been saved, use:

```sh
uv run python scripts/verify_recovery.py --comparison
```

This script starts its own worker, stops it after saved observations, and starts a replacement. It checks retained evidence, a higher worker generation, unique findings, and a repeated queue message that completes without changing the report. It then stops only the worker it started. Restart the normal worker after this check. Do not run this test while another Arsune worker can claim the same jobs.

For incident recovery, stop the normal worker under the same conditions and run:

```sh
uv run python scripts/verify_incident_recovery.py
```

The first test worker pauses after it commits specialist progress. The script kills that worker and starts a replacement. It checks retained source text and hashes, stable results and stage records, and repeat delivery. The parser, specialists, checker, database, and queue are real. The pause only makes the interruption point repeatable. Restart the normal worker after the check.

If Chromium is installed in a custom folder, set `PLAYWRIGHT_BROWSERS_PATH` for Python browser tests and workers. Frontend tests can use an existing Chromium executable through `ARSUNE_CHROME_PATH`.
