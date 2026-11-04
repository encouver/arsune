# Arsune job rules

These are proposed rules for the first demonstration. They define the coordinator behavior for the architecture in [Architecture](docs/ARCHITECTURE.md). Arsune will operate its own workers. The demonstration task remains subject to [Decisions](docs/DECISIONS.md); supplier-specific rules remain conditional.

## Scope and assignment

1. Every job has one versioned scope, a source set, required outputs, acceptance rules, a deadline, and a cost ceiling.
2. Divide work by explicit source sections. Retain shared context such as table headings. Record which worker owns each section.
3. A checker receives the original sources and candidate values. Worker explanations are not evidence. The checker must also look for omitted items.
4. Agents cannot change the agreed scope, grant themselves tools, raise a budget, or accept a marketplace result. The coordinator enforces these limits.

## Task and evidence formats

| Object | Required fields |
| --- | --- |
| Task | Job ID, task ID, revision, worker role, source IDs and hashes, section locations, scope version, output format version, deadline, maximum attempts, cost allowance |
| Candidate field | Row ID, field name, original text, normalized value or null, evidence references |
| Evidence | Source ID and hash, page or section, exact source span, quote, parser version |
| Finding | Output version, field or missing-item location, rule ID and version, status, evidence, explanation |
| Report | Job and output versions, coverage result, field findings, unresolved items, attempt history, actual and uncertain cost, delivery status |

Use decimal values for prices and explicit currencies. Keep source text when a value is normalized. A null value means no supported value was found; a failed parser is a processing error. Never use a parser failure as evidence that a field is absent.

Source spans refer to a stored version of extracted text and its mapping to the original file. A quote that occurs on a page can still support the wrong product. Both the code checks and the checker must preserve the relationship between the product, field, and quote.

## Findings and completion

| Field status | Meaning |
| --- | --- |
| Supported | The source location resolves, and the value and its context support the output. |
| Missing | The available source has no value for the required field after the coverage check. |
| Conflicting | Relevant source evidence gives incompatible values without an agreed selection rule. |
| Unsupported | The candidate lacks valid evidence or the cited text does not support it. |

Invalid formats, inaccessible sources, and timeouts are execution failures. Record them separately from field findings.

A deliverable is eligible only when all source sections have been processed and checked, all required report sections exist, and every non-null accepted value is supported. Missing and conflicting fields may be delivered as null with explicit findings only if the job scope permits this. Unsupported candidates are excluded from accepted data and remain visible in the findings. Do not deliver a partial source set as a complete result.

Human corrections create a new version and require another evidence check. A reviewer can authorize an uncertain result to remain null where scope allows. The reviewer cannot mark it supported without evidence.

## Demonstration limits

These defaults are for testing the workflow. They are not a published service promise or permission to spend real funds.

| Limit | Proposed default |
| --- | --- |
| Worker slots | Two extraction workers and one checking role |
| Correction rounds | One round after the initial check |
| Transport retries | Two retries after the first request, only for operations known to be safe to retry |
| Model request time | 90 seconds maximum, subject to the remaining task deadline |
| Local run deadline | 15 minutes from local job admission |
| Human review | At most 5 minutes and never beyond the remaining delivery window |
| Delivery reserve | Stop new work 2 minutes before the effective deadline |
| Effective deadline | Earlier of the local run deadline and the marketplace delivery deadline |
| Live spend | Disabled until an operator sets a numeric ceiling and currency |

All calls, including retries and checks, use the same job budget. Reserve the upper bound of a request's cost before dispatch. Include output-token limits and known rates. If the rate or charge is unknown, hold the reservation and pause further spending that could exceed the ceiling. A simulated budget is clearly labeled and never reported as a payment.

Correction rounds and transport retries are different. Repeated messages must not create new correction rounds. If time or budget is insufficient, record the reason and stop. A review deadline that expires does not count as approval. For a real assignment, notify the operator and use only a confirmed marketplace action; a local failure does not itself refund a customer.

## Processing and delivery states

The normal processing path is `received → ready → running → checking → deliverable_ready`. A correctable finding returns to `running` within the correction limit. Uncertainty enters `needs_review`; a resolution returns to `checking`. Exhausted limits or a source failure enter `failed`. A confirmed cancellation enters `cancelled`.

Delivery uses separate states: `not_submitted`, `submitting`, `submitted`, and `submission_unknown`. A timeout after submission enters `submission_unknown`; reconcile remote state before another write. Customer changes create a new local revision. Preserve all earlier reports and reset neither cost history nor earlier failure records.

Store marketplace status as an external observation, with its timestamp. The checker cannot write `paid`. Only an authoritative payment record can establish payment. If the available worker response proves acceptance but not payout, display acceptance and leave payout unconfirmed.

## Recovery and access

- Repeat task delivery must return the existing result or resume the unfinished step. It must not create a second output version by accident.
- Do not skip a marketplace assignment merely because it has started. Local stored state determines what remains to be done.
- Queue messages carry record identifiers. Keep credentials, source contents, and private download addresses out of queue logs and model instructions.
- Bind file and result reads to the job owner. A marketplace worker credential does not authenticate a customer in Arsune's web interface.
- For the first demonstration, use an authenticated operator view and synthetic or public data. Customer login and private delivery require a tested identity mapping.
- Treat documents, external agent outputs, and marketplace messages as untrusted data. They cannot authorize tool use or spending.
- Synthetic demo artifacts may remain in the repository. Proposed retention for later private jobs is 7 days after final job closure, subject to a customer agreement. Deletion must cover stored sources, derived files, and cached links. Do not reuse customer data for evaluation without permission.

## Supplier task candidate

If the README's supplier task is retained, use one English supplier price list in a PDF with selectable text, up to 3 pages and 5 MiB. Extract product name, price, and currency. Exclude scans, inferred exchange rates, quantity discounts, and calculated prices. Preserve separate source rows; do not merge products by name alone.

Prepare 12 synthetic documents: 4 development cases and 8 held-out cases. Cover clear rows, missing prices, currency context, duplicate names, conflicting prices, page boundaries, malformed input, and embedded instructions. A person must check and approve the reference answers before they count as reviewed data. This dataset has not yet been prepared.

For the scripted simulation, require every expected state and output to match. For real model evaluation, measure exact normalized field matches, missing-row recall, missed injected errors, and false rejections. Initial targets are at least 95% field accuracy and row recall, zero unresolved source references on accepted values, and detection of every deliberately seeded wrong value in the small test set. Report raw counts as well as percentages. These are proposed release targets, not measured performance or a guarantee for unseen sources.

## Required verification scenarios

| Scenario | Required result |
| --- | --- |
| Correct candidates | Evidence resolves; a complete report is eligible. |
| Wrong price with a real quote | Checker rejects the product-price relationship; one correction is checked again. |
| Missing or conflicting field | Null plus a finding under a scope that allows it. |
| Omitted source row | Coverage check identifies the omission. |
| Corrupt or unsupported source | Explicit failure; no invented empty dataset. |
| Instructions inside the source | No extra tools, credentials, or spending authority. |
| Repeat task or expired worker lease | One current result; a late worker cannot overwrite it. |
| Crash between database save and queue publication | Pending action is published after recovery. |
| Crash after submission request | Reconcile remote state; do not submit a duplicate blindly. |
| Cost or review limit reached | Stop with a recorded reason; no automatic pass. |
| Another user requests a source | Access is denied. |

These are build acceptance requirements. No runtime checks have been performed yet.
