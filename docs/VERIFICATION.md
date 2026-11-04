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

Redirects and resources from other origins are blocked. The report states that rendering can be incomplete. Consistency confirmation requires explicitly matched fact/context attributes; it does not establish general consistency of ordinary website text. These limits are tracked in `arsune-dxk`.

Model mode has three separate specialist requests and a browser checker, but no live provider result is claimed. Complete `arsune-5is` with a locally stored credential, a selected compatible model, current token prices, and an authorized test spending limit.

This build has no customer accounts, public-service access controls, marketplace payments, website repairs, or production log connections. Incident investigation remains the second use case.
