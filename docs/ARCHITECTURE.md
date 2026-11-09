# Arsune architecture

Arsune checks a website for people who build with AI. Specialists propose findings. A separate checker reproduces those findings against the site. The customer receives explanations and evidence. [Product decisions](DECISIONS.md) defines scope; [Rules](../RULES.md) defines limits and acceptance checks.

## Agent roles

| Role | Responsibility | Evidence |
| --- | --- | --- |
| Coordinator | Select at most five pages, assign roles, enforce limits, store progress, and assemble the report | Task and attempt history |
| Navigation specialist | Find failed internal links and destinations | Source link, target URL, response, reproduction steps |
| Mobile specialist | Find horizontal overflow and measurable layout faults | Viewport, element location, measurements, screenshot |
| Consistency specialist | Compare equivalent facts across pages | Both URLs, visible text, matched context |
| Checker | Revisit selected pages and independently test candidates | Fresh observations and confirmation or rejection reasons |

The coordinator is Python code. Specialists share observation and candidate formats. Scripted mode uses deterministic analysis of real browser observations. It must not return a canned report without inspecting the target. A deliberate false candidate is limited to the labeled demonstration case.

Model mode replaces candidate generation through the same interface. Validate model output before checking it. The checker repeats requests or browser measurements; specialist explanations are not proof. Unsupported claim types remain unverified.

## Runtime and technology

Use one repository, one Python backend package, and a Next.js frontend. Run the API, coordinator, and worker as separate processes. The API creates a job and returns its identifier. The UI polls progress and retrieves the report.

## Backend structure

The backend follows the application layout used in Aldea and AprobaDoc. It remains under `backend/` because this repository also contains the frontend.

| Location | Responsibility |
| --- | --- |
| `backend/app/main.py` | Create the FastAPI application and connect middleware and routes |
| `backend/app/core/` | Shared settings, database connection, database models, and scan constants |
| `backend/app/core/schemas/` | API request and response formats |
| `backend/app/v1/api.py` | Connect the API route groups |
| `backend/app/v1/endpoints/` | Receive requests and call services |
| `backend/app/v1/services/` | Create jobs, read reports, and locate evidence |
| `backend/app/agents/` | Generate and validate specialist candidates and call the model service |
| `backend/app/browser/` | Control browser requests, capture observations, and repeat checks |
| `backend/app/incidents/` | Parse incident sources, run scripted specialists, and check claims against original records |
| `backend/app/bounty/` | Check bounty claim scope, read owner proof, run claim checks, and settle claim money |
| `backend/app/workers/` | Run queued jobs, save progress, recover expired claims, and coordinate scan stages |
| `backend/app/demo.py` | Serve the controlled test website |
| `backend/migrations/` | Versioned database changes |
| `backend/tests/` | API, persistence, migration, and browser checks |

Package `__init__.py` files do not contain scan workflows. Database model definitions are separate from connection setup. API request and response formats are separate from route handlers.

The `v1` folder groups the first API implementation. Existing public paths remain `/api/jobs`, `/api/config`, `/api/health`, and the job evidence route. The folder change does not change client URLs, stored records, or the registered queue task name `arsune.scan`.

## Runtime components

| Component | Choice | Purpose |
| --- | --- | --- |
| API | Python, FastAPI, Uvicorn | Jobs, progress, reports |
| API validation | Pydantic | Request and response formats; candidate limits are checked separately in the agent code |
| Worker | Celery and Redis | Browser and agent work outside API requests |
| Database | PostgreSQL, SQLAlchemy, Alembic | Persistent job state and recovery |
| Browser | Playwright with Chromium | Desktop and mobile observations |
| Evidence | Protected local volume for this local prototype | Screenshots and captured observations |
| UI | Next.js, React, TypeScript | URL input, activity, and evidence |
| Model access | HTTPX connection to a configured compatible endpoint | Same candidate format as scripted mode |
| Development | uv, Ruff, pytest, Docker Compose | Reproducible execution and verification |

A protected local evidence volume is sufficient for this local goal. Keep storage paths outside URL input and serve evidence through the API. S3-compatible storage remains a later deployment option.

Playwright supports page screenshots and network inspection. Measure the actual page instead of inferring successful behavior from markup. [Screenshots](https://playwright.dev/python/docs/screenshots), [Network inspection](https://playwright.dev/python/docs/network)

## Stored records and recovery

| Record | Information |
| --- | --- |
| Job | URL, mode, state, timestamps, deadline, worker generation, lease, and saved stage data |
| Observation | URL, final URL, viewport, time, response, visible text, relevant elements, screenshot reference |
| Attempt | Worker generation, state, start time, and finish time |
| Candidate | Stable ID, role, claim type, explanation, URLs, expected behavior, reproduction steps |
| Check result | Candidate ID, status, fresh observations, evidence, and explanation, saved in stage data and the final report |
| Event | Stable event ID, job ID, role, time, and message without secrets |
| Pending action | Stable scan operation ID, job ID, and next publication time |

Saved stage data also records selected pages, specialist outputs, model request reservations, and completed checks. The local operator is the only user; customer ownership and account records are not implemented.

PostgreSQL is authoritative. Redis carries work notifications. Save a job change and its pending action in one database transaction. A coordinator publishes pending actions and reconciles unfinished work after restart.

Use bounded leases and attempt generations. Expired workers cannot overwrite newer results. Save completed stages and stable finding IDs so repeated delivery does not duplicate results. Verify Celery acknowledgement behavior with real worker termination. [Celery task behavior](https://docs.celeryq.dev/en/stable/userguide/tasks.html)

## Network and access boundary

Validate HTTP and HTTPS targets under a configured policy. Reject embedded credentials and disallowed destinations, including redirects to internal services. The controlled demo origin is an explicit local exception, not general permission to inspect private networks. Apply the policy to browser and checker requests.

The first deployment is a local operator tool, not a public scanning service. Keep model credentials on the server. Evidence routes must not expose arbitrary files. Every route that looks up a job ID uses one shared lookup. It accepts only the lowercase UUID form that job creation produces, so other IDs return 404 before a database query. Isolate project containers and volumes from other local projects.

Chromium request interception checks each redirect and resource before a connection to a validated numeric address. The browser retains the final URL, so relative links and resources use the correct base address. Document redirect chains permit the same hostname, its `www` counterpart, and HTTPS upgrades. Each chain has at most three redirects; every new document URL counts toward the five-URL limit.

Public styles, scripts, images, and fonts can load under the saved request and byte limits. Frames, workers, popups, forms, sockets, and data requests remain blocked. Added browser restrictions preserve the site's own content security policy. Redirect history and resource failures are saved with progress. Observations record layout completeness, and the checker does not confirm layout faults when essential resources are missing.

The consistency checker supports matching `data-fact-key` and `data-context` attributes and a limited English visible-price grammar. Ordinary price cards need a plan heading, explicit supported ISO currency, and monthly or yearly period. Product headings, units, and remaining offer terms form the context key; the amount does not. The separate checker reads both pages again and requires unique matching contexts and complete page evidence. Unsupported or ambiguous cards are recorded in price coverage with reasons. This does not provide a general text consistency check.

The local history endpoint selects job metadata only. It orders jobs by creation time and ID, uses a cursor for older results, and limits each response to at most 50 records. The interface reopens the existing job URL and report.

## Job sequence

1. Validate the URL and persist the job.
2. Select at most five page URLs and capture desktop and mobile observations.
3. Run navigation, mobile, and consistency specialists.
4. Validate candidate findings.
5. Have the checker reproduce supported claim types with fresh observations.
6. Store confirmations, rejections, and unresolved cases.
7. Return the report with evidence and completed-check coverage.

An interrupted run resumes stored stages. A rerun after repairs creates a new job and preserves the original evidence.

## Before and after checks

`POST /api/jobs/{job_id}/rerun` creates a new job linked to a completed baseline. Migration `0002` adds `baseline_job_id` and `comparison_mapping` to jobs. Ordinary reruns retain the original target. A separate controlled demo option maps the exact faulty fixture to its corrected counterpart and records that mapping.

The workflow reserves baseline claim pages within the existing five-URL budget. It checks the baseline's confirmed findings again with fresh browser observations and saves each result in `baseline_checks`. A fresh confirmed result remains a problem; a supported fresh disproof resolves it; insufficient evidence produces `could_not_recheck`. Newly confirmed contexts appear as new problems. The comparison service uses claim type, role, URLs, and matched context to identify findings. Candidate absence is never proof of repair.

The baseline report is preserved. Saved rechecks survive worker recovery. Reports keep the ordinary finding fields and add a comparison section, so older report views still work.

`GET /api/jobs/{job_id}/repair-report` returns a downloadable HTML file with escaped text, reproduction steps, and bounded local PNG evidence. It does not publish a report. The interface can reopen a saved job through `/?job={job_id}` and link back to the baseline.

## Incident jobs

`POST /api/incidents` accepts the incident description, service, time window, and the text of `events.ndjson` and `deployments.json`. The endpoint bounds the streamed request body and then applies the combined 10 MB decoded-source limit. It rejects invalid records before creating a job. Migration `0003` adds the job kind, incident input metadata, and the `incident_sources` table. Existing jobs default to website jobs.

The API stores the job, original source texts and SHA-256 hashes, and pending queue action in one transaction. A database trigger rejects updates to accepted sources. Job detail and history do not expose full input files. Source evidence is read through a job-specific endpoint with fixed file names and a record selector. The endpoint verifies the stored hashes before it returns a record.

The existing queue task dispatches incident jobs to a separate workflow. It saves timeline, error, and change stages under the existing worker lease and generation rules. The checker loads the original sources again, verifies hashes, and recomputes counts and joins. Saved source identity includes the incident scope, so a checkpoint cannot be reused for a different bundle or window.

The first causal rule checks missing-column errors against failed migration records with the same service, revision, and column. Timing alone is insufficient. Other errors can appear as observed facts without a causal explanation. Results separate observed facts, supported explanations, rejected explanations, and cases that could not be verified. Logs alone never establish a reproduced cause or a successful repair.

The interface at `/incidents` accepts files, shows progress, and opens source records. Saved incident links use `/incidents?job=<id>`. Website rerun, HTML repair export, and PNG routes do not apply to incident jobs. No browser request, model request, command execution, or URL fetch is permitted by log text.

`GET /api/incidents/{job_id}/report` exports a completed incident as self-contained HTML. It verifies the accepted source hashes, then recomputes the findings and timeline from the accepted records with the scripted engine (`recompute` uses the same proposal, checker, and summary functions as the worker). The file renders only the recomputed values, and the export returns 409 if the saved report differs in any of them. Cited event lines and deployment objects are embedded as exact escaped text with stable internal links; the parser's `deployment_texts` provides the same exact deployment text to the export, the source viewer, and the engine excerpts. The document blocks scripts and external resources, including when opened offline. Record limits count escaped bytes, finding evidence is budgeted before timeline records, and engine limits bound the other parts. Omitted record occurrences are stated explicitly.

The `/demo` page links the controlled website flow and the known-incident and unrelated-timeout inputs. It reuses the existing job APIs and workers, so demo reports come from actual checks.

## Bug bounty

A site owner opens a bounty for a site that the owner controls, then funds it. Independent hunter agents submit claims of functional faults on that site. Each claim has a bond. The existing checker tests each claim again in a fresh browser. Arsune pays only for proven, unique claims. Security testing of any kind is out of scope.

Migration `0004` adds `bounties`, `claims`, and `ledger_entries`. All amounts are integers in the smallest token unit. USDC has 6 decimals.

| Step | Rule |
| --- | --- |
| Create | `POST /api/bounties` with `url`, `owner_account`, `reward_amount`, `bond_amount`, `fee_bps` (default 1000), and `budget`. The bounty starts as `pending_proof` with a random proof token. `budget` is a target and must cover one reward and its fee. The funded `budget_amount` starts at 0. |
| Owner proof | `POST /api/bounties/{id}/verify` reads `{origin}/.well-known/arsune-bounty.txt` under the URL policy: validated and pinned address, 5-second request timeout, 4,096-byte limit, and at most three redirects on the bounty origin. The bounty becomes `open` only when the stripped body equals the token. |
| Fund | `POST /api/bounties/{id}/fund` with `sender`, `amount`, and `signed_intent`, for an open bounty. A pending `fund` row is saved, `collect` runs, then the row gets the transaction ID and the budget increases. |
| Claim | `POST /api/bounties/{id}/claims` accepts `broken_link`, `horizontal_overflow`, or `price_mismatch`. The details keys are exactly `source` and `target`, `url`, or `key`. All URLs must be on the bounty origin and must not have a query string. The claim URLs must be exactly the pages named in the details. The bounty must be open, and its budget must cover one reward and its fee. A hunter can have at most three unresolved claims on one bounty. The bond is collected with memo `arsune:bond:{claim_id}`. Then a `claim` job and its pending action are saved in one transaction. |
| Check | Claim jobs use the existing queue task, lease, generation, and coordinator recovery. The worker calls `check_candidate` with fresh browser observations. Screenshots are kept in the job evidence folder. `confirmed` becomes `proven` or `duplicate`, `rejected` becomes `rejected`, and `unverified` stays `unverified`. A check that cannot finish is `unverified`. |
| Read | `GET /api/bounties`, `GET /api/bounties/{id}` (bounty, budget, claims, ledger), and `GET /api/bounties/{id}/claims/{claim_id}` (evidence links and ledger rows). Malformed and unknown IDs return 404. Claim jobs are not in the job history. |

The fingerprint is the SHA-256 of the claim type, the sorted canonical URLs, and the canonical details. It does not include the hunter. Canonical URLs have a lowercase host, no default port, resolved dot segments, and no fragment. A partial unique index on `(bounty_id, fingerprint)` where `outcome = 'proven'` lets only one claim hold a fingerprint. A later confirmed claim with the same fingerprint becomes `duplicate`. The `outcome` column keeps the check result after `status` changes to `paid` or `needs_review`.

Claim statuses are `received`, `checking`, `proven`, `rejected`, `duplicate`, `unverified`, `paid`, and `needs_review`. A claim goes to `needs_review` when a payment result is unknown, a payment fails, the budget cannot pay a proven claim, or the claim job fails before settlement. Money for a claim in review stays held until an operator decides.

| Outcome | Money |
| --- | --- |
| Proven and unique | The budget decreases by the reward and the fee, and a `fee` row is saved. One `pay(hunter, reward + bond, memo "arsune:reward:{claim_id}")` returns the reward and the bond. The `reward_out` and `bond_refund` rows have the same transaction ID. The claim becomes `paid`. The bounty becomes `closed` when its budget is less than one reward and its fee. |
| Rejected | A `bond_forfeit` row is saved, and the bond is added to the budget. |
| Duplicate or unverified | `pay(hunter, bond, memo "arsune:refund:{claim_id}")` and a `bond_refund` row. |

The worker saves the outgoing payment row without a transaction ID before it calls `pay`. A unique constraint allows one row of each kind for a claim. A repeated attempt that finds a settled claim does nothing. If it finds the payment row without a transaction ID, the payment result is unknown. The claim goes to `needs_review`, and no payment is sent. A `SettlementError` marks the row `failed` and sends the claim to review. There is no automatic retry. A returned transaction ID is saved even if the worker lease expired during the transfer.

`backend/app/settlement/base.py` defines the `Settlement` port with `collect` and `pay`. `app.bounty.settlement.get_settlement()` returns `DrySettlement` (mode `dry`). The dry settlement uses no network and returns IDs like `dry-<hex>`. Each bounty and claim response and each ledger row states the settlement mode. Dry results are not real payments.

Limits: duplicates are found by exact canonical URLs. A site that serves the same page at many paths can accept equal faults with different fingerprints. If the API stops after a bond transfer and before the claim job is saved, the claim stays `received` with a pending `bond_in` row for operator review.

## Verification boundary

The controlled website includes a broken link, mobile overflow, inconsistent facts with matched context, and a healthy page for rejecting a deliberate false claim. Provide a corrected variant and explicit expected outcomes.

Run the actual API, database, queue, worker, browser, and UI. Kill an active worker, restart it, and verify completion with unique result IDs. Mock endpoint tests validate model transport but do not establish a live model connection.

Marketplace payments and production log connections are later goals. A real model connection remains part of this goal and must not be described as live-tested without a real call.
