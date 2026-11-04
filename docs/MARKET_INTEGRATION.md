# Agent Market integration findings

Public documentation and the served API schema were read on 4 November 2026. The served worker reference identifies version 0.50.0; OpenAPI identifies API version 2.0.0. These are design inputs, not proof of an authenticated integration. No listing, hire, upload, message, model call, or payment was made.

## Selling Arsune

Use one self-hosted listing and its `aat_` token. The documented worker flow supports polling, starting work, messages, and submission. Webhooks are optional. Keep the token in the marketplace connection code and outside model inputs. [Self-hosted agents](https://docs.market.near.ai/guides/self-hosted-agents)

The first connection should poll every 30–60 seconds. Reconcile active and recently submitted work from stored assignment IDs. Read rework messages before creating a new local revision. Webhooks can be added later; they require signature checks and duplicate handling.

## API contract

The following operations and fields were found in the current [OpenAPI schema](https://market.near.ai/openapi.json):

| Operation | Relevant contract |
| --- | --- |
| `GET /v1/agents/me/assignments` | Default status is `in_progress`; `status=all` is available. Page size is at most 100, with an offset. |
| `GET /v1/jobs/{job_id}/attachments` | Lists job sources; access depends on participation and job visibility. |
| `GET /v1/jobs/{job_id}/attachments/{attachment_id}/raw` | Downloads source bytes. A pending scan can return 503 with retry guidance. |
| `POST /v1/assignments/{id}/start` | Repeated starts return the original start stamp. |
| `GET/POST /v1/assignments/{id}/messages` | Reads feedback and sends progress. The message API supports `clientRequestId`. |
| `POST /v1/files` | Reserves an upload using `filename` and `byteSize`. |
| `POST /v1/files/{file_id}/complete` | Confirms upload completion and returns file metadata and a hash. |
| `POST /v1/assignments/{id}/submit` | Takes `deliverableMarkdown` and optional `files`; `files` is a list of strings. |

Do not assume that submission has the same duplicate protection as start. Persist the intended report hash and reconcile an uncertain response before retrying. Compare the actual submitted version; do not assume a remote hash uses our encoding or algorithm. If the version cannot be established, retain an unknown state for operator review. Cover pagination in the implementation.

## File privacy

The schema defines `ReserveFileRequest.link=true` as a public capability link. Anyone with that link can use it while the file exists. This differs from an authenticated customer download. The served [worker reference](https://market.near.ai/skill/SKILL.md) uses that option in its result-file flow.

Use synthetic or public inputs for the first marketplace demonstration. Keep original sources in private Arsune storage. Before accepting private customer data, test whether submitting completed file IDs without public links provides the required access control for every intended client. Do not claim that mode is verified. Private internal storage alone does not make exported evidence private.

## Acceptance and payment

For ordinary hires, submission and payment are separate. The buyer can accept or request changes. The documentation also describes automatic acceptance after the configured 3–7 day window and a 5% platform fee. The checker cannot release payment. Read confirmed marketplace records and report payout as unknown when only acceptance is observable. [Payments and disputes](https://docs.market.near.ai/concepts/payments-and-disputes)

Keep these semantics specific to ordinary hires. The worker reference describes different settlement behavior for A2A/x402 calls. Those calls are outside the first integration proposal.

## Hiring other agents

A worker token cannot accept deliverables, access the owner's wallet, or move funds. Hiring requires a separate buyer connection. The served worker reference distinguishes `/mcp`, where a hire is prepared for approval on a draft page, from `/mcp/pro`, where the buyer can spend in the call. [Served worker reference](https://market.near.ai/skill/SKILL.md)

The general [MCP hiring guide](https://docs.market.near.ai/a2a/hire-agent-over-mcp) describes buyer tools and real spending, but does not make that endpoint distinction consistently. Use the tools actually advertised to the authenticated buyer, then test permissions before designing autonomous subcontracting.

For the external-worker option, Arsune needs a separate child job, a selected provider, permitted source data, an explicit budget, a shorter child deadline, and rules for accepting or disputing the child result. The parent customer remains responsible for accepting Arsune's own deliverable. Do not equate internal cost allocations with payments between independent agents.

## Test path and limits

The listing guide permits hiring your own draft agent. It says this hire does not build reputation and still deducts the marketplace fee on acceptance. This is a real-money test, not a free sandbox. [Listing guide](https://docs.market.near.ai/list-your-agent)

No sandbox or testnet route was identified in the inspected OpenAPI paths. This is not proof that no test environment exists. Use a local simulation until a supported test environment or a bounded live test is selected.

Before live use, verify source download, start, file upload and completion, submission, rework, cancellation, and status reconciliation with an authorized account. Confirm artifact access as both the intended buyer and an unrelated user. The current research establishes the documented connection shape only.
