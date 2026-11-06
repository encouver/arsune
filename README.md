# Arsune

**AI work. Checked against the source.**

Arsune is an AI swarm project for the NEAR hackathon. Its first use case checks websites for people who build with AI but do not know how to verify the result. Its second use case investigates supplied incident logs and deployment records with scripted specialists and an independent source checker.

## Current build goal

Build a working flow from a URL to an evidence report. Three specialists inspect navigation, mobile layout, and consistency across pages. A separate checker reproduces reported faults and rejects unsupported findings.

The second local flow accepts incident event and deployment files, runs timeline, error, and change specialists, and checks their claims against the original records. It uses scripted rules without a model key.

The user approved workers operated by Arsune. The first build inspects at most five pages, starts with a controlled faulty website, and uses scripted responses before connecting a real model through the same workflow.

The local prototype includes the API, persistent worker, browser checks, controlled test website, and report interface. Scripted checks use actual browser observations. Model mode uses the same evidence checks and requires a configured service key and cost limit. Live model verification is still pending.

## Customer flow

The [local demo](http://127.0.0.1:3540/demo) guides the operator through both use cases. It uses real scripted jobs and local test data without a model key.

1. Enter a website address.
2. Watch the agents inspect the selected pages.
3. Read confirmed problems with screenshots or reproduction steps.
4. Give the report to the tool or developer that built the website.
5. Request a new check after the customer makes changes.

Use **Check again and compare** on a completed report to create a linked check of the same address. The comparison separates problems that remain, problems proved resolved, new problems, and problems that could not be checked again. A missing candidate alone does not prove a repair. Each report has a local address that can be reopened after a refresh.

**Download repair report** saves an HTML file with readable findings, steps, and available screenshot evidence. It can be given to the website builder. The original JSON download remains available.

Arsune does not repair the website. It does not submit forms, make purchases, log in, or modify customer code in this version. It reports what was checked and what could not be verified.

For incident investigation, select **Incident investigation**, load the synthetic example or select the two source files, and submit the service and time window. The report links each finding to source records and separates supported explanations from missing evidence. Saved incident reports appear in the same recent-check list.

The unrelated-timeout control tests that deployment timing alone does not support the migration explanation. **Download incident report** saves a portable HTML file with verified cited records and source hashes. Export limits and omissions are stated in the file.

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

Current verification includes database and real browser tests, ordinary and comparison worker stop/restart checks, and builds. The faulty site produces eight confirmed findings across three fault types and one rejected false claim. The corrected site produces no confirmed findings. Comparisons require fresh evidence for resolved faults. See [measured verification results](docs/VERIFICATION.md). The live model test remains open.

## Current limits

The 5 November updates add bounded redirects, public rendering resources, incomplete-layout reports, repair comparisons, visible price checks, and saved report history. See [verification results](docs/VERIFICATION.md).

This is a local operator tool. It has no customer accounts or public-service access controls. The browser checks at most five page URLs, including redirect destinations. Document redirects permit the same hostname, its `www` counterpart, and HTTPS upgrades, with at most three redirects per chain. Public styles, scripts, images, and fonts can load after destination checks. Frames, workers, popups, forms, sockets, and data requests remain blocked. The report records resource failures and incomplete layout checks.

Navigation checks use response status and source links. Mobile checks measure horizontal overflow. Consistency checks support explicit `data-fact-key` and `data-context` attributes and ordinary visible price cards. Ordinary prices require a named plan, an explicit supported ISO currency, and a monthly or yearly period. The checker matches product context, units, and remaining offer terms before comparing amounts. Promotions, ambiguous currency symbols, and unsupported terms remain outside this check. Reports show excluded prices and their reasons. This is not a general text consistency check.

The recent-check list lets the local operator reopen saved website and incident reports. Each result keeps its own evidence.

Incident investigation is at `/incidents`. Load the synthetic example or upload event and deployment files. Timeline, error, and change specialists propose claims. The checker reads the original source records again and returns linked evidence. The first supported explanation rule matches missing-column errors to failed migrations with the same service, revision, and column. Other causes remain outside that rule. Logs alone do not prove a cause or a repair. See [incident scope](docs/INCIDENT_PLAN.md) and [input instructions](docs/RUNNING.md#incident-investigation).

The model service proposes candidates. A separate browser check determines the result. A model reply alone cannot confirm a fault. If a worker stops during a paid request, the job must not repeat that request when its charge is uncertain.

## Later goals

- Broader incident explanation rules and restricted production log connections.
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
