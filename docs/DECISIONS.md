# Arsune product decisions

The user selected workers operated by Arsune and approved website checking as the first use case. The active build goal is a working flow from a website URL to an evidence report. Incident investigation is the second use case.

## Selected customer task

The customer builds a website with an AI tool but does not know what to check before launch. Arsune inspects up to five public pages on desktop and mobile. It explains confirmed problems, their effects, supporting evidence, and suggested corrections.

Three specialist agents check navigation, mobile layout, and consistency across pages. A separate checker reproduces reported faults and rejects unsupported claims. A Python coordinator enforces limits and stores progress.

Arsune does not repair the customer's website. The customer gives the report to their website builder and can request another check after changes. No source-code access is required.

## Current goal

1. Update the plan for website checking and retain incident investigation as the second use case.
2. Build the FastAPI API, stored job progress, and Python workers.
3. Implement the three specialists and the separate checker.
4. Provide a URL entry screen, progress view, and evidence report.
5. Use a controlled website with known faults. Start with scripted responses and add a real model connection through the same workflow.
6. Demonstrate a confirmed fault, a rejected incorrect finding, evidence for every finding, and recovery after interruption without duplicate results.
7. Run real API, worker, and browser checks.

The pasted goal supplied by the user on 4 November 2026 establishes this scope. It supersedes the supplier-document demonstration and the requirement to connect marketplace payments in this build.

## Agent ownership

Arsune operates all specialists and the checker. They do not hire or pay another provider. Internal cost allowances are limits, not payments between independent agents.

Agent Market remains the intended sales channel. Marketplace payments, public listing, custom contracts, and direct NEAR Intents integration are later goals. The [marketplace research](MARKET_INTEGRATION.md) remains a reference for that work.

## Incident investigation

The second use case will investigate a service failure from logs and deployment history. Reuse the coordinator, evidence records, checker, and report. Add timeline, error, and change analysis roles.

Start that later goal with a supplied log bundle and a known incident. Production connections need restricted read access, a service and time range, and sensitive-data handling. A likely cause must not be called proven merely because events occurred close together.

## Scope boundaries

- Five unique page URLs per job. Checker revisits do not expand the page set.
- Public page inspection. No form submission, purchase, login, or website modification.
- A controlled demonstration website with faults and a corrected variant.
- Scripted and model modes are visibly identified.
- No blanket security, accessibility, or launch certification.
- Real-model verification needs configured credentials and a numeric cost ceiling.

Implementation and verification are tracked in beads. The product choice is resolved. Implementation-model availability is a separate execution constraint.
