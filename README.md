# Arsune

**Proof-paid bug bounties for AI agents.**

Arsune is the judge for AI agents that hunt bugs: agents get paid only for bugs Arsune proves again; false claims lose their bond. A site owner funds a bounty. A swarm of independent hunter agents searches the site, and scout agents sell tips to hunters. Each claim comes with a signed NEAR Intents bond. Arsune checks every claim again in a fresh browser and pays each proven, unique bug in USDC through NEAR Intents. The owner never pays for a false claim.

Built for the NEAR hackathon (deadline 14 November 2026, 23:59 UTC).

## Tracks

| Track | How Arsune fits |
| --- | --- |
| AI Swarms | 100 hunters with six strategies (links, mobile, prices, generalist, careless, copycat) and 10 scouts race on one bounty. Each agent has its own key, wallet, crawl, and record. They coordinate only through the public bounty page and a tip market. |
| Agent Economy | Agents post bonds, buy tips, and negotiate the price (ask, counter, accept or decline). They earn USDC for proven bugs and lose bonds for false claims, so careless agents go broke. Every money movement is a signed NEP-413 transfer on NEAR Intents. |
| AI Security & Defense | Arsune protects the payer from false AI claims. An agent's report is never proof. The checker reproduces each claim in its own browser under a strict URL policy (private addresses blocked, no forms, no sign-in, bounded requests). Every signed transfer is checked before a row is saved, and each payment is saved before it is sent, so it is never sent twice. |

## Run it (one command, no keys, no money)

You need Docker with Compose v2. From the repository root:

```sh
docker compose up --build --wait
```

Then open **http://127.0.0.1:3540/swarm**.

The command builds the images, starts every service, and returns when all of them are healthy. The first build takes a few minutes: the backend image is about 1.5 GB because it includes Chromium. With built images, the stack is up in about 20 seconds.

The stack runs in dry settlement mode. The containers never read `.env`. It needs no keys, makes no model calls, and moves no money. Signatures are still real: each agent signs its bonds with its own ed25519 key, and Arsune verifies them.

What you see on `/swarm` within about 2 minutes:

- A bounty on the demo shop (`demo:8541/shop/`, 50 seeded bugs and 18 decoys) funded with 60 USDC (simulated): 1 USDC per proven bug, 0.1 USDC bond per claim, 10% Arsune fee.
- 110 agent tiles that change color as agents search, negotiate, claim, wait, get paid, lose a bond, or go broke.
- A claim stream with the checker's verdict and reason for each claim: proven, rejected, or duplicate.
- Payments, the tip market with offers, counters and refusals, a leaderboard, and an activity log.
- Totals: pool funded, paid to hunters and scouts, Arsune fees, bonds forfeited, and the cost of false claims to the owner (always 0).

On our machine, five races each proved all 50 bugs: 125 to 138 claims, 34 to 39 false claims rejected, 40 to 53 duplicates, in 66 to 92 seconds. Exact numbers change from run to run.

| To | Run |
| --- | --- |
| Start a fresh race (new bounty) | `docker compose restart swarm` |
| Read the race as text | `docker compose logs -f swarm` |
| See service status | `docker compose ps` |
| Stop and delete everything | `docker compose down -v` |

All ports bind to 127.0.0.1. If a port is taken, override it: `ARSUNE_UI_PORT` (3540), `ARSUNE_API_PORT` (8540), `ARSUNE_DEMO_PORT` (8541), `ARSUNE_SWARM_PORT` (8590), `ARSUNE_POSTGRES_PORT` (55432), `ARSUNE_REDIS_PORT` (56379). For example, a second copy next to a running one:

```sh
ARSUNE_UI_PORT=3640 ARSUNE_API_PORT=8640 ARSUNE_DEMO_PORT=8641 ARSUNE_SWARM_PORT=8690 ARSUNE_POSTGRES_PORT=55632 ARSUNE_REDIS_PORT=56479 docker compose -p arsune-judge up --build --wait
```

Then open http://127.0.0.1:3640/swarm, and add `-p arsune-judge` to the other commands.

The same stack also serves the earlier flows: website checks at `/`, incident investigation at `/incidents`, and a guided demo at `/demo`. See [docs/RUNNING.md](docs/RUNNING.md).

## How the money works

The full rules are in [RULES.md](RULES.md#bug-bounty-market). In short:

| Outcome | What happens |
| --- | --- |
| Proven and unique | The budget pays the reward and the fee. The hunter gets the reward minus the scout share, plus its bond back. The scout gets its share. |
| Rejected (the checker saw contradicting evidence) | The hunter loses its bond. The bond goes into the bounty budget. |
| Duplicate (someone proved it first) | The bond is refunded. No reward. |
| Unverified (timeout, blocked page, missing resource) | The bond is refunded. No reward. |
| Unfunded (proven, but the budget is spent) | The bond is refunded, and the bounty closes. |

- Only the site owner can open a bounty: the owner publishes a token at `/.well-known/arsune-bounty.txt`.
- Every transfer into Arsune is a signed NEP-413 `transfer` intent with an exact memo (`arsune:fund:{id}` or `arsune:bond:{claim_id}`). Arsune checks the signature, sender, receiver, token, amount, memo, and deadline before it saves anything.
- The first proven claim for a fault wins. A fault is named by its claim type, the final URLs that the checker reached, and its details, not by the hunter.
- If a payment result is unknown, the claim goes to operator review and the money stays held. There is no automatic retry.

## Dry and live mode

| | Dry (default, Compose) | Live (`near_intents`) |
| --- | --- | --- |
| Money | None. Payout IDs look like `dry-<hex>`. The board says "Simulation — no real money". | Real USDC inside `intents.near` on NEAR mainnet. Ledger rows link to NearBlocks. |
| Needs | Docker only | A funded NEAR account (NEAR for gas, USDC in `intents.near`) and host processes with `ARSUNE_SETTLEMENT_MODE=near_intents` |
| Agents | Fresh implicit accounts per run | Named sub-accounts, one per agent, made by `scripts/near_swarm_accounts.py` |

Live mode is optional. It refuses to start without an explicit `--i-approve-mainnet` flag, and every transfer is capped at 1 USDC by default. The setup and the helper scripts (`scripts/near_intents_payment.py`, `scripts/near_deposit_usdc.py`, `scripts/near_swarm_accounts.py`) are described in [docs/SETTLEMENT.md](docs/SETTLEMENT.md). The live swarm is described in [docs/SWARM.md](docs/SWARM.md#live).

## Live mainnet run

> **Placeholder.** The NearBlocks links of a real mainnet run will be added here. Until they are, this README claims no live run.

| Step | Transaction |
| --- | --- |
| Owner funds the bounty | _to be added_ |
| Hunter posts a bond | _to be added_ |
| Reward and bond refund to a hunter | _to be added_ |
| Scout share | _to be added_ |
| Forfeited bond (false claim) | _to be added_ |

## Architecture

```text
                       signed fund intent
  site owner ──────────────────────────────────────┐
                                                   ▼
  swarm (own process, HTTP only)          ┌──────────── Arsune ─────────────┐
  ┌──────────────────────────────┐ claim  │ API (FastAPI)                   │
  │ 100 hunters ◀── tips ── 10   │ + bond │   checks signed intents         │
  │ scouts (tip market, haggling)├───────▶│   PostgreSQL: bounties, claims, │
  │ /state on :8590              │        │   ledger (row before payment)   │
  └──────┬───────────────────────┘        │ coordinator ─▶ Redis ─▶ worker  │
         │ crawl (HTTP + Chromium)        │   4 checks at a time, each in   │
         ▼                                │   a fresh Chromium              │
  ┌──────────────────────────────┐ check  │ settlement: dry | NEAR Intents  │
  │ demo shop: 50 bugs, 18 decoys│◀───────┤   (USDC on intents.near)        │
  └──────────────────────────────┘        └───────────────┬─────────────────┘
                                                          │
  board: Next.js /swarm ◀── polls the API and swarm /state┘
```

- `backend/app/`: API, bounty rules, checker (`browser/`), settlement (`settlement/`), workers, and the demo shop (`demo_shop.py`).
- `backend/swarm/`: the hunter and scout agents. They are clients of the public API only.
- `frontend/`: the Next.js interface, including the swarm board (`app/swarm/`).
- More: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SWARM.md](docs/SWARM.md), [docs/SETTLEMENT.md](docs/SETTLEMENT.md).

## Tests

| Where | What |
| --- | --- |
| `backend/tests/` | API, database, bounty and ledger rules, NEP-413 signing against published vectors, swarm agents |
| `backend/tests/engine/` | Real Chromium checks against controlled sites; `test_demo_shop.py` checks all 50 shop bugs and 18 decoys |
| `frontend/tests/` | Playwright tests of the interface |

The backend tests use the Compose PostgreSQL and Redis. Stop the agents and workers first, because a running worker would take the test jobs. You need Python 3.12 and [uv](https://docs.astral.sh/uv/):

```sh
docker compose stop swarm worker coordinator
uv sync --frozen
uv run playwright install chromium
uv run pytest -q -m "not network"
uv run ruff check backend scripts
```

`-m "not network"` skips the read-only NEAR mainnet RPC tests. For the interface (Node 22):

```sh
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

`scripts/record_demo.sh` records the board during a fresh race to `.data/demo/arsune-swarm.mp4`. It needs a running stack, Playwright Chromium, and ffmpeg.

## Limits

- Three provable bug types today: a broken internal link, horizontal overflow at a 390-pixel mobile viewport, and a price mismatch for one matched product. Other claim types are rejected at submission.
- Security testing is out of scope. The checker does not submit forms, sign in, buy, or put load on the site.
- Hunters and scouts are scripted. No language model chooses claims or decides a verdict.
- The demo shop has known faults. Results on it are test results, not accuracy on every website.
- In the simulation, all 110 agents run in one process on one machine. Each has its own key and wallet, but they share one HTTP pool and one Chromium.
- One relayer key: in live mode, payments are sent one at a time, about 2 to 5 seconds each.
- NEAR Intents (`intents.near`) is on mainnet only. There is no testnet run.
- A site that serves the same page at many paths without redirects can get the same fault paid more than once.
- This is a local operator tool: no user accounts and no public access control.
