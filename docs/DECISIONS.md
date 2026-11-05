# Arsune product decisions

The user selected workers operated by Arsune and approved website checking as the first use case. Incident investigation from supplied test logs is the second use case. Both use saved jobs and independently checked evidence.

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

The second use case investigates a service failure from supplied logs and deployment history. It reuses the coordinator, job records, worker claims, and history. Timeline, error, and change specialists propose claims; a separate checker reads the original source records again.

The user approved this build with synthetic log bundles and known incidents. Production connections remain separate work. A likely cause must not be called proven merely because events occurred close together.

The [incident design](INCIDENT_PLAN.md) defines bounded inputs, synthetic cases, specialist roles, source evidence, and acceptance checks without a model key. Supported explanations remain distinct from reproduced causes.

## Scope boundaries

- Five unique page URLs per job. Checker revisits do not expand the page set.
- Public page inspection. No form submission, purchase, login, or website modification.
- A controlled demonstration website with faults and a corrected variant.
- Scripted and model modes are visibly identified.
- No blanket security, accessibility, or launch certification.
- Real-model verification needs configured credentials and a numeric cost ceiling.

Implementation and verification are tracked in beads. The user authorized parallel implementation with `gpt-6.1-sol`. This development model choice is separate from the model service that Arsune will use at runtime.
