# Agent swarm

The swarm is a set of independent hunter and scout agents that work on one Arsune bounty.
Arsune is only the judge, the escrow and the payer. The agents are clients: they reach Arsune
only through its public HTTP API, and they never read its database, models or bounty code.

- Code: `backend/swarm/` (importable as `swarm`, the same way as `app`).
- Tests: `backend/tests/test_swarm.py` and `backend/tests/test_swarm_host.py`.
- Start: `uv run python -m swarm simulate --setup` or `uv run python -m swarm live ...`.
- Host races for the web UI: `uv run python -m swarm serve` (see [Race host](#race-host-serve)).

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
| 409 or 429 | Keeps the finding and waits. A paused bounty stops the whole swarm (see [Site check](#site-check)). |
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
| `ARSUNE_SWARM_MODEL_TIMEOUT_SECONDS` | none; default 60, at most 900 |
| `ARSUNE_SWARM_MODEL_TEMPERATURE` | none; not sent when it is not set |

The swarm refuses to start AI agents without a base URL, a key, a model name and both token
prices. A price of 0 means a free model, for example one on your own machine: its calls never
spend the budget. Do not set 0 for a paid model. Some cloud models refuse a temperature, so
the swarm sends one only when `ARSUNE_SWARM_MODEL_TEMPERATURE` is set. The model client
(`backend/swarm/llm.py`) is the swarm's own. It does not use Arsune's judge model code.

### What the model does

An AI agent crawls and measures pages like a scripted generalist: it reads the status of each
link, the content width at a 390 px phone viewport, and the price cards. In the same desktop
visit it also reads the images that finished loading without a picture (and whether each is
shown), and the uncaught errors and unhandled rejections of the page (a rejection that is
handled later does not count). Every 6 pages (or at the end of its crawl) it sends a compact
summary to the model, with the tips on offer:

```text
p2 /shop/p/hosting-plus "Hosting Plus - Northwind Supply" phone width 390 px (fits)
  text: All hosting plans Hosting Plus USD 39 per month, per account ...
  links: 18 answer below 400
  links that fail: L4 /shop/retired/item-02 "Service status history" HTTP 404
  price F2 hosting plus (USD per month): USD 39; also seen: p1 /shop/c/hosting USD 29
  images that do not load: I1 /shop/img/partner-badge.png HTTP 404 shown
  uncaught errors: E1 "TypeError: Cannot read properties of undefined (reading 'items')" from the site's own script; E2 "Error: Chat widget failed to start" from another origin's script
```

AI agents claim and sell all five provable types: `broken_link`, `horizontal_overflow`,
`price_mismatch`, `broken_image` (`{"page", "image"}`) and `page_error` (`{"page",
"message"}`). For a page error the agent quotes the full first line of the error, normalized
as Arsune normalizes it (whitespace collapsed, case folded), so its key is Arsune's key. The
scripted `images`, `errors` and `generalist` strategies use the same observations and the same
rules.

- An AI hunter's model answers one JSON object: the claims to make (`type` with page, link
  and fact ids), a decision on each offered tip (`accept`, `counter` with a share of 5% to 50%,
  or `walk`), and a short reason for each.
- A hunter holds at most 3 open claims, but its model lists every fault that it can prove (at
  most 10 in each answer). The agent queues the claims above the limit and makes each one when
  a claim settles. A reviewed page that still shows a fault that nobody claimed comes back
  once, under "Pages again", when a claim slot is free.
- Tips on offer wait for the next page review. After its crawl, a hunter calls the model for
  tips alone only when 4 tips are on offer or no scout still searches.
- An AI scout's model answers the tips to offer and the asking share (10% to 50%). When a
  hunter counters, the scout's model accepts or refuses the counter.
- Page text is untrusted data. The prompt says so, and the answer can only name ids that the
  agent itself observed. Unknown ids, other claim types and bad shares are dropped. An id with
  text after it (`"F3 analytics scale"`) names `F3`. When a sentence comes before or after the
  JSON, the agent reads the first complete JSON object. An answer with no JSON object means no
  action.
- The system prompt is the same for every agent of a role. The agent's name, wallet, bond and
  record are in the user message, so a model server can reuse the cached system prompt.
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

### Run AI agents on a local model (free)

NEAR AI Cloud is the main provider. It was paused on 9 November 2026 after a security
incident. The AI agents use any OpenAI-compatible chat API, so they also run on a local model
at no cost. On 9 November they ran on `llama3:latest` (Llama 3 8B, Q4_0) in Ollama 0.3.6 on one
GTX 1660 Ti (6 GB):

```sh
export ARSUNE_SWARM_MODEL_BASE_URL=http://127.0.0.1:11434/v1
export ARSUNE_SWARM_MODEL_NAME=llama3:latest
export ARSUNE_SWARM_MODEL_API_KEY=ollama          # Ollama needs no key; any text works
export ARSUNE_SWARM_MODEL_INPUT_USD_PER_MILLION=0 # 0 = a free model
export ARSUNE_SWARM_MODEL_OUTPUT_USD_PER_MILLION=0
export ARSUNE_SWARM_MODEL_TIMEOUT_SECONDS=300     # calls wait in the model's queue
export ARSUNE_SWARM_MODEL_TEMPERATURE=0.2         # steadier JSON from a small model
uv run python -m swarm simulate --setup --budget 80000000 --seed 11 \
  --hunters 3 --ai-hunters 3 --scouts 1 --ai-scouts 1 --pages 20 --pace 3 --duration 900
```

What we saw on 9 November (all runs dry, all model calls $0):

- The race above (3 scripted and 3 AI hunters, 1 scripted and 1 AI scout, each agent reads at
  most 20 of the 41 pages): `Summary: 31 claims, 30 proven, 1 rejected, 0 duplicates` and
  `Tips: 13 offered, 3 sold, 13 countered, 10 declined`, wall time 281 s. The AI agents made
  17 model calls. AI hunters: 6 claims, 5 proven, 1 rejected, 0 duplicates; they were first on
  5 of the 30 proven bugs. The AI scout offered no tip: the faults it saw were already claimed.
  A call took 65 s (median) as the agent saw it, mostly in the one-slot queue; the model
  itself needed about 15 s.
- One AI hunter and one AI scout (`--hunters 0 --scouts 0 --ai-hunters 1 --ai-scouts 1
  --pages 12`): 8 calls; the scout offered 4 tips, the hunter countered 2 and bought them at
  20%; 6 claims, 6 proven, 126.9 s.
- One AI hunter alone (`--hunters 0 --scouts 0 --ai-hunters 1`): 7 calls, 14 claims, 12
  proven, 2 rejected, 181.5 s.

Decisions from these runs, with the checker's verdict:

| Agent and its reason | Verdict |
| --- | --- |
| `hunter-ai-03`: "/shop/c/payments lists Payments Plus at USD 33, /shop/p/payments-plus at USD 43" | Proven: fresh reads 33 and 43 |
| `hunter-ai-03`: "/shop/p/support-basic is 812 px wide on a 390 px phone" | Proven: content width 812 px |
| `hunter-ai-03`: "/shop/p/hosting-basic and /shop/c/hosting both list hosting basic as USD 9 per month" (claimed as a price mismatch) | Rejected, bond lost: fresh reads 9 and 9 |
| `hunter-ai-01` countered a tip: "20% instead of 25%, because /shop/p/support-basic is 812 px wide on a 390 px phone"; `scout-ai-01` accepted: "Hunter's offer is reasonable, and I'm willing to take the risk" | Proven; the scout earned 0.2 USDC |
| `hunter-ai-01`: an overflow on /shop/p/storage-scale ("450 px wide") | Rejected, bond lost: content width 390 px |

Notes for a local model:

- Make sure that the model runs on the GPU: `curl -s 127.0.0.1:11434/api/ps` shows
  `size_vram`. On 16 CPU cores, one decision took 96 s. On the GPU it took 9 to 33 s, at
  about 23 output tokens per second. Our system Ollama had started before the GPU driver and
  ran on the CPU, so the run used a second `ollama serve` with `OLLAMA_NUM_PARALLEL=1` (two
  parallel slots were not faster on this card).
- One GPU answers about 4 calls a minute for all AI agents together, and each AI agent reads
  every page itself, so more AI agents do not cover the site faster. With 30 scripted hunters
  and 5 AI hunters at full speed, the scripted agents proved all 66 bugs in about 100 seconds,
  before the AI agents had made 10 calls. With 5 scripted and 5 AI hunters at `--pace 3`, the
  scripted agents proved 63 bugs in less than 2 minutes, and the AI hunters 1 (a tip that one
  bought after a counter). So the race above uses 3 scripted hunters, `--pace 3` and
  `--pages 20`. These limits come from the speed of one small GPU, not from the agents.
- Ollama's default context is 2,048 tokens, and Ollama cuts a longer prompt without an error.
  A 6-page prompt was at most about 1,460 tokens in our runs, and an answer about 50 to 230.
- At Ollama's default temperature (1.0 on its OpenAI API), llama3 sometimes broke a JSON key
  and then wrote spaces up to the token limit. At 0.2, all answers in our runs were JSON.
- `llama3.2:latest` (3B) answered in 37 s on the CPU but claimed two page errors that did not
  exist and a 392 px page as an overflow, so the run used the 8B model.

### Run AI agents on Claude

The AI agents can call Claude through the official Anthropic SDK. Set the provider, the model and its prices. The key comes from the environment (`ANTHROPIC_API_KEY`) or from `ARSUNE_SWARM_MODEL_API_KEY`; no base URL is needed. With this provider the swarm reads only `ARSUNE_SWARM_MODEL_*`, never the shared `ARSUNE_MODEL_*` settings, so it cannot send another provider's key to Anthropic.

```sh
export ARSUNE_SWARM_MODEL_PROVIDER=anthropic
export ARSUNE_SWARM_MODEL_NAME=claude-haiku-5-5
export ARSUNE_SWARM_MODEL_INPUT_USD_PER_MILLION=0.10
export ARSUNE_SWARM_MODEL_OUTPUT_USD_PER_MILLION=0.50
# ANTHROPIC_API_KEY must be set in the environment
uv run python -m swarm simulate --setup --budget 80000000 --seed 11 \
  --hunters 3 --ai-hunters 3 --scouts 1 --ai-scouts 1 --pages 20 --pace 3 --duration 900
```

Prices per million tokens (prompts up to 100K tokens):

| Model | Input | Output |
| --- | --- | --- |
| `claude-opus-5-5` | $4 | $20 |
| `claude-sonnet-5-5` | $2 | $10 |
| `claude-haiku-5-5` | $0.10 | $0.50 |

Notes:

- Each call asks for low effort. The swarm sends no temperature, top-p or thinking setting (current models reject them), so `ARSUNE_SWARM_MODEL_TEMPERATURE` has no effect here.
- A model thinks before it answers, and the thinking tokens count in `max_tokens` and in the output bill. Each call therefore allows at least 4,000 output tokens, on every provider, and the budget holds that worst case before the call. A small `--ai-budget` with Opus can stop the run after a few calls.
- For `claude-opus-5-5` and `claude-sonnet-5-5`, a policy refusal is retried on the server (`server-side-fallback-2026-07-01`). That retry bills at the fallback model's rates, which the swarm budget does not know. Use `claude-haiku-5-5` for a strict budget.
- The SDK retries a failed call twice. A refusal, a cut-off answer (`max_tokens`, or `finish_reason` `length` on the OpenAI-compatible path) or an API error is "no action" for the agent, and so is an answer that has none of the keys the prompt asks for. The parser does not read an inner object from a cut-off answer. The swarm does not use structured outputs. The parser takes the JSON object of an answer, also when a sentence comes before it, and the agent checks every field.

Result of a run on Claude:

On 10 November 2026, three AI hunters and one AI scout ran on `claude-opus-5-5`, with one scripted scout and no scripted hunters (`--hunters 0 --ai-hunters 3 --scouts 1 --ai-scouts 1 --pages 25 --pace 1 --seed 11`). After the fixes in [MODEL-EVAL.md](MODEL-EVAL.md#after-fixes), the hunters made 56 claims: 52 proven, 0 rejected, 4 duplicates. They proved every seeded bug on the pages that they read. They made 16 counter-offers and bought 5 tips from the AI scout at 10% or 15%; 4 were proven and 1 was a duplicate. All 31 model calls answered valid JSON. The race took 87 s and spent 0.52 USD of a 1.50 USD AI budget. In the same rerun, `claude-haiku-5-5` proved 54 bugs for 0.014 USD but bought no tip. Before the fixes, `claude-sonnet-5-5` proved 52 bugs in 66 s for 0.23 USD but almost did not trade. [MODEL-EVAL.md](MODEL-EVAL.md) compares all four models, with the local `llama3:latest`.

## Modes

### `simulate` (default)

Simulation needs no money and no model. It can run on a judge's machine.

- It refuses to start unless `GET /api/settlement` reports `"mode": "dry"`.
- Defaults: 100 hunters
  (`links=17,mobile=13,prices=12,images=8,errors=5,generalist=25,careless=10,copycat=10`) and
  10 scouts (`links=3,mobile=2,prices=2,images=1,errors=1,hasty=1`). The specialists follow
  the demo shop's 66 bugs: 20 links, 15 overflows, 15 prices, 10 images and 6 page errors.
- With `--setup`, a fresh implicit owner creates a bounty on the demo site, publishes the proof
  token through the demo's `POST /bounty-proof-control`, and verifies the bounty. The swarm then
  waits until Arsune's site check opens the bounty. The demo shop passes the check.
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

### Site check

Agents spend time, bonds and model tokens. Arsune's checker must be able to read the site, or
every claim is `unverified`. The swarm therefore asks Arsune before it starts an agent:

1. The bounty must be `open`. A paused or closed bounty stops the run at start, with Arsune's
   reason. `--bounty-id` on a bounty that is not open fails at start.
2. The runner calls `POST /api/bounties/{id}/site-check`. Arsune's worker loads the bounty URL
   at 1280 and 390 pixels with the checker's own browser code. The runner reads
   `GET /api/bounties/{id}/site-check` until the state is `passed` or `failed`
   (`site_check_timeout`, 180 s). Arsune keeps a result for 5 minutes, so the check that opened
   a `--setup` bounty is used again.
3. `passed`: the log shows "Site check passed: Arsune loaded <url> completely at 1280 and 390
   pixels wide." Then the agents start.
4. `failed`: no agent starts and no model is called. The log shows "No agent starts: Arsune
   cannot check this site: ..." with what did not load. `/state` has the result in
   `site_check`, and the board shows it. The run exits with status 2. With `--hold`, the
   swarm keeps `/state` served instead.

An external hunter agent can make the same call before it spends tokens. The check runs in
Arsune's worker queue, so `POST` answers 202 first; read the check again until it has a result.

During a race, Arsune pauses a bounty after 5 claims in a row that its own limits left
unverified (docs/ARCHITECTURE.md, "Pause"). The swarm sees the pause on its next read of the
bounty, about one second later. It logs "The bounty is paused: Arsune cannot check this site —
agents stop to save tokens." and then Arsune's reason. The runner stops the model client before
the agents see the stop, so no AI agent makes a model call after that line. Arsune still
settles the claims that it took before the pause.

### Stop conditions and summary

The swarm stops at the first of these:

- The time limit (`--duration`, default 300 s).
- The bounty is no longer `open` (for example closed because the budget is spent, or paused
  because Arsune cannot check the site).
- Every agent is broke or done.
- A stop request (`POST /races/stop` on the [race host](#race-host-serve)). Then the swarm does
  not wait for settlement.

At the stop, AI agents make no more model calls.

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

## Race host (`serve`)

`python -m swarm serve` is a long-running process that lets the web UI start a race. It runs one
race at a time, keeps the state of the last race, and serves the board and a small control API
on one port (`--state-host`, `--state-port`, default `127.0.0.1:8590`). Code:
`backend/swarm/host.py`.

The host only runs simulations. It never starts `live` mode: a request cannot name a mode, the
host sets dry simulation on every race it starts, and a race refuses to start unless Arsune
reports `"mode": "dry"`. Like the agents, the host reaches Arsune only through its HTTP API.

```sh
uv run python -m swarm serve --api http://127.0.0.1:8540
uv run python -m swarm serve --demo-on-start --budget 80000000   # a demo race at once
```

| Option | Meaning |
| --- | --- |
| `--api` | Arsune API base URL (default `http://127.0.0.1:8540`). |
| `--demo-on-start` | Starts one demo race at once, the same as `simulate --setup`, so the board is not empty. |
| `--site`, `--hunters`, `--scouts`, `--budget` | The demo race: bounty site, scripted hunters (100), scripted scouts (10), funding in token units (20 USDC). |
| `--browser-pages` | Shared Chromium page pool of each race (default 6). |
| `--ai-budget` | USD for the model calls of one AI race. A UI race uses at most 0.50 USD. |
| `--quiet` | Does not print events. |

### The race that the UI starts

The profile is a set of constants in `backend/swarm/host.py` (`UI_RACE`, `AI_AGENTS`,
`UI_AI_BUDGET_USD`):

- 12 scripted hunters and 3 scripted scouts with the default strategy mixes. Careless and
  copycat hunters take part, so false claims lose their bond and copies are duplicates.
- Pace 0.5, time limit 240 s, 30 pages per agent, 6 shared browser pages.
- When the swarm's model settings are valid: also 1 AI hunter, with no choice by the user. The
  AI budget is the smaller of `--ai-budget` and 0.10 USD. At most `--ai-races-per-day` races
  (default 50) per UTC day get the AI hunter; after that, races run without AI and `/host`
  shows `ai_note`.

Before any agent starts, the race asks for Arsune's site check, the same as `simulate` (see
[Site check](#site-check)). When the check fails, no agent starts and no model is called. The
race ends, and `/state` shows the refusal.

### Control API

All answers are JSON. An error is `{"detail": "<one sentence>"}`.

`GET /state` is the board, with the shape in [`GET /state`](#get-state). It shows the race that
runs now, or the last race. `bounty_id` is set when the race starts. Before the first race,
`run_id` and `bounty_id` are `null`, `finished` is `true`, and the log has one line.

`GET /host`:

```json
{"busy": false, "bounty_id": "…", "run_id": "3f2a9c1b7d4e", "ai_available": false,
 "ai_active": false, "ai_note": null, "ai_model": null, "ai_max_usd": 0.1,
 "agents": {"hunters": 12, "scouts": 3, "ai_hunters": 1, "ai_scouts": 0}}
```

- `busy` is `true` while a race runs. `bounty_id` and `run_id` are those of the race that
  `/state` shows.
- `agents` is the UI race. `ai_hunters` and `ai_scouts` are what a race adds when `ai_active` is `true`.
- `ai_available` is `true` when the swarm's model settings are valid (the same check as
  `--ai-hunters`; see [AI agents](#ai-agents)). `ai_model` is then the model name. The host
  never shows or logs a key. `ai_active` is `true` when `ai_available` and today's AI races are
  not used up; if not, `ai_note` says "AI agents are resting until tomorrow."

`POST /races` with `Content-Type: application/json` and the body
`{"bounty_id": "<uuid>"}`:

| Answer | When |
| --- | --- |
| 202 `{"run_id": "…", "bounty_id": "…"}` | The race starts. Read `/state` for its progress. |
| 400 | The body is not JSON, has another field, or has a bad value. |
| 404 | Arsune has no bounty with this ID. |
| 409 | A race runs ("A race is already running."), or Arsune shows the bounty as not `open`, or its budget cannot pay one more reward and its fee. The host reads `GET /api/bounties/{id}` first. |
| 502 | Arsune's API did not answer. |

`POST /races/stop` stops the race that runs now and answers 202 with its `run_id` and
`bounty_id`, or 409 ("No race is running.") when no race runs. The agents stop, as at the time
limit, and the log shows "The swarm stops: the race was stopped on request." The host does not
wait for Arsune to settle the open claims; the log tells how many are still open, and the
bounty page shows their results. A stop before the agents start (during the site check) ends
the race at once. `/host` shows `busy: false` when the race has ended.

The UI calls these through its own server (for example a Next rewrite). `GET` answers allow any
origin, as `/state` did before. `POST` needs a JSON content type, so a page on another site
cannot start a race from a browser.

### In Compose

The Compose `swarm` service runs `serve --demo-on-start` with the demo flags (100 hunters, 10
scouts, 80 USDC). `docker compose restart swarm` starts a fresh demo race.

The containers never read `.env`, so AI agents are off in Compose by default. To turn them on,
pass the model settings to the `swarm` service in an override file. Docker Compose reads
`compose.override.yaml` next to `compose.yaml` on its own. Do not commit the file. Put no key
in it: `${...}` takes the value from your shell when you run `docker compose` (Compose also
reads a `.env` file in the project folder for these values).

```yaml
# compose.override.yaml: AI agents on Claude for races that the UI starts
services:
  swarm:
    environment:
      ARSUNE_SWARM_MODEL_PROVIDER: anthropic
      ARSUNE_SWARM_MODEL_NAME: claude-haiku-5-5
      ARSUNE_SWARM_MODEL_INPUT_USD_PER_MILLION: "0.10"
      ARSUNE_SWARM_MODEL_OUTPUT_USD_PER_MILLION: "0.50"
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY in your shell}
```

For an OpenAI-compatible API (NEAR AI Cloud, or a local model), pass
`ARSUNE_SWARM_MODEL_BASE_URL`, `ARSUNE_SWARM_MODEL_NAME`,
`ARSUNE_SWARM_MODEL_API_KEY: ${ARSUNE_SWARM_MODEL_API_KEY}` and both prices instead. A model on
your own machine is at `http://host.docker.internal:<port>/v1` from the container; on Linux,
also add `extra_hosts: ["host.docker.internal:host-gateway"]` to the service.

Then run `docker compose up -d swarm` and read `GET http://127.0.0.1:8590/host`:
`ai_available` must be `true`. If it is `false`, `docker compose logs swarm` shows the reason
in the line that starts with "swarm serve: AI agents are off".

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
        "model": "openai/gpt-5.6-luna"},
 "site_check": {"state": "passed", "reason": "Arsune loaded … completely at 1280 and 390 pixels wide.", "…": "…"}}
```

`site_check` is Arsune's site check from before the agents started (see
[Site check](#site-check)), or `null` before the runner has one. When its `state` is `failed`,
no agent started, and the board shows the reason.

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

- Duplicates follow the final-URL fingerprint, which Arsune takes after redirects. One dead
  target that is linked from several pages is a separate `broken_link` fault for each source
  page.
- Scripted agents call no language model. AI agents call one only within `--ai-budget`.
- Scripted and AI agents claim the five types that Arsune can prove. The careless strategy
  and the hasty scout guess only overflows and broken links. Every agent shows a readable
  label for a claim type it does not know, such as a tip or a copied claim of a new type.
- A proven claim that the remaining budget cannot pay is `unfunded`: Arsune refunds its bond
  and closes the bounty, and the swarm stops. The summary counts it as "proven but unfunded".
  The one-command race funds 80 USDC, which pays all 66 bugs (66 x 1.1 USDC with the fee).
