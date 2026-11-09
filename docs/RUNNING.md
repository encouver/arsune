# Run Arsune locally

The first build is a local operator tool. The interface is at http://127.0.0.1:3540 and the API documentation is at http://127.0.0.1:8540/api/docs. Model mode stays unavailable until its server settings are valid.

The local demo starts at http://127.0.0.1:3540/demo. It links to real website and incident checks, using scripted mode and local examples. No model key is required.

For the website demo, select **Site with faults**, read the confirmed findings and rejected false claim, then select **Compare with corrected demo**. For the incident demo, load the known incident and run the investigation, then repeat with the unrelated timeout control. The control must not support a failed-migration explanation from deployment timing alone. Each run creates a real job and saved report.

## Docker setup

Use Docker with Compose v2. From the repository root:

```sh
docker compose up --build --wait
docker compose ps
```

This starts PostgreSQL, Redis, the migrations, the API, the worker (four claim checks at a time), the coordinator, the demo site, the interface, and the swarm. The command returns when every service is healthy. The swarm then runs 100 hunters and 10 scouts on a 60 USDC bounty on `http://demo:8541/shop/`; watch it at http://127.0.0.1:3540/swarm. Start a fresh race with `docker compose restart swarm`, and read it as text with `docker compose logs -f swarm`.

The containers never read `.env`. Compose always uses dry settlement and scripted checks, so it needs no keys and moves no money. For model mode or `near_intents` settlement, use the host processes in [Development setup](#development-setup).

All ports bind to 127.0.0.1. Override a taken port with `ARSUNE_UI_PORT` (3540), `ARSUNE_API_PORT` (8540), `ARSUNE_DEMO_PORT` (8541), `ARSUNE_SWARM_PORT` (8590), `ARSUNE_POSTGRES_PORT` (55432), or `ARSUNE_REDIS_PORT` (56379). Use a different project name (`-p`) to run a second copy next to the first one.

`scripts/record_demo.sh` records the swarm board at 1440x900 during a fresh race and writes `.data/demo/arsune-swarm.mp4` and a full-page screenshot. It restarts the Compose `swarm` service and needs uv with Playwright Chromium and an ffmpeg with libx264. For another project or port, run for example `COMPOSE_PROJECT_NAME=arsune-judge ARSUNE_UI_PORT=3640 scripts/record_demo.sh`.

Select **Site with faults** in the interface. This scans the controlled site and adds a labeled false claim. Select **Corrected site** to create a new report after all fixture faults are removed. Both buttons use scripted mode and do not make paid model calls.

On a completed report, select **Check again and compare** for a linked scan of the same address. For the controlled faulty site, **Compare with corrected demo** uses the known corrected fixture and records that address mapping. Follow **Before report** to inspect the preserved baseline. The browser address includes the job identifier, so a refresh can reopen the saved report.

Select **Download repair report** to save a readable HTML report with available embedded screenshots. Missing or oversized evidence is identified in the file. Downloading does not publish the report or send it to anyone. JSON remains available for technical use.

Existing installations must run `uv run alembic upgrade head` before starting the new API and worker. Migration `0002` adds the baseline link and comparison mapping. Migration `0003` adds incident jobs and immutable source records. Migration `0004` adds bounties, claims, and the settlement ledger. Existing website jobs and history remain in place.

The interface lists recent checks and opens saved reports with `/?job=<id>`. The metadata endpoint is `GET /api/jobs?limit=10`; pass its `next_cursor` as `cursor` to fetch older records. Limits are 1 through 50. This history belongs to the local operator; the application has no customer account separation.

Scripted price checks need no model key. They support named visible price cards with explicit ISO currency and monthly or yearly terms. Check the report's price coverage before using the result: excluded offers are not passing checks. The isolated `/prices/{case}/{page}` demo routes support repeatable price tests.

Compose uses separate Arsune database, queue, and evidence volumes. In container reports, the demo address is `http://demo:8541`; the browser on your machine can open the same fixture at http://127.0.0.1:8541/faulty/.

To stop the stack and retain data, run `docker compose stop`. To delete the containers and volumes, run `docker compose down -v`.

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

## Bug bounty

The settlement mode is `dry` by default. No money moves, and every response states `"settlement_mode": "dry"`. Amounts are integers in the smallest token unit; 1 USDC is `1000000`. See [Architecture](ARCHITECTURE.md#bug-bounty) for the API and money rules, [Rules](../RULES.md#bug-bounty-market) for the market rules, and [Settlement](SETTLEMENT.md) for `near_intents` mode.

Every transfer into Arsune is a signed NEP-413 intent, also in dry mode. Read `GET /api/settlement` first. It gives the receiver account, token, contract, and salt to sign with. In dry mode, the salt is `00000000`. Pass it to `sign_transfer(..., salt=bytes.fromhex(salt))`. Without an explicit salt, `sign_transfer` reads it from the mainnet RPC.

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.settlement.near_intents import sign_transfer

claim_id = str(uuid4())
bond_intent = sign_transfer(
    "hunter.testnet", "<hunter ed25519 private key>", info["arsune_account"], info["token"],
    bounty["bond_amount"], f"arsune:bond:{claim_id}", datetime.now(UTC) + timedelta(minutes=5),
    salt=bytes.fromhex(info["salt"]), contract=info["intents_contract"],
)
```

Use the same pattern for funding with memo `arsune:fund:{fund_id}`. The steps on the controlled site with the host setup:

1. `POST /api/bounties` with `url` (for example `http://127.0.0.1:8541/faulty/`), `owner_account`, `reward_amount`, `bond_amount`, and `budget`. Keep the returned `id` and `proof_token`.
2. Publish the token: `POST http://127.0.0.1:8541/bounty-proof-control` with `{"token": "<proof_token>"}`. You can also set `ARSUNE_DEMO_BOUNTY_TOKEN` before the demo starts. A real site owner puts the token in `/.well-known/arsune-bounty.txt`. In the Compose setup, the API reads the controlled site at `http://demo:8541`, so use that address in the bounty URL.
3. `POST /api/bounties/<id>/verify`.
4. `POST /api/bounties/<id>/fund` with `fund_id`, `sender`, `amount`, and `signed_intent`.
5. `POST /api/bounties/<id>/claims` with `claim_id`, `hunter_account`, `claim_type`, `urls`, `details`, `explanation`, `reproduction_steps`, `bond_intent`, and an optional `scout` of `{"account", "share_bps", "tip_id"}`.
6. Read `GET /api/bounties/<id>` and `GET /api/bounties/<id>/claims/<claim_id>`.

The claim detail shows the status, the checker reason, screenshot links, and ledger rows with transaction IDs. For `near_intents` rows, `explorer_url` links to nearblocks. A claim in `unfunded` was proven after the budget was spent: its bond is refunded, it gets no reward, and the bounty is closed. A claim on a redirecting URL is a duplicate of a proven claim on the page it reaches. A claim in `needs_review` holds money until an operator checks the settlement account. There is no automatic retry. Error codes: 404 for unknown or malformed IDs; 409 for a bounty that is not ready, is closed, or a reused `claim_id` or `fund_id`; 422 for an invalid request, an out-of-scope claim, or an invalid signed transfer; 429 for too many unresolved claims from one hunter; 402 when a transfer fails; 504 when a transfer result is unknown.

For a race of up to 100 hunter agents, run the worker with `--concurrency=4` on a machine with 8 GB of memory, or `--concurrency=8` with 16 GB. Each browser check uses one Chromium process of about 300 MB for 5 to 15 seconds. Claims for a fault that is already proven finish without a browser, so most of a race settles fast. Use one worker host. Several prefork processes are safe: each has its own database connections, and each claim job has its own lease. Keep `ARSUNE_LEASE_SECONDS` at 30 or more. In `near_intents` mode, payments go through one relayer key and one advisory lock. Expect about 2 to 5 seconds for each transfer.

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

Playwright test screenshots are saved under `frontend/test-results/`, in a per-test output folder.

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

## Swarm board

Open `/swarm` (optionally `?bounty=<id>`) for the live swarm board. It polls the Arsune API and the swarm runner state every 1.8 seconds. The frontend proxies `/swarm-state` to `ARSUNE_SWARM_URL` (default `http://127.0.0.1:8590`, route `/state`). If the runner is down, the board still works from Arsune data. When the settlement mode is `dry`, every money view shows "Simulation — no real money".

## Demo shop

The demo site serves a larger shop, Northwind Supply, under `/shop/` (for example `http://127.0.0.1:8541/shop/`). It has 8 category pages, 25 plan pages and 6 information pages, with 50 seeded faults (20 broken links, 15 mobile overflow pages, 15 price mismatches) and 18 decoys that look like faults but must not be proven. The ground truth is in `backend/app/demo_shop.py` (`GROUND_TRUTH`, `build_ground_truth`); no route serves it. `backend/tests/engine/test_demo_shop.py` checks every fault and decoy with real Chromium.
