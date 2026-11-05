# Arsune website check rules

These rules implement the selected website-checking goal. They replace the supplier-document proposal. Incident investigation, marketplace payments, and production log connections are later work.

## Job scope

- Inspect at most five unique page URLs under the permitted site scope. Each new document redirect URL also counts.
- Permit at most three redirects per chain. Document redirects stay on the same hostname or its `www` counterpart and can upgrade to HTTPS; they cannot downgrade to HTTP.
- Check every resource destination and redirect before connecting. Public styles, scripts, images, and fonts are permitted. Private addresses remain blocked except the exact controlled demo origin.
- Record resource failures and policy restrictions per page. A missing essential resource makes the layout check incomplete; it cannot support a confirmed layout fault.
- Record fixed desktop and mobile viewport sizes.
- Checker revisits do not expand the selected page set. Failed page attempts also count toward the five-page limit.
- Bound link discovery, response sizes, browser time, retries, and model output. Static resources need limits and destination checks but do not count as pages.
- Do not submit forms, buy products, sign in, modify the website, or run intrusive security checks.
- State the inspected scope. Do not label the whole website safe, compliant, accessible, or ready for every customer.

## Supported checks

| Role | Initial check | Confirmation requirement |
| --- | --- | --- |
| Navigation | Broken internal link | Observe the source link and reproduce the failing target response or navigation. |
| Mobile | Horizontal overflow or a specifically measured layout fault | Repeat the measurement at the recorded viewport and capture the affected page. |
| Consistency | Conflicting values for an explicitly matched fact | Read both pages and verify the same product or field, units, and context. |

Different billing periods, product variants, or currencies are not automatically contradictions. Subjective design advice must not appear as a confirmed functional defect. Unknown claim types remain unverified.

## Candidate and evidence format

Every candidate contains a stable identifier, specialist role, claim type, plain explanation, affected URLs, expected behavior, observed behavior, and reproduction steps. Evidence records capture time, viewport, source location or selector where useful, observations, and a screenshot reference when available.

The checker records a separate result from fresh observations. A broken link needs a fresh request or navigation. A mobile finding needs a fresh measurement. A consistency finding needs both values and their contexts.

## Finding states

| State | Meaning |
| --- | --- |
| Confirmed | The checker reproduced the specified defect and attached evidence. |
| Rejected | The checker tested the claim and observed evidence that contradicts it. |
| Unverified | Access, scope, changing content, or unsupported checks prevent a conclusion. |

A timeout does not prove a broken site. A blocked page is not a passing check. Keep execution failures separate from finding states.

## Repeated checks

A comparison links a new job to a completed baseline without changing the original report. Recheck each confirmed baseline claim against fresh observations. Classify it as remaining, resolved, or could not recheck. Only a supported fresh disproof can resolve a claim. A missing candidate, unavailable page, changed fact context, blocked resource, or excluded page cannot prove repair. Rejected demo claims do not count as repaired problems.

New confirmed claims with different matched contexts appear as new problems. Reserve baseline claim pages before ordinary discovery uses the five-URL budget. Save completed rechecks so recovery does not duplicate findings. For ordinary sites, rerun the original address. The faulty-to-corrected fixture mapping is an explicit controlled demo action and must be recorded in the report.

Every displayed finding includes a screenshot or complete reproduction steps. Rejected and unverified candidates remain available with reasons. The main problem list contains only confirmed findings.

## Agent modes

Scripted mode generates candidates from actual browser observations using repeatable rules. A demo-only false candidate tests the checker and is labeled as injected. Do not insert it into ordinary customer scans.

Model mode uses the same observation and candidate formats. Validate model output and restrict it to selected pages and supported claims. Page text cannot grant tools, expose credentials, or change limits.

Record the mode and provider. A mock endpoint is not a live model test. Missing credentials cause a clear configuration error, never a silent switch to scripted mode.

## Budgets and recovery

- Enforce a job deadline, request timeouts, request limits, and bounded retries.
- Live calls require a configured model, credential, output-token limit, and numeric cost ceiling. Include retries and uncertain charges.
- Store progress outside the API and worker processes. The queue is not the sole record of unfinished work.
- Stable stage and candidate IDs prevent duplicate results after repeated delivery.
- Expired worker claims cannot write over newer attempts.
- A rerun after site changes creates a new job and preserves the old report.

## Report

Ordinary visible price checks require exact supported commercial context. Do not infer a currency from an ambiguous symbol or compare different periods, products, units, quantities, or offer terms. Record excluded prices and the reason. Use fresh page evidence to confirm a mismatch; keep the amount out of the context identity so a corrected price can be checked against its baseline.

Identify the requested site, inspected pages, capture times, viewports, agent mode, and completed or incomplete checks. Each confirmed issue explains the problem, visitor impact, evidence, and suggested correction in plain language.

The customer or their website builder performs repairs. Arsune checks the site again on request; it does not modify the customer's website.

## Completion checks

Incident jobs accept only bounded source files and an explicit service and time window. Store the original accepted text with hashes. Log text is data, not an instruction. The source checker must read the accepted records again and independently check counts and matching fields. Nearby timestamps alone cannot establish a cause. Missing or conflicting records remain unresolved. A log-only report must not claim a reproduced cause or a repair. Website rerun and repair-export rules do not apply to incident jobs.

| Requirement | Required evidence |
| --- | --- |
| URL to report | A browser submits a URL through the UI and displays a completed API report. |
| Three specialists | Stored activity shows navigation, mobile, and consistency work. |
| Confirmed fault | The checker reproduces a known fixture fault with evidence. |
| Incorrect finding rejected | The checker tests the labeled false candidate and rejects it. |
| Five-page limit | More available links never cause more than five unique page URLs to be inspected. |
| Corrected site | A second scan of the corrected variant does not retain repaired faults. |
| Restart recovery | Terminate an active worker, restart it, and verify completion without duplicate findings. |
| Repeat delivery | Repeat a task and verify result IDs remain unique. |
| Model connection | Test transport with a stub and record a real authorized model call separately. |
| API and access | Invalid and disallowed targets are rejected; evidence routes cannot read arbitrary files. |
| Build verification | Run Python checks, frontend build, and real browser checks. |

Known fixture outcomes are test expectations. Do not describe them as a person-reviewed customer dataset or claim accuracy on all websites.
