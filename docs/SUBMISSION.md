# Submission texts

Texts for the NEAR hackathon form, X posts, and the demo video. Deadline: 14 Nov 2026, 23:59 UTC.
Every fact comes from the repo ([README.md](../README.md), [BUSINESS.md](BUSINESS.md)). Items that are not true yet are marked TODO.

## 1. One-line pitch

AI builds sites fast and bugs ship with them, so Arsune lets agents race to find the bugs and pays only for proof.

## 2. Form descriptions

### Short description

Arsune is a proof-paid bug race on NEAR. An owner funds a pool. Agents claim bugs on the owner's site, and each claim locks a bond. Arsune checks every claim again in a fresh browser, with no AI model. It pays proven bugs in USDC. False claims lose their bond.

### Long description (under 300 words)

AI builds sites fast. Bugs ship with them. Arsune lets agents race to find those bugs, and the owner pays only for proof.

An owner starts a race on a site and funds a bounty pool. Hunter agents search the site and claim bugs. Scout agents sell tips to hunters. Each claim locks a bond, a small deposit signed through NEAR Intents. The size of the bond follows the agent's record.

Arsune then checks every claim again in a fresh browser. The checker is plain browser code. It has no AI model, so the verdict is the same each time. 

The outcome of a claim:
- Proven and unique: the agent is paid in USDC.
- False: the agent loses its bond. The bond goes into the owner's pool, not to Arsune.
- Repeat, or a claim that Arsune could not check: the bond comes back.

Arsune's fee is a percentage of the reward, only on proven bugs.

Other entries judge "proof of done": they check that an agent finished a task, with an AI reviewer, a jury, hidden tests, or an attested enclave. Arsune is different. It targets bugs that real visitors hit on websites. It trusts no report and no judge. It pays only for a bug that it proves again itself, and anyone can re-run the verdict with plain code. The first proven, unique claim wins. A false claim costs the agent, not the owner.

The race works today. In the demo, 100 hunters and 10 scouts race on a shop with 66 seeded bugs and 26 decoys. In the last full run, all 66 bugs were proven and paid once, and no decoy was paid. The demo runs in dry mode: signatures are real, but no money moves. TODO: a live mainnet run.

## 3. X post drafts

Each post has `@NEARProtocol`, `#NEARHackathon`, and a `[video]` placeholder.

Post 1:

```
AI builds sites fast. Bugs ship with them.
Arsune lets agents race to find them. You pay only for proof: every claim is checked again in a fresh browser, by plain code. No AI judge, no enclave to trust.
@NEARProtocol #NEARHackathon
[video]
```

Post 2:

```
Agents lock a bond on every bug claim. Proven bug: paid in USDC. False claim: the bond goes to the owner's pool. Repeat: bond back. Arsune's fee is only on proven bugs. Built on NEAR Intents.
@NEARProtocol #NEARHackathon
[video]
```

Post 3:

```
Most agent judges score "the task is done". Arsune pays only for the first bug it proves again itself, in a fresh browser. A false claim loses its bond to the owner's pool. Demo: 66 of 66 bugs proven, 0 decoys paid.
@NEARProtocol #NEARHackathon
[video]
```

## 4. Video script (about 80 seconds)

Use the demo stack (`docker compose up --build --wait`). Record with the pages "New hunt", "My hunts", and "Watch live".

| Time | Shot | Voice-over |
| --- | --- | --- |
| 0:00-0:12 | Start page (`/`): the headline and the three steps. | "AI builds sites fast. Bugs ship with them. Let agents race to find them. Pay only for proof." |
| 0:12-0:25 | Click "Try the demo shop". Fill the owner account. Click start. | "I start a race on the demo shop. Agents will search it. Each claim locks a bond, a small deposit." |
| 0:25-0:50 | "Watch live" page (`/swarm`): agent tiles change color, dots move. Show the story line. | "100 hunters and 10 scouts race. Arsune checks each claim in a fresh browser. No AI model decides." |
| 0:50-0:62 | Same page: "Who won" and the totals. | "Proven bugs are paid in USDC. False claims lose their bond. The owner pays nothing for them." |
| 0:62-0:72 | "My hunts" (`/races`), then the hunt page: the result story ("Your hunt found ... bugs"). | "The owner sees a plain result: bugs found, bugs paid, and false claims that cost nothing." |
| 0:72-0:82 | Click an evidence link on a proven claim. Show the hash. | "Every ruling has public evidence. Anyone can check it again." |
| 0:82-0:88 | End card: repo URL, `@NEARProtocol`. | "Arsune. Pay only for proof." |

Notes:
- The "Watch live" and result story lines come from the UI spec ([simplify-ui](superpowers/specs/2026-11-12-simplify-ui.md)). TODO: check the final wording on the running stack before recording.
- Say "simulation" once. The demo moves no money.
- TODO: record the video and add its URL.

## 5. Judge checklist

### Live demo

Open https://arsune.augevi.com, select New hunt, then "Try the demo shop" and "Find bugs on my site". Set the proof with the demo button, add simulated money, and select "Start the hunt". Then open "Watch live". The public demo uses simulation money (no real money moves), 15 agents per race, one race at a time, and no AI agents.

### Run it with no keys and no money

You need Docker with Compose v2.

```sh
docker compose up --build --wait
```

Open http://127.0.0.1:3540/swarm. The first build takes a few minutes. With built images, the stack is up in about 20 seconds. The containers never read `.env`. They use dry settlement, make no model calls, and move no money. Signatures are still real.

The race starts by itself and ends in about 2 minutes. To start a new demo race, run `docker compose restart swarm`. To clean up, run `docker compose down -v`.

### What to look at

1. Start page (`/`): the story and the three steps. Header links: "New hunt", "My hunts", "Watch live".
2. Watch live page (`/swarm`): agent tiles, the claims list with each verdict and reason, "Who won", and "More details".
3. Totals: the cost of false claims to the owner is always 0.
4. Last full run (9 Nov 2026): 226 claims, 66 of 66 bugs proven and paid once, 0 decoys paid, 39 false claims rejected, 81 duplicates, 40 unverified. Numbers change a little from run to run.
5. Evidence: open `/api/claims/<claim_id>/evidence` on port 8540. Its SHA-256 equals the claim's `evidence_hash`. Run `docker compose exec api python -m app.verify http://127.0.0.1:8540 <claim_id>` to check the ruling again in a fresh browser.
6. The checker has no AI model. See [docs/BUSINESS.md](BUSINESS.md) (cost table) and the "Limits" section of the README.
7. Money rules: [RULES.md](../RULES.md#bug-bounty-market). Bond sizes follow a reputation tier.
8. Track fit: AI Swarms, Agent Economy, AI Security & Defense (README, "Tracks").

### Known limits

- Five provable bug types only.
- The demo shop has known faults. Results on it are test results, not accuracy on every website.
- Escrow contract: tested on a local sandbox, not deployed on mainnet, not audited.
- TODO: live mainnet run. The README table of NearBlocks links is still empty.
- The monthly price of $10 per site is planned and not built.
