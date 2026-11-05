# Prototype verification — 4 November 2026

The local scripted flow works from URL entry to an evidence report. Live model verification remains incomplete because no model service credential and test spending limit were supplied for this run. Model transport tests used a stub and are reported separately.

## Results

| Check | Measured result |
| --- | --- |
| Complete Python suite | 25 tests passed against actual PostgreSQL and Chromium after the backend structure change |
| Engine tests | 18 tests passed, including faulty/corrected scans, policy limits, saved state, and model failure cases |
| Backend tests | Seven tests passed for claim fencing, repeat-safe records, API/evidence access, deadlines, example settings, response compatibility, and migration upgrade/downgrade/upgrade in an isolated schema |
| Frontend checks | Production build, TypeScript check, format check, and three browser tests passed |
| URL to report | Actual UI, API, coordinator, Celery worker, Redis, PostgreSQL, and Chromium used together |
| Faulty fixture | Five selected pages; eight confirmed findings across broken links, mobile overflow, and matched price conflicts; one rejected injected claim |
| Corrected fixture | New job completed with zero confirmed findings |
| Evidence | Confirmed and rejected screenshots loaded through the API; reproduction steps were present |
| Worker interruption | Worker stopped after saved observations; replacement finished the same job at generation 2 and retained saved screenshot names |
| Repeated queue delivery | Repeated task reached the worker and completed; report, generation, and unique finding IDs stayed unchanged |
| Public HTTPS smoke | `https://example.com/` returned HTTP 200 at desktop and mobile sizes; one page selected, no incomplete pages, zero findings |
| Container checks | Backend and frontend images built; backend API import and Chromium launch also passed inside a container with network disabled |
| Model transport | Stub tests cover request shape, authorization, token/cost bounds, malformed JSON, timeouts, role validation, and uncertain-charge recovery |
| Live model service | Not run; remains required to complete the full build goal |

The eight fixture findings include separate source-link and page-pair findings. They do not represent eight different defect categories. The fixture has three defect types.

The complete suite emitted dependency deprecation notices. These did not fail a test. The full runtime flow was tested with host Python/Next processes and container PostgreSQL/Redis; image builds and container browser startup were additional checks, not a claim that every Compose service was tested together.

## Backend structure change

The backend moved from the prototype package to `backend/app`, following the Aldea and AprobaDoc layout. Application setup, schemas, database models, endpoints, services, agents, browser checks, and workers now have separate modules. Scan workflows are no longer in package `__init__.py` files.

The new API was compared with a saved response from job `49d47d3c-eff0-4677-a4f4-47091bb79846`. The full JSON response matched, including timestamps, report fields, event order, and findings. Existing screenshot evidence returned HTTP 200. The OpenAPI route and method sets also matched.

All three interface browser tests passed against the new API and worker in 20.8 seconds. They covered faulty and corrected scans, rejected claims, screenshot access, coverage, mobile layout, and a lost progress connection. The unchanged frontend also passed its type check. Worker recovery passed on the new command path with job `7be3164f-ea4e-4cf3-8c63-b5e17b9bd447` at generation 2, with saved captures and no result changes after repeated task delivery.

The database schema and registered task name `arsune.scan` stay the same. Python process commands now use `app.main`, `app.demo`, `app.workers.queue`, and `app.workers.coordinator`; see [updated commands](RUNNING.md).

## Local evidence identifiers

- Latest recovery job: `7be3164f-ea4e-4cf3-8c63-b5e17b9bd447`.
- Latest repeated task confirmed as consumed: `f24a162c-5886-4d28-8876-a94766597166`.
- Existing job used for full response compatibility: `49d47d3c-eff0-4677-a4f4-47091bb79846`.
- Public HTTPS smoke job: `85482330-69b8-4501-a248-b4ab1e5b8208`.
- UI screenshots from the test run: `/tmp/arsune-desktop-report.png` and `/tmp/arsune-mobile-error.png`.

Job data and screenshots are local test artifacts. They are not committed customer data. Use [the setup and check commands](RUNNING.md) to reproduce the checks.

## Limits that remain

The 5 November network update permits bounded same-site redirects and public rendering resources. Frames, workers, popups, forms, sockets, and data requests remain blocked. The report records these restrictions and incomplete layouts. Consistency supports explicit fact/context attributes and a limited visible-price grammar. General text matching remains unsupported.

Model mode has three separate specialist requests and a browser checker, but no live provider result is claimed. Complete `arsune-5is` with a locally stored credential, a selected compatible model, current token prices, and an authorized test spending limit.

This build has no customer accounts, public-service access controls, marketplace payments, website repairs, or production log connections. Incident investigation remains the second use case.

## Network coverage update — 5 November 2026

Controlled Chromium tests exercise redirect final addresses and relative styles, HTTPS and `www` changes, redirect loops and limits, external script execution and image loading, delayed and missing styles, five-URL limits, and saved request/transfer budgets. Private-address and changing-DNS tests use a listening sentinel and assert that forbidden requests never reach it. Test-only public-host routing maps a small fixed set of fixture names to the local server; production private-address checks are not relaxed.

All five frontend browser tests passed against the updated API and worker in 45.6 seconds. They include actual redirects, missing stylesheet reasons, incomplete layout notices, old report support, screenshot evidence, corrected-site results, and connection errors. The frontend production build and type check passed.

Worker recovery passed with job `e18e331c-b8da-4072-ae84-89d177af2898`: eight confirmed findings, one rejected injected claim, five pages, retained screenshots, and unchanged results after repeated delivery. The backend container build and import checks passed. No paid model calls were made.

The full backend suite passed 40 tests in 92.30 seconds. A final targeted Chromium test also passed after the font readiness correction: a font response with invalid font data leaves layout incomplete, prevents confirmed overflow, and retains the reason after checkpoint recovery. Valid external font decoding has no separate acceptance test yet.

## Comparison and repair report update — 5 November 2026

The backend passed 46 tests in two groups: 44 regression, API, database, and migration tests in 99.23 seconds, plus two final real Chromium comparison tests in 141.29 seconds. Comparison cases include unchanged faults, repairs with fresh evidence, new faults, unavailable pages, blocked styles, excluded scope, explicit corrected-demo mapping, preserved baseline reports, and resumed saved checks. API checks also cover normalized input addresses, unfinished baselines, and escaped report text with rejected external evidence paths.

Migration `0002` passed upgrade, downgrade, and upgrade in an isolated PostgreSQL schema and was applied to the local database. The backend package and container builds passed.

Ordinary worker recovery passed with job `67ebcbce-2039-4928-b7c9-4d31b45e6b0a`. Comparison recovery passed with job `5a8de2b9-58ce-41bc-9423-421dc8f7ef96`, linked to baseline `ee0f459f-3a09-44ab-b1bc-440a40f492fc`. It resumed at generation 2 after a saved baseline check, kept the baseline unchanged, retained evidence, and ignored duplicate delivery. Its comparison recorded eight remaining faults and zero resolved, new, or uncheckable faults. The downloaded HTML report contained 30 embedded PNG references.

The new features use scripted checks and require no paid model call. Reports can be reopened from a local job URL. Downloading an HTML repair report does not publish it or send it to another service.

All eight frontend browser tests passed across the full run and one corrected test rerun. The corrected expectation distinguishes resolved claims on available pages from uncheckable claims on a failed page. Coverage includes all four comparison groups, active-job refresh, before/after links, preserved old reports, screenshot evidence, HTML download, and mobile layout. The production build and type check passed. Mobile test capture: `/tmp/arsune-comparison-mobile.png`.

## Visible prices and saved history — 5 November 2026

Twenty new Chromium price cases passed across two runs. The first run passed 19 cases and failed a quote assertion because Chromium inserted two line breaks. After the assertion was changed to compare normalized whitespace, nine affected cases passed again, including stronger checks that different commercial contexts produce different keys. Cases cover fresh mismatch evidence, repair resolution, equal prices, currencies, periods, products, units, quantities, ambiguous terms, hidden text, and price cards below the first screen. The original annotated fixtures remain unchanged.

A separate real Chromium and database test passed for the HTML export of a price card below the first screen. Card images use the existing file-access and export-size limits. The checker requires fresh card evidence before it confirms an ordinary price mismatch.

Ordinary recovery passed with job `544a57d3-fa8b-4f0e-ac4e-278e02067de2`. Comparison recovery passed with job `28f6cd6a-e6c1-46a2-bcc1-f9f13c0bd62b`. Both retained the controlled outcome of eight confirmed findings, one rejected claim, and five pages. The backend container build and Ruff check passed.

The history API passed two actual PostgreSQL tests covering metadata-only reads, pagination with equal creation times, new inserts between pages, and invalid parameters. The frontend production build and type check passed. Each finding remains separate because its page pair, quoted text, images, and repair status can differ.

The final backend verification covers 71 distinct tests across completed runs: 48 regression tests, 20 visible-price cases, and three price-card export and exclusion checks. Six affected tests passed again after the last narrow fixes for inherited crossed-out prices and signed amounts. The final backend container was rebuilt successfully. The regression run emitted four dependency deprecation warnings and no test failures.

All 11 distinct interface browser tests passed across the full run and two corrected test reruns. The initial run passed nine tests. The two test corrections wait for the new scan to finish and accept the singular heading for one fault. The final rerun passed both cases against the final worker. Tests cover exact price quotes and card images, downloads, history reopening, older pages, excluded offers, baseline links, incomplete comparisons, new faults, and mobile evidence. Final TypeScript checks passed after the test changes. UI capture: `/tmp/arsune-price-history.png`.

The incident plan is complete in [INCIDENT_PLAN.md](INCIDENT_PLAN.md). No incident analysis feature, production log connection, or paid model call was added by this update.

## Incident investigation — 5 November 2026

Incident investigation now accepts supplied event and deployment files through the local interface and API. The parser and engine pass 32 tests. These cover the known missing-column case, unrelated timeout control, corrected and incomplete bundles, conflicting deployments, timestamp offsets, duplicate IDs, malformed and oversized inputs, instruction-like text, source hash checks, tampered specialist counts, checkpoint reuse, report limits, and original deployment evidence values.

The known incident supports a possible failed-migration explanation. A timeout does not establish that explanation. Missing failure records and missing or conflicting deployment evidence remain unresolved. No test or report labels a log-only explanation as a reproduced cause.

Real worker recovery passed for job `202abb1b-d0b5-44b8-938f-9e71dca8cabc`. The test worker paused after committing specialist progress and was killed. A replacement completed generation 2 with the same source text, source hashes, saved stages, and report as a clean engine run. The four activity event IDs stayed unique. Repeated queue delivery left the result unchanged. The pause made the kill point repeatable; the API, database, queue, parser, specialists, and checker were real.

Six incident interface scenarios passed against the real API, worker, queue, and PostgreSQL. They cover upload and completion, exact source records, saved history and reopening, the unrelated-timeout control, malformed input, invalid UTF-8, inert HTML text, and a request larger than 10 MB after JSON encoding. The large-request case completed with 1,100 accepted events while the decoded sources remained within the 10 MB limit. Two initial error-message test selectors also matched Next.js route alerts; the selectors were narrowed to the incident form and the tests passed again. Existing website faulty/corrected and price/history/comparison flows also passed.

The final frontend production build and type check passed. A final browser check exercised the result-area navigation after both submission and reopening. UI capture: `/tmp/arsune-incident-report.png`. Migration tests cover upgrade/downgrade/upgrade and preservation of a website job from schema `0002`. The final backend container build and Ruff checks passed. No production log connection or paid model call was used.

Backend verification covers 107 distinct tests. The full regression run passed its 105 collected tests in 454.72 seconds and emitted four dependency deprecation warnings. Two later tests, for original deployment timestamp evidence and preservation of a pre-incident website job, passed separately. Existing website engine, comparison, export, database, and migration checks remain included in this result.
