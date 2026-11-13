# Business model

How Arsune makes money, what it costs to run, and how many sites it needs to break even.
The cost numbers come from 1,066 claims on the test stack (12 Nov 2026). Numbers marked
*estimate* are not measured yet.

## Who pays what

| Who | Pays | Gets |
| --- | --- | --- |
| Site owner | A monthly price per site, and the bounty pool (the rewards) | Proven bugs, each with evidence that anyone can re-run |
| Hunter or scout agent | A bond for each claim (a deposit) | The bond back and the reward for a true, new bug |
| Arsune | The cost to check claims and to move money | The monthly price and a fee on each proven bug |

## Arsune's income

1. **Monthly price per site: $10 per site per month.** *(Planned, not built.)* It pays for the
   servers, so Arsune does not depend on how many bugs a race finds. The owner can run a race
   after each change to the site. The rewards still come from the owner's bounty pool.
2. **Fee on each proven bug: 10% of the reward** (`fee_bps`, default 1000). The pool pays the
   fee on top of the reward. Arsune takes no fee on false claims, repeats or unchecked claims.

Arsune does not keep lost bonds. A lost bond goes into the owner's bounty pool. So Arsune gets
nothing from a wrong "false" verdict, and it earns only when a bug is true.

## Arsune's costs

| Cost | Measured value | Cost |
| --- | --- | --- |
| Browser check of a claim | 3.4 s typical on a real site (7.5 s max); 1.9 s on the demo shop | ≈ $0.00003 per check (one server at $0.10/hour, 4 checks at the same time) |
| Repeat claim | 0.05 s, a database lookup, no browser | ≈ $0 |
| AI model in the checker | None: the check is plain browser code | $0 |
| Evidence storage | ≈ 70 KB per claim | ≈ $0 |
| NEAR transactions (bond in, then payout or refund) | ≈ 2 per claim | ≈ 0.001 NEAR each *(estimate)*, ≈ $0.006 per claim at 3 USD/NEAR *(assumed price)* |
| Servers (API, worker, database, queue) | Fixed | ≈ $50–100 per month *(estimate)* |
| Escrow contract (optional mode) | One time | ≈ 2.31 NEAR to deploy |
| Collecting the monthly price | In USDC through NEAR Intents, or by card | ≈ $0 in USDC; ≈ $0.59 per $10 by card (2.9% + $0.30) |

The main cost per claim is the blockchain fee, not the check. In the test mix (40 true,
33 repeats, 20 false, 7 unchecked per 100 claims), each true bug carries ≈ 1.4 cents of
transaction cost, including the false claims and repeats around it.

In the demo, Arsune also runs its own AI hunters and pays their model cost (≈ $0.19 for
14 claims on the staging site). In the real market, hunters pay their own costs.

## Break-even

Assumptions: servers $75 per month, 7 true bugs per site per month (one staging race found 7),
fee 10%, transaction cost 1.4 cents per true bug.

| Plan | Owner pays per month | Arsune gains per site | Sites to break even |
| --- | --- | --- | --- |
| Fee only, 1 USDC | ≈ $8 (no monthly price) | $0.60 | ≈ 125 |
| Fee only, 5 USDC | ≈ $39 (no monthly price) | $3.40 | ≈ 22 |
| $10/month + 1 USDC | ≈ $18 | $10.60 | **≈ 7** |
| $10/month + 5 USDC | ≈ $49 | $13.40 | **≈ 6** |
| $10/month, no bugs found | $10 | $10.00 | ≈ 8 |

With the monthly price, about **8 paying sites** pay for the servers, even when the races
find no bugs. The fee is then extra income that grows with the number of true bugs.

## Risks

- **Bugs run out.** When the owner fixes the bugs, the next race finds fewer. The monthly
  price keeps the servers paid, and the sales message is "run a race after every deploy".
- **Small rewards.** With a 0.10 USDC reward the fee is 1 cent, less than the transaction cost.
- **Spam and false claims.** Each one costs a browser check and transactions, and the lost bond
  goes to the owner, not to Arsune.
- **NEAR price and gas.** The transaction cost follows the NEAR price. Measure it on the first
  mainnet run.

## Open decisions

- A minimum fee (for example 10%, but never less than 5 cents), so small rewards do not lose money.
- A small check fee on every claim (for example 0.01 USDC, true or false), to pay for spam.
  It keeps Arsune neutral: the same income for any verdict.
- One blockchain transaction for many payments, to cut transaction cost.
