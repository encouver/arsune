# Arsune demonstration decisions

The next discussion must decide what the swarm demonstrates and who operates its workers. FastAPI can support both options below. The current README proposes workers operated by Arsune and a supplier-data task.

## Worker ownership

| Choice | Demonstration | Scope consequence |
| --- | --- | --- |
| A — Arsune operates the swarm | A coordinator assigns two workers, a checker finds an error, and a worker corrects it. Arsune sells the combined result through one Agent Market listing. | Matches the current README. Internal worker budgets are cost controls. They are not independent agent payments. |
| B — Arsune hires another provider | Arsune delegates one bounded subtask through Agent Market, checks the returned result, and combines it with its own work. | Adds buyer authentication, a real provider, payment authority, source-sharing rules, child deadlines, and child-result acceptance. |

Recommendation: build A first if the priority is a complete working service within the current scope. Choose B now if a real economic exchange between agents is essential to the demonstration. B is a material product change, not a different Python framework.

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

Record A or B and one demonstration task. If B is selected, also settle the provider, source-sharing permission, and maximum real spend before enabling a buyer connection. No real-spend authority has been granted by this design work.

Implementation tasks and their dependencies are stored in beads. The scope decision is `arsune-1s7`. The original integration and dataset task, `arsune-pqj`, remains incomplete until this decision, reviewed reference answers, and confirmed acceptance rules are complete. The live seller connection has its own implementation task.
