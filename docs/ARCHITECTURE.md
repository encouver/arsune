# Arsune website checking architecture

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

| Component | Choice | Purpose |
| --- | --- | --- |
| API | Python, FastAPI, Uvicorn | Jobs, progress, reports |
| Validation | Pydantic | Request, observation, candidate, and finding formats |
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

The first deployment is a local operator tool, not a public scanning service. Keep model credentials on the server. Evidence routes must not expose arbitrary files. Isolate project containers and volumes from other local projects.

The current browser permits resources only from the target origin. It blocks external resources and redirects and records blocked requests in coverage. This can change the appearance of sites that use external styles or scripts. A redirect requires a new job with the final permitted URL. The consistency checker currently requires matching `data-fact-key` and `data-context` attributes. Ordinary page text does not yet receive a general consistency check.

## Job sequence

1. Validate the URL and persist the job.
2. Select at most five page URLs and capture desktop and mobile observations.
3. Run navigation, mobile, and consistency specialists.
4. Validate candidate findings.
5. Have the checker reproduce supported claim types with fresh observations.
6. Store confirmations, rejections, and unresolved cases.
7. Return the report with evidence and completed-check coverage.

An interrupted run resumes stored stages. A rerun after repairs creates a new job and preserves the original evidence.

## Verification boundary

The controlled website includes a broken link, mobile overflow, inconsistent facts with matched context, and a healthy page for rejecting a deliberate false claim. Provide a corrected variant and explicit expected outcomes.

Run the actual API, database, queue, worker, browser, and UI. Kill an active worker, restart it, and verify completion with unique result IDs. Mock endpoint tests validate model transport but do not establish a live model connection.

Marketplace payments and production log connections are later goals. A real model connection remains part of this goal and must not be described as live-tested without a real call.
