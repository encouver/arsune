# Agent swarm

The swarm is a set of independent hunter and scout agents that work on one Arsune bounty.
Arsune is only the judge, the escrow and the payer. The agents are clients: they reach Arsune
only through its public HTTP API, and they never read its database, models or bounty code.

- Code: `backend/swarm/` (importable as `swarm`, the same way as `app`).
- Tests: `backend/tests/test_swarm.py`.
- Start: `uv run python -m swarm simulate --setup` or `uv run python -m swarm live ...`.

The agents import two kinds of shared library code, as any NEAR Intents client could:

- `app.settlement.near_intents.sign_transfer` and `app.settlement.near_tx` (keys, NEP-413 signing).
- `app.browser.prices` (the published visible-price grammar), so a hunter computes the same
  `price_mismatch` key that the checker computes.

Live mode also reads `app.settlement.swarm_accounts.load_swarm_accounts` (the accounts file)
and `near_intents.balance` (a read-only RPC view).

## Agents

All agents run as asyncio tasks in one process. Each agent has its own identity, wallet,
crawl state and track record.

- Simulation: each agent has a fresh ed25519 key. The account ID is the implicit account
  (the hex public key).
- Live: each agent uses a named sub-account from the swarm accounts file.

Agents share only transport and public information:

- One HTTP connection pool.
- One Chromium with a small page pool (`--browser-pages`, default 6).
- One reader of the public bounty page (`GET /api/bounties/{id}`, polled every second).

### Hunters

Each hunter walks the site itself. It starts at the bounty URL, picks the next page at random,
and stays under the bounty URL's folder. Plain HTTP is used for pages and links. Chromium is
used for layout, prices, images and page errors.

| Strategy | What it does |
| --- | --- |
| `links` | Reads every link among the first 80 anchors of a page, requests each target once, and claims `broken_link` for a target with HTTP 400 or more. |
| `mobile` | Measures `document.documentElement.scrollWidth` at 390 x 844. It claims `horizontal_overflow` above 392 px (the checker's limit). |
| `prices` | Reads labelled facts and plain price cards on each page. It claims `price_mismatch` when two pages show exactly one value each for the same key and the values differ. |
| `images` | Opens each page at 1280 x 900 and reads the images on the bounty origin that finished loading without a picture. It claims `broken_image` for each one that is shown. A hidden image, an image on another origin, and an image that has not finished loading are not claimed. |
| `errors` | Opens each page at 1280 x 900 and records uncaught errors and unhandled promise rejections (a rejection that gets a handler later does not count). It claims `page_error` for each error from a script on the bounty origin, with the full first line of the error as the message. Console messages, caught errors and errors from another origin's script are not claimed. |
| `generalist` | Runs all five checks on 60% of the page budget. It is more willing to buy tips. |
| `careless` | Reads a few pages and guesses. It claims an overflow where the markup has a wide fixed width, or a broken link whose address or text looks old. It does not measure anything, so most guesses are rejected and it loses bonds. |
| `copycat` | Does not search. It re-submits faults that other hunters proved on the public bounty page. These are duplicates (bond refunded, no reward). It stops after `--copy-loss-limit` (default 3) copies in a row that earn nothing. |

Rules for every hunter:

- A page is named by its final URL after redirects, so a redirecting alias of a page is not
  claimed as a new fault.
- A careful hunter does not claim a fault that the public bounty page already shows as claimed
  (being checked or proven). The same fault gets the same verdict, so the claim could only be a
  duplicate. Claims made in the same second still race, and the later one becomes a duplicate.
- At most 3 unresolved claims (Arsune's limit; HTTP 429 means wait).
- It claims only when it can pay its own bond (see [Bonds](#bonds)). It is `broke` when its
  available balance is less than its own bond and no unresolved claim can refund a bond. A
  broke hunter stops.
- It is `done` when its crawl is finished, its queue is empty, no claim is open, and there is
  no tip left that it could buy.

### Bonds

Arsune sets each hunter's bond from the hunter's record on all bounties. A new hunter pays the
base bond (`bond_amount`). After 3 proven or rejected claims, the tier sets the bond: `trusted`
0.5x, `standard` 1x, `poor` 2x, `very_poor` 3x.

- Before it signs each claim, the hunter reads `GET /api/bounties/{id}/bond?hunter=<account>`
  and signs exactly `required_bond`.
- It also reads `tier` and `required_bond` from its row in the public bounty page, so it sees
  a tier change within a second.
- Its wallet checks, the broke rule and its tip valuation use its own bond.
- A tier change goes to the log, for example "hunter-012 is now trusted: its bond drops to
  0.05 USDC".
- If the record changes between the quote and the claim, Arsune answers 422 with "must be
  exactly". The hunter reads the quote again and signs the new bond once.

### Claims

The hunter makes the claim ID (a lowercase UUID v4) and signs the bond before it sends the
request:

- Signed transfer: exactly the hunter's `required_bond` of `token`, from the hunter to
  `arsune_account`, memo `arsune:bond:{claim_id}`.
- The `token`, `arsune_account`, `intents_contract` and `salt` come from `GET /api/settlement`.
  The swarm never reads the salt from the NEAR RPC.
- The deadline is 5 minutes.

Live hunters read `GET /api/settlement` again before each claim, because the salt can change.

The request is `POST /api/bounties/{id}/claims`. Its fields are `claim_id`, `hunter_account`,
`claim_type`, `urls`, `details`, `explanation`, `reproduction_steps`, `bond_intent`, and
`scout` for a bought tip. The responses:

| Response | Hunter action |
| --- | --- |
| 202 | The claim is accepted. |
| 409 or 429 | Keeps the finding and waits. |
| 402 | The bond failed. The hunter reads its balance again and gives up after 3 failures. |
| 422 | Drops the finding. |
| 504 | Counts the claim. Arsune holds it for review. |

### Wallets

- Simulation: the agent books its own rows from Arsune's public ledger.
  - The balance is the start amount (`--hunter-wallet`, default 0.3 USDC = 3 bonds), plus
    `reward_out`, `bond_refund` and `scout_share` rows, minus `bond_in` rows.
  - `earned` counts `reward_out` and `scout_share` rows.
  - `bonds_lost` counts `bond_forfeit` rows.
- Live: the balance is the account's real USDC balance in `intents.near` (`mt_balance_of`).
  The agent reads it at start and after its ledger rows change.

### Scouts and the tip board

Scouts are fast crawlers that never claim. They post tips on the swarm's tip board. The board
is part of the swarm, not of Arsune. Scout specialties:

- `links-scout`, `mobile-scout`, `prices-scout`, `images-scout` and `errors-scout` use the
  hunter checks.
- `hasty-scout` guesses like a careless hunter, so some of its tips are false.

A tip shows its claim type, evidence summary and asking share (25% to 40%). The pages are
revealed only to the buyer. A scout does not post a fault that is already claimed or listed.

Negotiation, one round per hunter and tip:

1. The hunter estimates its chance of success from its own record for that claim type:
   `p = (proven + 2) / (resolved + 3)`.
2. The value of a tip at share `s` is `p * reward * (1 - s) - (1 - p) * bond`, with the
   hunter's own bond. A claim that is not proven is counted as a lost bond, which is the
   cautious case.
3. The hunter's limit is the largest share that leaves `margin * reward`. The margin is a
   personal trait, from 0.35 to 0.65. The limit is rounded down to 1% and capped at 50%.
4. If the ask is within the limit, the hunter buys at the ask.
5. If the limit is at least 5%, the hunter counters once at its limit and gives a reason, for
   example "its record for mobile overflow claims is 1 proven of 3, so 16% is its limit".
6. Otherwise the hunter walks away. The tip stays offered for other hunters.
7. The scout accepts a counter at or above its private floor (55% to 80% of its ask).
8. If the scout refuses, the tip becomes `declined`. The scout posts the same fault once more,
   half way between its ask and its floor.

The buyer claims a sold tip at once with `scout: {account, share_bps, tip_id}`. Arsune pays the
scout `reward * share_bps // 10000` only when the claim is proven. The hunter gets
`reward - scout part + bond`.

Tips that are still offered are withdrawn (`declined`) in these cases:

- A hunter claims the fault.
- No buyer is left.
- The run ends.

A scout is `done` when its crawl is finished and it has nothing left to sell. Before that, it
waits for each sold tip to be settled.

## AI agents

AI hunters and AI scouts decide with a language model. They use the OpenAI-compatible chat API
of NEAR AI Cloud (`https://cloud-api.near.ai/v1`) by default. The default run has none, so the
judge's one-command demo stays free.

```sh
uv run python -m swarm simulate --setup --hunters 30 --ai-hunters 5 --scouts 5 --ai-scouts 2
```

| Option | Meaning |
| --- | --- |
| `--ai-hunters N` | Hunters `hunter-ai-01` ... with strategy `ai`. Added to `--hunters`. |
| `--ai-scouts M` | Scouts `scout-ai-01` ... with strategy `ai-scout`. Added to `--scouts`. |
| `--ai-budget USD` | The limit for all model calls of the run (default 0.50, at most 5). |

Settings come from the environment or `.env`. Each `ARSUNE_SWARM_MODEL_*` value that is not set
falls back to the matching `ARSUNE_MODEL_*` value:

| Variable | Fallback |
| --- | --- |
| `ARSUNE_SWARM_MODEL_BASE_URL` | `ARSUNE_MODEL_BASE_URL` |
| `ARSUNE_SWARM_MODEL_API_KEY` | `ARSUNE_MODEL_API_KEY` |
| `ARSUNE_SWARM_MODEL_NAME` | `ARSUNE_MODEL_NAME` |
| `ARSUNE_SWARM_MODEL_INPUT_USD_PER_MILLION` | `ARSUNE_MODEL_INPUT_USD_PER_MILLION` |
| `ARSUNE_SWARM_MODEL_OUTPUT_USD_PER_MILLION` | `ARSUNE_MODEL_OUTPUT_USD_PER_MILLION` |

The swarm refuses to start AI agents without a base URL, a key, a model name and both token
prices. The model client (`backend/swarm/llm.py`) is the swarm's own. It does not use
Arsune's judge model code.

### What the model does

An AI agent crawls and measures pages like a scripted generalist: it reads the status of each
link, the content width at a 390 px phone viewport, and the price cards. In the same desktop
visit it also reads the images that finished loading without a picture (and whether each is
shown), and the uncaught errors and unhandled rejections of the page (a rejection that is
handled later does not count). Every 6 pages (or at the end of its crawl, or when tips are
offered) it sends a compact summary to the model:

```text
p2 /shop/p/hosting-plus "Hosting Plus - Northwind Supply" phone width 390 px
  text: All hosting plans Hosting Plus USD 39 per month, per account ...
  links: 18 answer below 400; L4 /shop/retired/item-02 "Service status history" HTTP 404
  price F2 hosting plus (USD per month): USD 39; also seen: p1 /shop/c/hosting USD 29
  images that do not load: I1 /shop/img/partner-badge.png HTTP 404 shown
  uncaught errors: E1 "Error: Chat widget failed to start" from another origin's script
```

AI agents claim and sell all five provable types: `broken_link`, `horizontal_overflow`,
`price_mismatch`, `broken_image` (`{"page", "image"}`) and `page_error` (`{"page",
"message"}`). For a page error the agent quotes the full first line of the error, normalized
as Arsune normalizes it (whitespace collapsed, case folded), so its key is Arsune's key. The
scripted `images`, `errors` and `generalist` strategies use the same observations and the same
rules.

- An AI hunter's model answers strict JSON: the claims to make (`type` with page, link and
  fact ids), a decision on each offered tip (`accept`, `counter` with a share of 5% to 50%, or
  `walk`), and a short reason for each.
- An AI scout's model answers the tips to offer and the asking share (10% to 50%). When a
  hunter counters, the scout's model accepts or refuses the counter.
- Page text is untrusted data. The prompt says so, and the answer can only name ids that the
  agent itself observed. Unknown ids, other claim types and bad shares are dropped. An answer
  that is not a JSON object means no action.
- The agent builds the claim from its own observations, then claims through the same bond
  path as every hunter. The model never sees a key.
- The reason (at most 140 characters) goes to the log, for example "hunter-ai-03: the plan
  page shows USD 39 but the listing shows USD 29; claiming price_mismatch".

### Cost control

- Before each call, the run holds the worst case: the prompt's UTF-8 bytes (an upper bound of
  its tokens) times the input price, plus the output token cap times the output price.
- After the call, it charges the reported `usage`. Without a usage report (a timeout, for
  example), the whole hold stays charged. An HTTP error answer is not charged.
- When a call does not fit in the budget, no more calls are made. Each AI agent goes on as a
  scripted generalist or scripted scout, and its pages that are not reviewed get the scripted
  checks.
- The summary and `/state` show the calls and the spend.

## Modes

### `simulate` (default)

Simulation needs no money and no model. It can run on a judge's machine.

- It refuses to start unless `GET /api/settlement` reports `"mode": "dry"`.
- Defaults: 100 hunters
  (`links=17,mobile=13,prices=12,images=8,errors=5,generalist=25,careless=10,copycat=10`) and
  10 scouts (`links=3,mobile=2,prices=2,images=1,errors=1,hasty=1`). The specialists follow
  the demo shop's 66 bugs: 20 links, 15 overflows, 15 prices, 10 images and 6 page errors.
- With `--setup`, a fresh implicit owner creates a bounty on the demo site, publishes the proof
  token through the demo's `POST /bounty-proof-control`, and verifies the bounty.
- The owner then funds the bounty with a signed transfer (memo `arsune:fund:{fund_id}`).
  Defaults: reward 1 USDC, bond 0.1 USDC, budget 20 USDC, fee 10%.

```sh
uv run python -m swarm simulate --setup --site http://127.0.0.1:8541/shop/ --budget 80000000
uv run python -m swarm simulate --bounty-id <id> --hunters 30 --scouts 5 --mix links=1,careless=1
```

### `live`

Live mode moves real USDC on NEAR mainnet.

- It refuses to start unless `--i-approve-mainnet` is given and `GET /api/settlement` reports
  `"mode": "near_intents"`.
- Accounts come from the swarm accounts file (`--accounts`, default
  `ARSUNE_SWARM_ACCOUNTS_FILE`, written by `scripts/near_swarm_accounts.py`). The roles are
  `owner`, `hunter-N` and `scout-N`.
- With `--setup`, the `owner` account creates and funds the bounty.
- Default amounts are small: reward 0.1 USDC, bond 0.01 USDC, budget 0.5 USDC. The swarm
  refuses a budget above 1 USDC, or a reward plus bond above 1 USDC.

```sh
uv run python -m swarm live --setup --i-approve-mainnet --hunters 4 --scouts 1
```

### Stop conditions and summary

The swarm stops at the first of these:

- The time limit (`--duration`, default 300 s).
- The bounty is no longer `open` (for example closed because the budget is spent).
- Every agent is broke or done.

The swarm then waits up to `--settle-timeout` (120 s) for Arsune to settle the open claims.
It prints a summary: claims, proven, rejected, duplicates, unverified, in review, payouts to
hunters and scouts, bonds lost, tips offered, sold, countered and declined, broke agents, wall
time, the hunters' reputation tiers, the AI agents' calls, spend and results (when AI agents
ran), and a leaderboard. The same lines are the last entries of the `/state` log.
`--summary-json <file>` also writes the summary as JSON.

Other options:

| Option | Meaning |
| --- | --- |
| `--pace` | Think-time multiplier. `0` runs at full speed. |
| `--pages` | Page budget per agent (default 60). |
| `--seed` | Repeatable strategy assignment and agent traits. Timing still varies between runs. |
| `--hold` | Keeps `/state` served after the run until Ctrl-C. |
| `--quiet` | Does not print events. |

## `GET /state`

The swarm serves `http://127.0.0.1:8590/state` (`--state-host`, `--state-port`). It is a
read-only JSON endpoint with `Access-Control-Allow-Origin: *`. The board reads it through the
`/swarm-state` rewrite.

```json
{"run_id": "3f2a9c1b7d4e", "mode": "simulation", "bounty_id": "…", "started_at": "2026-11-09T20:50:23+00:00",
 "finished": false,
 "agents": [{"id": "hunter-007", "account": "<64 hex or named account>", "role": "hunter",
             "strategy": "links", "state": "waiting", "balance": 1200000, "claims": 3,
             "proven": 1, "earned": 1000000, "bonds_lost": 100000,
             "tier": "new", "required_bond": 100000}],
 "tips": [{"id": "<uuid>", "scout": "scout-02", "claim_type": "broken_link", "urls": ["…", "…"],
           "ask_share_bps": 3000, "status": "sold", "buyer": "hunter-007",
           "final_share_bps": 2400, "note": "sold to hunter-007 after a counter"}],
 "log": [{"at": "2026-11-09T20:50:24+00:00", "agent": "hunter-007",
          "text": "hunter-007 lost its bond: the checker could not reproduce the mobile overflow on /shop/about (…)."}],
 "ai": {"agents": 7, "calls": 64, "usd_spent": 0.0412, "budget_usd": 0.5,
        "model": "openai/gpt-5.6-luna"}}
```

Agent fields:

- `state`: one of `searching`, `negotiating`, `claiming`, `waiting`, `broke`, `done`.
- `balance` and `earned` are integers in token units (USDC has 6 decimals).
- `bonds_lost` is the forfeited bond amount in token units, not a count.
- `tier` and `required_bond` are the hunter's reputation tier and the bond it must sign now.
  They are `null` for scouts.
- AI agents have the strategy `ai` (hunters) or `ai-scout` (scouts).
- Hunters: `claims` is the number of accepted claims and `proven` the number of proven ones.
- Scouts: `claims` is the number of sold tips that a buyer claimed, `proven` the number of
  those that were proven, and `earned` is the sum of scout shares.

Tip fields:

- `status`: `offered`, `countered` (a counter waits for the scout), `sold` or `declined`.
- `buyer` is set while a hunter counters and after a sale.
- `note` holds the last negotiation sentence.

`log` holds the last 200 events as plain sentences.

`ai` shows the number of AI agents, the model calls, the spend in USD, the budget and the model
name. Without AI agents it is `{"agents": 0, "calls": 0, "usd_spent": 0.0, "budget_usd": 0.0,
"model": ""}`.

## Limits

- Duplicates follow Arsune's exact-URL fingerprint. One dead target that is linked from
  several pages is a separate `broken_link` fault for each source page.
- Scripted agents call no language model. AI agents call one only within `--ai-budget`.
- Scripted and AI agents claim the five types that Arsune can prove. The careless strategy
  and the hasty scout guess only overflows and broken links. Every agent shows a readable
  label for a claim type it does not know, such as a tip or a copied claim of a new type.
- A proven claim that the remaining budget cannot pay is `unfunded`: Arsune refunds its bond
  and closes the bounty, and the swarm stops. The summary counts it as "proven but unfunded".
  The one-command race funds 80 USDC, which pays all 66 bugs (66 x 1.1 USDC with the fee).
