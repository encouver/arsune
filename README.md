# Arsune

**AI work. Checked against the source.**

Arsune is an AI swarm project for the NEAR hackathon. Its first use case checks websites for people who build with AI but do not know how to verify the result. Its second use case will investigate incidents from logs and deployment history.

## Current build goal

Build a working flow from a URL to an evidence report. Three specialists inspect navigation, mobile layout, and consistency across pages. A separate checker reproduces reported faults and rejects unsupported findings.

The user approved workers operated by Arsune. The first build inspects at most five pages, starts with a controlled faulty website, and uses scripted responses before connecting a real model through the same workflow.

The local prototype includes the API, persistent worker, browser checks, controlled test website, and report interface. Scripted checks use actual browser observations. Model mode uses the same evidence checks and requires a configured service key and cost limit. Live model verification is still pending.

## Customer flow

1. Enter a website address.
2. Watch the agents inspect the selected pages.
3. Read confirmed problems with screenshots or reproduction steps.
4. Give the report to the tool or developer that built the website.
5. Request a new check after the customer makes changes.

Arsune does not repair the website. It does not submit forms, make purchases, log in, or modify customer code in this version. It reports what was checked and what could not be verified.

## Design

- [Architecture and agent roles](docs/ARCHITECTURE.md)
- [Job rules and completion checks](RULES.md)
- [Product decisions](docs/DECISIONS.md)
- [Setup and verification commands](docs/RUNNING.md)
- [Agent Market research for a later integration](docs/MARKET_INTEGRATION.md)

FastAPI and Python follow the patterns used in Aldea and AprobaDoc. The runtime uses PostgreSQL, Celery with Redis, Playwright, and a Next.js interface. A protected local evidence volume stores screenshots. Python and frontend lockfiles record dependency versions.

The backend package is `backend/app`. It separates application setup, shared schemas and database code, API endpoints and services, specialists, browser checks, and workers. Start the API with `uv run uvicorn app.main:app --host 127.0.0.1 --port 8540`. See the [backend structure](docs/ARCHITECTURE.md#backend-structure) and [process commands](docs/RUNNING.md).

Task status and dependencies are stored in beads. Use bd ready to find available work.

## Completion evidence

The first goal requires a real API, worker, and browser run that confirms a known fault, rejects an incorrect finding, and returns evidence for every finding. An interrupted job must resume without duplicate results. Scripted and real-model verification must be reported separately.

The controlled website must contain known faults and a corrected variant. These are repeatable test cases, not evidence of accuracy on every website.

Local checks passed on 4 November 2026: 25 Python tests, three interface browser tests, a worker stop/restart test, and frontend/backend image builds. The backend structure change also preserved the full stored job response and existing evidence access. The faulty site produced eight confirmed findings across three fault types and one rejected false claim. The corrected site produced no confirmed findings. See [measured verification results](docs/VERIFICATION.md). The live model test remains open.

## Current limits

This is a local operator tool. It has no customer accounts or public-service access controls. The browser checks at most five selected pages under one origin. External resources and redirects are blocked and can affect page appearance; the report records these limits. For a redirect, submit the final permitted URL in a new job.

Navigation checks use response status and source links. Mobile checks measure horizontal overflow. Consistency checks compare visible values with matching `data-fact-key` and `data-context` attributes. The controlled site supplies these attributes. General text consistency on ordinary websites needs a later extension.

The model service proposes candidates. A separate browser check determines the result. A model reply alone cannot confirm a fault. If a worker stops during a paid request, the job must not repeat that request when its charge is uncertain.

## Later goals

- Incident investigation from a supplied log bundle, followed by restricted production log connections.
- Agent Market listing, seller connection, and payments.
- Private customer deployment and cloud evidence storage.
- Broader checks and supported user journeys.

Agent Market remains the intended sales channel. Direct NEAR Intents integration and a custom payment contract are unnecessary for the current local goal.

## Hackathon context

The intended track is AI Swarms. All agents are operated by Arsune; internal cost limits are not payments between independent providers. Track fit remains subject to the event's evaluation.

The event site checked on 4 November 2026 lists a development deadline of 14 November 2026 at 23:59 UTC and requires a public repository and public demo post. This repository remains private. Publication is a separate submission action.

- [NEAR hackathon](https://nearhackathon.com/)
- [AI Swarms example](https://github.com/nearai/near-hackathon-prompt-examples/blob/main/tracks/05-swarms.md)
- [NEAR Agent Market](https://market.near.ai/)
- [NEAR AI documentation](https://docs.near.ai/)
