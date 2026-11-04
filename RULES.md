# Arsune website check rules

These rules implement the selected website-checking goal. They replace the supplier-document proposal. Incident investigation, marketplace payments, and production log connections are later work.

## Job scope

- Inspect at most five unique page URLs under the permitted origin.
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

Identify the requested site, inspected pages, capture times, viewports, agent mode, and completed or incomplete checks. Each confirmed issue explains the problem, visitor impact, evidence, and suggested correction in plain language.

The customer or their website builder performs repairs. Arsune checks the site again on request; it does not modify the customer's website.

## Completion checks

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
