# Arsune

**AI work. Checked against the source.**

Arsune is an AI swarm project for the NEAR hackathon. Its first use case checks websites for people who build with AI but do not know how to verify the result. Its second use case will investigate incidents from logs and deployment history.

## Current build goal

Build a working flow from a URL to an evidence report. Three specialists inspect navigation, mobile layout, and consistency across pages. A separate checker reproduces reported faults and rejects unsupported findings.

The user approved workers operated by Arsune. The first build inspects at most five pages, starts with a controlled faulty website, and uses scripted responses before connecting a real model through the same workflow.

Implementation has not started yet. The project currently contains the design and beads tasks. Runtime instructions and measured verification results will be added with the implementation.

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
- [Agent Market research for a later integration](docs/MARKET_INTEGRATION.md)

FastAPI and Python follow the patterns used in Aldea and AprobaDoc. The proposed runtime uses PostgreSQL, Celery with Redis, Playwright, and a Next.js interface. A protected local evidence volume supports the first local demonstration. Pin compatible dependency versions during implementation.

Task status and dependencies are stored in beads. Use bd ready to find available work.

## Completion evidence

The first goal requires a real API, worker, and browser run that confirms a known fault, rejects an incorrect finding, and returns evidence for every finding. An interrupted job must resume without duplicate results. Scripted and real-model verification must be reported separately.

The controlled website must contain known faults and a corrected variant. These are repeatable test cases, not evidence of accuracy on every website.

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
