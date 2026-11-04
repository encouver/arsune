# Arsune demonstration decisions

The user selected option A on 4 November 2026: Arsune operates its own workers. The first demonstration task remains open. Supplier-data extraction is the earlier candidate; other tasks are now being considered.

## Worker ownership

| Choice | Demonstration | Scope consequence |
| --- | --- | --- |
| A — Arsune operates the swarm | A coordinator assigns two workers, a checker finds an error, and a worker corrects it. Arsune sells the combined result through one Agent Market listing. | Matches the current README. Internal worker budgets are cost controls. They are not independent agent payments. |
| B — Arsune hires another provider | Arsune delegates one bounded subtask through Agent Market, checks the returned result, and combines it with its own work. | Adds buyer authentication, a real provider, payment authority, source-sharing rules, child deadlines, and child-result acceptance. |

Decision: use A for the first build. B is deferred. The internal workers do not hire or pay other providers.

The official AI Swarms track emphasizes coordination markets and economic activity between agents. Our assessment is that B demonstrates this more directly. A demonstrates coordinated work and checking, but its economic claim is limited to the customer hiring Arsune. This assessment is not a ruling on track eligibility. The published group-buying prompt is an example, not a requirement to build an auction or create a particular number of accounts. [Track example](https://github.com/nearai/near-hackathon-prompt-examples/blob/main/tracks/05-swarms.md), [Hackathon tracks](https://nearhackathon.com/)

No option requires us to build a new marketplace or payment contract. [Market integration](MARKET_INTEGRATION.md) records the buyer and worker credential distinction.

## First task

The proposed customer request remains: turn a supplier price list into product names, prices, currencies, and evidence. For a small demonstration, [Rules](../RULES.md) narrows this to one PDF with selectable text and proposes synthetic test cases.

Confirm whether this is still the intended first task. If the product should perform another kind of work, name one concrete customer request and its required output before choosing agents or evaluation data. The coordination design can remain similar, but the checks and reference answers will change.

## What can remain fixed

- FastAPI API and Python agent execution, with a Next.js interface.
- Explicit task and evidence formats.
- A coordinator that enforces deadlines, correction limits, and cost ceilings.
- A checker with access to original sources and a record of unresolved findings.
- Stored processing state, repeat-safe work, and separate marketplace status.
- One repeatable simulation and one real model demonstration.
- Synthetic or public data until private marketplace delivery is verified.

## Discussion outcome

Worker ownership is settled. Select one demonstration task next. Do not implement the supplier-specific rules or prepare its dataset until the task is selected. No real-spend authority has been granted by this design work.

Implementation tasks and their dependencies are stored in beads. The scope decision is `arsune-1s7`. The original integration and dataset task, `arsune-pqj`, remains incomplete until this decision, reviewed reference answers, and confirmed acceptance rules are complete. The live seller connection has its own implementation task.
