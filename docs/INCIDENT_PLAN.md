# Incident investigation: scope and design

This defines the second use case: investigate supplied event and deployment files with scripted specialists and an independent source checker. It uses no model key and does not connect to production logs. See [verification results](VERIFICATION.md) for tested behavior and [local instructions](RUNNING.md) for the upload flow.

## Input and limits

Start with a local bundle containing `events.ndjson`, `deployments.json`, and a short incident description with the service and time range. Use synthetic data for the first test. Accept at most 10 MB, 5,000 events, 20 deployment records, and a 24-hour window. Require timestamps with an explicit offset, stable event IDs, and a service name. Reject invalid records with line numbers; do not silently repair missing time zones.

Copy accepted inputs to job-owned storage and record a content hash. Evidence references include file name, hash, line number or record ID, timestamp, and an escaped excerpt. Do not execute log text, follow URLs in logs, or treat a message as an instruction. No credentials or production connection are needed for the test bundle.

## Controlled incident

Create a synthetic checkout service fixture. Deployment v2 starts at 10:00 UTC. Requests then fail with a missing-column database error. Deployment records show that the required migration did not complete. A control case contains the same deployment time but an unrelated upstream timeout, so timing alone cannot establish the cause.

Provide a corrected bundle, a missing-deployment-record bundle, a duplicate-event bundle, a conflicting-time-zone bundle, and a bundle containing instruction-like text. Expected results must identify evidence records, not merely match a summary sentence.

## Roles

| Role | Work | Required evidence |
| --- | --- | --- |
| Timeline specialist | Order accepted records and identify the first observed failure | Timestamped event IDs and known gaps |
| Error specialist | Group exact error signatures and affected requests | Error excerpts, counts, and request IDs |
| Change specialist | Find recorded deployments and configuration changes in the selected window | Deployment IDs, revisions, and recorded migration results |
| Checker | Read cited records again, validate counts and joins, and test each proposed explanation | Independent checks of the source records and explicit unresolved questions |

The coordinator enforces bounds and saves completed stages. Stable event and candidate IDs prevent duplicate evidence after worker restart. Reuse the job, progress, evidence, report, and recovery patterns already used for website checks; define a separate input format and claim types.

## Result rules

- **Observed fact:** directly established by accepted records, such as a count of matching errors.
- **Supported explanation:** records support a possible cause, but the failure has not been reproduced.
- **Rejected explanation:** available records contradict the specific claim.
- **Could not verify:** records are missing, ambiguous, outside the selected window, or inconsistent.
- **Reproduced cause:** reserved for a separate controlled execution that recreates the failure and removes it by changing the suspected cause. A log bundle alone cannot earn this label.

A deployment before a failure is a timing fact, not proof of causation. The report must separate the timeline, observations, possible causes, contradictory evidence, missing evidence, and suggested next checks. It must not claim that the service was repaired.

## Build sequence and acceptance

1. Add a bounded parser and immutable source records; test invalid rows, timestamps, duplicates, and size limits.
2. Add the three scripted specialists and the independent source checker.
3. Add a local bundle submission flow and report with clickable evidence excerpts.
4. Verify the known incident and the unrelated-timeout control. The control must not blame the deployment solely because its timestamp is nearby.
5. Stop and restart a worker after saved analysis. Confirm retained source hashes, stable results, and no duplicate events.
6. Verify that missing records produce unresolved results, corrected data changes the result with evidence, and instruction-like text has no effect on tools or limits.

Production connections, customer authentication, broader log formats, model-assisted explanations, retention policy, and deployment access are separate work. A later production connection must use narrowly scoped read access and identify the service and time window before data is read.
