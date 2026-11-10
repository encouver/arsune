# Arsune rules

These rules cover website checks, incident investigation, and the bug bounty market. They replace the supplier-document proposal. Production log connections are later work.

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
| Navigation | Broken image | Read the page again, find the shown `<img>`, and observe its request fail with HTTP 400 or higher, or an HTTP 2xx answer that the browser cannot decode. |
| Navigation | Uncaught page error | Load the page again and observe an uncaught error or unhandled promise rejection with the claimed message, raised by a script on the site's own origin. |
| Mobile | Horizontal overflow or a specifically measured layout fault | Repeat the measurement at the recorded viewport and capture the affected page. |
| Consistency | Conflicting values for an explicitly matched fact | Read both pages and verify the same product or field, units, and context. |

Different billing periods, product variants, or currencies are not automatically contradictions. Subjective design advice must not appear as a confirmed functional defect. Unknown claim types remain unverified.

## Candidate and evidence format

Every candidate contains a stable identifier, specialist role, claim type, plain explanation, affected URLs, expected behavior, observed behavior, and reproduction steps. Evidence records capture time, viewport, source location or selector where useful, observations, and a screenshot reference when available.

The checker records a separate result from fresh observations. A broken link needs a fresh request or navigation. A mobile finding needs a fresh measurement. A consistency finding needs both values and their contexts. A broken image needs a fresh page read with the failed image request. A page error needs a fresh page load that raises the error. Console messages, caught errors, and rejections that get a handler later are not page errors.

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

- Enforce a job deadline, request timeouts, request limits, and bounded retries. The job deadline starts when a worker first takes the job, so time in the queue does not count.
- Live calls require a configured model, credential, output-token limit, and numeric cost ceiling. Include retries and uncertain charges.
- Store progress outside the API and worker processes. The queue is not the sole record of unfinished work.
- Stable stage and candidate IDs prevent duplicate results after repeated delivery.
- Expired worker claims cannot write over newer attempts.
- A rerun after site changes creates a new job and preserves the old report.

## Report

Ordinary visible price checks require exact supported commercial context. Do not infer a currency from an ambiguous symbol or compare different periods, products, units, quantities, or offer terms. Record excluded prices and the reason. Use fresh page evidence to confirm a mismatch; keep the amount out of the context identity so a corrected price can be checked against its baseline.

Identify the requested site, inspected pages, capture times, viewports, agent mode, and completed or incomplete checks. Each confirmed issue explains the problem, visitor impact, evidence, and suggested correction in plain language.

The customer or their website builder performs repairs. Arsune checks the site again on request; it does not modify the customer's website.

## Bug bounty market

A site owner pays for proven functional faults on the owner's own site. Independent hunter agents find the faults. Scout agents sell tips to hunters. The Arsune checker decides each claim with fresh browser evidence. Hunter explanations and scout tips are not proof.

| Party | Pays | Receives |
| --- | --- | --- |
| Site owner | The bounty budget | Proven faults with evidence |
| Hunter | A bond for each claim. The amount depends on the hunter's reputation. | For a proven, unique claim: the reward minus the scout share, plus the bond. For a duplicate, unverified, or unfunded claim: the bond. |
| Scout | Nothing to Arsune | The scout share of the reward when the claim that names the scout is proven and unique |
| Arsune | Rewards, scout shares, and refunds from held money | The fee for each proven claim |

### Scope

- Only the owner of a site can open a bounty for it. The owner puts the bounty token in `/.well-known/arsune-bounty.txt` on the bounty origin. Arsune checks no claims before this proof.
- Only functional faults are in scope: a broken internal link, horizontal overflow at the 390-pixel mobile viewport, a price mismatch for one matched product context, a broken image, and an uncaught error on page load. Other claim types are rejected at submission.
- A page error counts only when a script on the bounty origin raises it. An error from a script on another origin does not count.
- Security testing of any kind is out of scope. The checker does not submit forms, sign in, buy, change the site, or put load on the site.
- All claim URLs and detail URLs must be on the bounty origin and must not have a query string. The claim URLs must be exactly the pages that the details name. For a broken image or a page error, the claim URLs are only the `page`; the `image` URL is a detail URL, not a page.
- A hunter can have at most three unresolved claims on one bounty.

### Money in

- Each transfer into Arsune is a signed `transfer` intent of the exact amount of the bounty token to Arsune's account. The signature is NEP-413 (a NEAR key) or ERC-191 (an Ethereum key, `personal_sign`). The memo must be exact: `arsune:fund:{fund_id}` for funding and `arsune:bond:{claim_id}` for a bond. The client makes each ID as a lowercase UUID v4 and can use it only once.
- An ERC-191 transfer comes from the signer's implicit account: its `0x` address. The address must be lowercase, because NEAR account IDs have no capital letters. A checksummed (EIP-55) address as hunter, sender, owner, or scout gets 422 with the message "Use the lowercase 0x address". Arsune pays a `0x` account like any other account.
- Arsune checks the signature, sender, receiver, token, amount, memo, contract, and deadline before it saves the claim or the funding row. An invalid transfer creates no claim.
- In `escrow` mode the transfer goes to the escrow contract, not to Arsune, and it also carries a `msg` that names the bounty (`{"fund":"<bounty_id>"}`) or the claim (`{"bond":{"bounty_id":...,"claim_id":...}}`, with the scout and its share if the claim names one). Arsune checks the `msg` too, and relays the transfer. See [docs/SETTLEMENT.md](docs/SETTLEMENT.md#escrow-mode).
- In `near_intents` mode, an owner can also fund a bounty from another chain through NEAR Intents 1Click. Arsune adds the amount that 1Click reports as delivered to Arsune's account, once for each deposit address. The ledger row has the memo `arsune:fund:1click:{deposit address}`.
- A claim is accepted only when the bounty is open and its budget covers one reward and its fee. A closed bounty rejects new claims with 409.

### Bonds and reputation

The `bond_amount` of a bounty is the base bond. Each hunter pays the base bond multiplied by the multiplier of the hunter's reputation tier. A hunter with a good record pays less. A hunter with many false claims pays more.

- Reputation belongs to the hunter account. It counts the claims of that account on all bounties.
- A proven claim is correct. An unfunded claim is also correct, because the checker reproduced the fault. A rejected claim is false. Duplicate, unverified, and unresolved claims do not change the reputation.
- The score is `(correct + 1) / (correct + rejected + 2)`. An account with no decided claims has a score of 0.5.
- An account with fewer than three correct or rejected claims is in the `new` tier and pays the base bond. One early mistake or one early success does not change the bond.

| Tier | Condition (first match) | Multiplier |
| --- | --- | --- |
| `new` | Fewer than 3 correct or rejected claims | 1.0 |
| `trusted` | Score 0.75 or more | 0.5 |
| `standard` | Score 0.5 or more | 1.0 |
| `poor` | Score 0.3 or more | 2.0 |
| `very_poor` | Score less than 0.3 | 3.0 |

Examples, as correct and rejected claims: 3 and 0 give 0.8 (`trusted`). 2 and 1 give 0.6 (`standard`). 1 and 2 give 0.4 (`poor`). 0 and 3 give 0.2 (`very_poor`).

- The required bond is the base bond multiplied by the multiplier, rounded up to a whole token unit. It is at least 1 unit.
- In `near_intents` mode, the required bond is at most `ARSUNE_MAX_PAYMENT_AMOUNT` minus the reward, because one transfer returns the reward and the bond. This limit applies after the multiplier.
- `GET /api/bounties/{id}/bond?hunter=<account>` gives the required bond. The signed bond must be exactly that amount. A different amount is rejected with 422, and the message gives the required amount.
- Arsune calculates the required bond again under the bounty lock, before it saves the claim. If a decision changed the hunter's tier after the signature check, the claim is rejected with 422 and the new amount. The hunter must sign the new amount.
- A refund, a forfeit, and the bond part of a reward payment always use the bond that Arsune collected for that claim. A later change of tier does not change them.

### Outcomes

| Outcome | Meaning | Money |
| --- | --- | --- |
| Proven | The checker reproduced the fault, and no other claim holds its fingerprint | The budget pays the reward and the fee. The hunter receives the reward minus the scout share, plus the bond. The scout receives the scout share. |
| Rejected | The checker observed evidence that contradicts the claim | The bond is forfeited and added to the budget. |
| Unfunded | The checker reproduced the fault, but the budget cannot pay one reward and its fee | The bond is refunded. No reward. The claim does not hold the fault, and the bounty closes. |
| Duplicate | Another claim already proved the same fault | The bond is refunded. No reward. |
| Unverified | Access, resources, or scope prevent a conclusion | The bond is refunded. No reward. |

- The first proven claim for a fault wins. The fingerprint names the fault: the claim type, the canonical URLs, and the details. It does not name the hunter. The database permits one proven claim for each fingerprint on a bounty.
- The fault is held by the final URLs that the checker reached after redirects, not by the submitted URLs. A claim on a redirecting alias (for example `/shop/old-pricing`, which redirects to `/shop/pricing`) and a claim on the final page are one fault. Whichever is proven first holds it, and the other is a duplicate. For a broken link, the fault is one final source page with one final target page: two links that redirect to the same missing page are one fault, and a source page that redirects counts as the page it reaches. For a broken image, the fault is the final page with the final image file after redirects; the image query string does not count. For a page error, the fault is the final page with the full message of the matched error, so claims that quote different parts of one message are one fault.
- A claim for a fault that is already proven is a duplicate. If the submitted URLs match the held fault exactly, no browser check is needed. Otherwise the fresh check finds the duplicate.
- A proven claim that finds the budget unable to pay one reward and its fee is `unfunded`: the bond is refunded and the bounty closes. The claim holds no fault, so a later claim for the same fault can be paid after a top-up.
- A timeout, a blocked page, or a missing resource makes a claim unverified, not rejected. Only contradicting evidence forfeits a bond.
- A check job that reaches its time or attempt limit before a decision also makes the claim unverified. The coordinator saves an evidence bundle that gives this reason and refunds the bond (in `escrow` mode with an `unverified` ruling). A claim that was decided before the job stopped gets the rest of its settlement.

### Fees and shares

- The fee is `reward * fee_bps / 10000`, rounded down. The default is 1000 basis points (10%). The budget pays the fee for each proven claim, in addition to the reward. In `escrow` mode the fee is at most 2000 basis points (the contract's limit).
- A hunter can name one scout for a claim, with a share of 1 to 5000 basis points (0.01% to 50%) of the reward. The scout share comes from the reward, not from the budget.
- A decision that leaves the budget less than one reward and its fee closes the bounty. The budget is reserved for a proven claim when the checker decides it, so two claims cannot be paid from the same money.
- A bounty opens after the owner proof with a budget of 0. Until its budget pays one reward and its fee, it takes funds, but it rejects claims with 409. The message gives the budget and the amount that one reward and its fee need.

### Closing and top-ups

- Arsune records why a bounty closed (`closed_reason`): `budget_spent` (the budget cannot pay one reward and its fee, also after an unfunded claim), or `withdrawn` (`escrow` mode: the rest of the budget went back to the owner).
- An owner can add money to an open bounty, or to a bounty that closed only because its budget was spent. This applies to a signed funding transfer and to a 1Click deposit.
- A top-up opens such a bounty again when the new budget can pay one reward and its fee. A smaller top-up is added to the budget, and the bounty stays closed.
- A bounty that closed for another reason (for example a deadline or an owner close) does not open again. It refuses a signed funding transfer and a new 1Click deposit address with 409. Money from a 1Click deposit that arrives after such a close is added to the budget, and the bounty stays closed.
- A bond that a rejected claim forfeits is added to the budget, but it is not a top-up: it does not open a closed bounty.
- The escrow contract applies the same rule: a funding deposit to a bounty that closed for a spent budget is kept, and it opens the bounty again when the balance pays one reward and its fee. The owner's `close_bounty` and the deadline are final. After them, the escrow refunds new deposits, and anyone can start the withdrawal of the rest to the owner.

### Payment safety

- Arsune saves each outgoing payment in the ledger before it sends it, with a unique memo for each claim and payment kind. A payment is never sent twice.
- If a payment result is unknown or the payment fails, the claim goes to `needs_review` and the money stays held. There is no automatic retry. An operator checks the settlement account before any further action. A check job that stops does not put a claim in `needs_review` unless a payment of that claim has an unknown result.
- Every ledger row records the settlement mode. A row for money that a transaction moved records that transaction ID. In `dry` and `near_intents` mode, a `fee` row and a `bond_forfeit` row have no transaction ID: the funding transfers and the bond transfer already put that money in Arsune's account, and it stays there, so no transfer occurs. In `escrow` mode, both rows record the `rule` transaction, because the escrow moves that money.

### Public evidence

- When Arsune decides a claim, it saves an evidence bundle: the claim, its URLs, the viewports, the fresh observations, the checker's status and reason, and the ruling. Screenshots are named by their own sha256. The bundle holds no secret and no host file path.
- The claim's `evidence_hash` is the SHA-256 of the bundle as canonical JSON (compact, sorted keys, UTF-8). `GET /api/claims/{claim_id}/evidence` serves exactly these bytes, so their SHA-256 is the published hash.
- In `escrow` mode the same hash is part of the ruling on the escrow contract.
- `python -m app.verify <api_url> <claim_id>` checks the hash, the screenshots, the escrow record, and the ruling again in a fresh local browser. A site that changed after the ruling also gives a different fresh result.

### Settlement modes

- `dry` is the default. No money moves. Signed transfers are still checked, and payouts get IDs like `dry-<hex>`. Every bounty response states the mode. Do not describe dry results as real payments.
- `near_intents` moves real USDC inside the NEAR Intents Verifier contract on mainnet. Each transfer is limited by `ARSUNE_MAX_PAYMENT_AMOUNT`. A funding transfer above this limit gets 422 with the limit in the message, before any transfer, so its `fund_id` stays unused. Ledger rows link to the transaction on nearblocks.
- `escrow` (opt-in) keeps bounty money and bonds in the escrow contract. Arsune creates the bounty on the escrow, relays the signed deposits, and sends one `rule` transaction for each decided claim; the contract makes the payouts. A ruling with an unknown or different result goes to `needs_review`, as a payment does. `ARSUNE_MAX_PAYMENT_AMOUNT` and the bond cap do not apply. Escrow mode is tested on a local NEAR sandbox and with mocks. The escrow is not deployed on mainnet.

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
