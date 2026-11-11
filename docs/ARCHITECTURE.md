# Arsune architecture

Arsune checks a website for people who build with AI. Specialists propose findings. A separate checker reproduces those findings against the site. The customer receives explanations and evidence. [Product decisions](DECISIONS.md) defines scope; [Rules](../RULES.md) defines limits and acceptance checks.

## Agent roles

| Role | Responsibility | Evidence |
| --- | --- | --- |
| Coordinator | Select at most five pages, assign roles, enforce limits, store progress, and assemble the report | Task and attempt history |
| Navigation specialist | Find failed internal links and destinations, broken images, and uncaught page errors | Source link, target URL, response, image request result, error message, reproduction steps |
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

Each observation also records page images and page errors, without new requests. `images` lists at most 60 `<img>` elements in page order: `src` (the URL that the element requested), `final_url` (after image redirects), `status` (the final HTTP status, or `null` when no allowed request finished), `failure` (why Arsune's interception stopped the request, if it did), `decoded` (`true`, `false`, or `null` while loading), and `rendered` (shown to visitors). `images_truncated` is true when the page has more. The read does not scroll, so a lazy image far below the first screen stays unloaded (`decoded: null`). `page_errors` lists at most 20 uncaught errors and unhandled promise rejections from the Chrome DevTools `Runtime.exceptionThrown` event: `message` (the first line, for example `TypeError: Cannot read properties of undefined (reading 'items')`), `kind` (`error` or `rejection`), `source` (the origin of the script that raised it, or empty), and `first_party` (the script is on the scanned site, its `www` counterpart, or its HTTPS upgrade). A rejection that gets a handler later (`Runtime.exceptionRevoked`) is removed. `page_errors_truncated` is true when more occurred. A resource issue caused by the site's own HTTP error answer has `http_status`; other issues mean that Arsune's limits changed what loaded. One exception: a data request that a script from another site sends to another site, such as an analytics or advertising beacon, is blocked like every data request but has `third_party_data: true` and does not count as a limit, because it serves the other site and not the page. `layout_complete` is false after any document, style, script, image, font, readiness, or policy issue, except third-party data and an image that the server answered with an HTTP error.

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

A site owner opens a bounty for a site that the owner controls, then funds it. Independent hunter agents submit claims of functional faults on that site. Each claim has a bond. Scout agents can sell tips to hunters for a share of the reward. The existing checker tests each claim again in a fresh browser. Arsune pays only for proven, unique claims. Security testing of any kind is out of scope. [Rules](../RULES.md#bug-bounty-market) states the market rules.

Migrations `0004` and `0005` add `bounties`, `claims` (with an optional `scout`), and `ledger_entries`. All amounts are integers in the smallest token unit. USDC has 6 decimals.

| Step | Rule |
| --- | --- |
| Settlement | `GET /api/settlement` returns `mode`, `arsune_account`, `token`, `intents_contract`, and `salt`. Clients sign NEP-413 transfers to `arsune_account` with these values. In dry mode without `ARSUNE_NEAR_ACCOUNT_ID`, the account is `arsune.dry` and the salt is `00000000`. In `near_intents` mode, the salt is the live Verifier `current_salt`, kept for 5 minutes. |
| Create | `POST /api/bounties` with `url`, `owner_account`, `reward_amount`, `bond_amount`, `fee_bps` (default 1000), and `budget`. The bounty starts as `pending_proof` with a random proof token. `budget` is a target and must cover one reward and its fee. In `near_intents` mode, the reward plus the bond must not exceed `ARSUNE_MAX_PAYMENT_AMOUNT`. |
| Owner proof | `POST /api/bounties/{id}/verify` reads `{origin}/.well-known/arsune-bounty.txt` under the URL policy: validated and pinned address, 5-second request timeout, 4,096-byte limit, and at most three redirects on the bounty origin. The bounty becomes `open` only when the stripped body equals the token. |
| Fund | `POST /api/bounties/{id}/fund` with `fund_id` (lowercase UUID v4 from the funder), `sender`, `amount`, and `signed_intent`, for an open bounty. The intent must transfer exactly `amount` from `sender` to `arsune_account` with memo `arsune:fund:{fund_id}`. A closed bounty takes funds only when `closed_reason` is `budget_spent`; the top-up opens it again when the budget pays one reward and its fee (`app.bounty.budget`). In `near_intents` mode an amount above `ARSUNE_MAX_PAYMENT_AMOUNT` gets 422 before any row or transfer. An open bounty whose budget cannot pay one reward and its fee (after the proof, before enough funding) rejects claims with 409. |
| Claim | `POST /api/bounties/{id}/claims` with `claim_id` (lowercase UUID v4 from the hunter), `hunter_account`, `claim_type`, `urls`, `details`, `explanation`, `reproduction_steps`, `bond_intent`, and an optional `scout` (`account`, `share_bps` 1 to 5000, `tip_id` of at most 64 characters). The bond intent must transfer exactly the hunter's required bond (see reputation below) from the hunter to `arsune_account` with memo `arsune:bond:{claim_id}`. Scope rules are unchanged: supported claim types, exact detail keys, URLs on the bounty origin without a query string, and at most three unresolved claims for each hunter on a bounty. |
| Check | Claim jobs use the existing queue task, lease, generation, and coordinator recovery. The job deadline starts when a worker first takes the job, not when the claim is queued. The worker calls `check_candidate` with fresh browser observations. A claim whose fingerprint is already proven becomes `duplicate` without a browser, at arrival or when the job starts. |
| Bond | `GET /api/bounties/{id}/bond?hunter=<account>` returns `hunter`, `base_bond`, `required_bond`, `multiplier`, `tier`, and `reputation` (`score`, `proven`, `rejected`, `duplicate`, `unverified`, `unfunded`). `GET /api/hunters/{account}` returns `account`, `tier`, `multiplier`, and the same `reputation` over all bounties. An account without claims is `new`. An invalid account returns 422. |
| Read | `GET /api/bounties`, `GET /api/bounties/{id}`, and `GET /api/bounties/{id}/claims/{claim_id}`. The bounty view has `settlement_mode`, claims with `scout`, ledger rows with `explorer_url`, and `hunters`. Each `hunters` row also has `reputation_score`, `tier`, and `required_bond` for this bounty. |
| Evidence | A decided claim has `evidence_hash` and `evidence_url`. `GET /api/claims/{claim_id}` is the claim view without the bounty ID. `GET /api/claims/{claim_id}/evidence` returns the stored evidence bundle bytes (canonical JSON, header `X-Evidence-Hash`); their SHA-256 is `evidence_hash`. 404 until the claim is decided. `python -m app.verify` checks a ruling from these routes (RULES.md "Public evidence"). |
| Escrow | In `escrow` mode, `GET /api/settlement` also has `escrow_contract`, and `POST /api/bounties/{id}/withdraw` starts the escrow's `withdraw_remaining` (docs/SETTLEMENT.md "Escrow mode"). In other modes it returns 409. |

Each signed transfer is checked before any row is written: valid ed25519 (NEP-413) or secp256k1 (ERC-191) signature, exact sender, receiver, token, amount, memo, and contract, and a future deadline. The dry settlement makes the same check, so dry runs show real signing. An invalid intent returns 422. A reused `claim_id` or `fund_id` returns 409. A transfer that fails returns 402. A transfer with an unknown result returns 504, and its claim or fund row stays held for review.

Reputation has no table and no migration. `app.bounty.reputation` counts the `claims` rows of one `hunter_account` (indexed) on all bounties, grouped by `outcome`. A proven claim without a `final_fingerprint` counts as `unfunded`. The score, tiers, multipliers, and rounding are in [Rules](../RULES.md#bonds-and-reputation). Intake calculates the required bond twice: once before the signature check, so that a wrong amount returns 422 with the required amount, and again inside the transaction that holds the bounty row lock and saves the claim and its `bond_in` row. If a decision on any bounty changed the amount between the two, the claim is rejected with 422 and nothing is saved. Intake does not change reputation, so parallel claims from one hunter get the same amount. The `bond_in` row holds the collected bond. The worker reads it for the `bond_forfeit` row, the `bond_refund` row, and the reward transfer, so a tier change after intake does not change what the hunter gets back.

The fingerprint is the SHA-256 of the claim type, the sorted canonical URLs, and the canonical details. It does not include the hunter. After the fresh check, the worker computes the `final_fingerprint` from the `final_url` of each observation (query and fragment dropped, same spelling as intake). The claim `fingerprint` stays as submitted. A partial unique index on `(bounty_id, final_fingerprint)` where `outcome = 'proven'` (migration `0006`, which backfills earlier proven rows) is the final rule for "first proven wins". For a broken link, the source and target are each replaced by their final page, so aliases of one source or of one missing target are one fault. For `broken_image` and `page_error`, the page is replaced by its final page and the second detail by what failed there (see the claim types below). Intake and job start compare the submitted `fingerprint` with held `final_fingerprint` values, which finds exact matches without a browser; an alias is found after its fresh observation. The `outcome` column keeps the check result after `status` changes to `paid` or `needs_review`.

| Outcome | Money |
| --- | --- |
| Proven and unique | The budget decreases by the reward and the fee when the outcome is decided, and a `fee` row is saved. `scout_part = reward * share_bps // 10000`. The hunter receives `reward - scout_part + bond` with memo `arsune:reward:{claim_id}`; the `reward_out` and `bond_refund` rows have its transaction ID. The scout receives `scout_part` with memo `arsune:scout:{claim_id}` in a `scout_share` row. The claim becomes `paid`. The bounty becomes `closed` with `closed_reason = 'budget_spent'` when its budget is less than one reward and its fee. |
| Rejected | A `bond_forfeit` row is saved, and the bond is added to the budget. |
| Proven, but the budget cannot pay the reward and fee | Checked after the duplicate test, under the bounty lock. The claim has `outcome = 'proven'` and `status = 'unfunded'`, no `final_fingerprint` (so it holds no fault), and an open bounty becomes `closed` with `closed_reason = 'budget_spent'`. The hunter receives the bond with memo `arsune:refund:{claim_id}` in a `bond_refund` row. No reward, fee, or scout row is saved. |
| Duplicate or unverified | The hunter receives the bond with memo `arsune:refund:{claim_id}` in a `bond_refund` row. |

The worker saves each outgoing payment row without a transaction ID before it sends that payment. A unique constraint allows one row of each kind for a claim. A repeated attempt skips rows that have a transaction ID. A row without a transaction ID means the result is unknown: the claim goes to `needs_review`, and no payment is sent again. A `SettlementError` marks the row `failed`. An `OutcomeUnknown` marks it `unknown` and records the NEAR transaction hash in the claim note. Neither is retried. A returned transaction ID is saved even if the worker lease expired during the transfer.

When a claim job fails at its time or attempt limit, the coordinator (`settle_stopped_claims`) settles the claim without a worker lease, through the same steps as the worker. A claim without an outcome becomes `unverified` with an evidence bundle that gives the reason, and its bond is refunded (`escrow`: an `unverified` ruling). A decided claim gets the rest of its payments. A payment row without a transaction ID still sends the claim to `needs_review`.

The coordinator recovers claims that stay `received` without a job for 5 minutes. This happens when the API stops between the bond transfer and the job creation. A bond row with a transaction ID queues the check. A bond row without one is marked `unknown`, and the claim goes to `needs_review`. If the slow API request later records the bond, the claim returns to `received` and its check is queued.

`app.bounty.settlement.get_settlement()` returns `NearIntentsSettlement` when `ARSUNE_SETTLEMENT_MODE=near_intents`, else `DrySettlement`. Dry payouts return IDs like `dry-<hex>` and use no network. Read views use the configured mode and do not load the key. In `near_intents` mode, every relayer transaction from the API and all workers takes one PostgreSQL advisory lock, because one key has one access-key nonce sequence. See [Settlement](SETTLEMENT.md).

Claim checks run in parallel. Each Celery prefork process opens its own database connections, and each claim job has its own lease. The bounty row lock is held only for short database steps, never during a browser check or a transfer.

Limits: duplicates are found by the final URLs after HTTP redirects. A site that serves the same page at many paths without redirects can accept equal faults with different fingerprints. In `near_intents` mode, the shared relayer lock sends one transaction at a time.

### Claim types

Bounty claims and scan candidates use the same shapes. `details` must have exactly these keys. All URLs are absolute.

| `claim_type` | `urls` | `details` | Checker result |
| --- | --- | --- | --- |
| `broken_link` | `[source, target]` | `{"source": url, "target": url}` | Confirmed when the fresh source page links to the target and the target answers HTTP 400 or higher. |
| `horizontal_overflow` | `[url]` | `{"url": url}` | Confirmed when the page is wider than 392 pixels at the 390-pixel mobile viewport with complete layout resources. |
| `price_mismatch` | `[page_a, page_b]` | `{"key": fact_key}` | Confirmed when both pages show one value each for the key, in the same context, and the values differ. |
| `broken_image` | `[page]` | `{"page": url, "image": url}` | See below. |
| `page_error` | `[page]` | `{"page": url, "message": text}` | See below. |

`broken_image` reads `page` once at the 1280-pixel desktop viewport. An image matches when its `src` or `final_url` has the same origin and path as `image`; query strings and fragments do not count. The claim is confirmed when a matching image is shown and its request ended with HTTP 400 or higher, or with HTTP 2xx that the browser cannot decode. It is rejected when every matching image loaded. It is unverified when the page did not answer HTTP 2xx, no image matches, the failing image is hidden, or the image is still loading or was stopped by Arsune's limits. An image inside `<noscript>` is never requested, so it does not match.

`page_error` loads `page` once at the desktop viewport. The claimed `message` and each error message are normalized: whitespace is trimmed and collapsed, and case is ignored. The claim needs at least 8 characters after normalization and must be a part of the error's message; quote the first line without the stack, with or without the error name. The claim is confirmed when a first-party error matches and Arsune's limits blocked nothing that a visitor's browser would load. It is rejected when the page loaded completely and raised no first-party error; failed images that the site answered with an HTTP error do not make the load incomplete. It is unverified when the only matching error comes from another origin, the page raised a different first-party error, a resource was blocked, or the load did not finish. Console output and caught errors are not page errors.

At intake, both new types need `urls` equal to `[page]`. `image` follows the URL rules of a page: on the bounty origin and without a query string. Intake stores `message` normalized and lowercase, 8 to 500 characters, so the submitted fingerprint uses that text. The final fingerprint uses `{"page": final page, "image": final image URL}` for a broken image and `{"page": final page, "message": full normalized message of the matched error}` for a page error. If several images or errors match, the lowest value in sort order is used. A hunter that mirrors Arsune's fingerprint must normalize the message the same way, and can match a held fault exactly only by quoting the full message.

The scripted navigation specialist proposes `broken_image` for each shown image on a desktop page whose state is broken, with `image` set to the element's `src`. It proposes `page_error` for each first-party error of at least 8 characters, with the full `message`, on pages where Arsune's limits blocked nothing.

## Verification boundary

The controlled website includes a broken link, mobile overflow, inconsistent facts with matched context, and a healthy page for rejecting a deliberate false claim. Provide a corrected variant and explicit expected outcomes.

Run the actual API, database, queue, worker, browser, and UI. Kill an active worker, restart it, and verify completion with unique result IDs. Mock endpoint tests validate model transport but do not establish a live model connection.

Marketplace payments and production log connections are later goals. A real model connection remains part of this goal and must not be described as live-tested without a real call.
