# Bounty escrow contract

The escrow is a NEAR contract that holds bounty money and hunter bonds. Arsune is still the
judge: it decides if a claim is proven, because that needs a real browser. But the contract
fixes every amount and every receiver. Arsune cannot take bounty money, and it cannot send
money to an account of its choice. Every ruling is a public record with a hash of Arsune's
evidence.

- Contract: `contracts/escrow/` (Rust, near-sdk 5.30.1).
- Sandbox tests: `contracts/escrow/integration-tests/` (near-workspaces 0.24, real `intents.near` code).
- Python client: `backend/app/settlement/escrow.py`. The bounty flow uses it in
  `ARSUNE_SETTLEMENT_MODE=escrow` (opt-in; see [SETTLEMENT.md](SETTLEMENT.md#escrow-mode)).
- Deploy script: `scripts/near_escrow_deploy.py`.
- Status: tested in a local sandbox only, also end to end with the Arsune API and worker (see
  "End-to-end run on a local sandbox"). Not deployed on mainnet. Not audited.

## Where the money is

The money stays inside NEAR Intents (`intents.near`), as today. The escrow account holds USDC
(`nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1`) as its own
NEP-245 balance on `intents.near`. The contract keeps the books: which part of that balance
belongs to which bounty, which bonds are held, and which payouts are owed.

`get_config().liabilities` is the sum of all bounty balances, held bonds and owed payouts.
`intents.near.mt_balance_of(escrow, token)` is never less than this sum. Anyone can compare the
two values.

## Deposits: verified behavior of intents.near

Source: near/intents at rev `16d04f6e`, the exact code of `intents.near` on mainnet (defuse
0.4.5; `contract_source_metadata` gives this rev and a reproducible build; the code hash
`BtA1BEFNS619KkvXFsgcpQ21Tb5yMm32aaUkn7xNothk` matched on 2026-11-09).

There are two ways to deposit. Both call `mt_on_transfer` on the escrow:

1. **`mt_transfer_call`** (`contracts/defuse/src/contract/tokens/nep245/core.rs`). A depositor
   with a NEAR account calls `intents.near.mt_transfer_call(receiver_id, token_id, amount, msg)`
   with 1 yoctoNEAR. `sender_id` is the caller.
2. **A signed `transfer` intent with `msg`** (`contracts/defuse/core/src/intents/tokens.rs`).
   `Transfer` has an optional flattened `NotifyOnTransfer { msg, min_gas }`. If `msg` is set,
   the Verifier calls `mt_on_transfer` on the receiver. `sender_id` is the signer of the
   intent. `min_gas` is 30 Tgas by default and at least 5 Tgas. The hunter signs (NEP-413 or
   ERC-191), and Arsune relays with `execute_intents`. The hunter needs no NEAR.

In both cases the tokens move to the escrow first. Then `mt_on_transfer` runs, and then
`mt_resolve_transfer` (`contracts/defuse/src/contract/tokens/nep245/resolver.rs`):

- The receiver returns a list of amounts to refund. The Verifier refunds them to the sender.
- If the receiver panics or returns malformed data, the Verifier refunds the full amount.
- A refund is limited to the receiver's balance at that moment.

So the escrow refuses a deposit by returning the full amount, with a `refund` event that gives
the reason. It does not change any state on that path. The escrow accepts `mt_on_transfer` only
from the configured intents contract, so nobody can fake a deposit.

In the sandbox, the escrow's `mt_on_transfer` for a bond burnt about 1 Tgas, far below the 5
Tgas minimum.

A plain `mt_transfer` or a `transfer` intent without `msg` does not notify the escrow. Such
tokens belong to no bounty (see "Open gaps").

### Deposit messages

`msg` is JSON. Unknown keys and other forms are refunded.

| Deposit | `msg` |
| --- | --- |
| Fund a bounty (anyone) | `{"fund":"<bounty_id>"}` |
| Post a bond | `{"bond":{"bounty_id":"<id>","claim_id":"<id>"}}` |
| Post a bond that names a scout | `{"bond":{"bounty_id":"<id>","claim_id":"<id>","scout":{"account_id":"<scout>","share_bps":2500}}}` |

IDs have 1 to 64 characters: `a-z`, `0-9`, `-` and `_`. Arsune's lowercase UUIDs fit.

## Bounty terms

`create_bounty(bounty_id, owner, judge, fee_account, reward, base_bond, fee_bps,
scout_share_max_bps, deadline_ns, rule_timeout_ns)`. The terms cannot change after creation.

| Rule | Limit |
| --- | --- |
| Who can create | The owner or the judge (Arsune can create for the owner with the owner's terms) |
| `reward`, `base_bond` | 1 to 10^30 smallest units |
| `fee_bps` | 0 to 2000 (fee = `reward * fee_bps / 10000`, rounded down) |
| `scout_share_max_bps` | 0 to 5000 |
| `deadline_ns` | In the future (nanoseconds, block time) |
| `rule_timeout_ns` | 60 s to 30 days (Arsune default: 24 h) |
| Parties | The owner, judge and fee account cannot be the escrow or the intents contract |
| Storage | The caller attaches NEAR for the bounty record and at least one claim |

### Storage

A hunter's bond arrives from `intents.near`, so it cannot carry NEAR for storage. The creator
of a bounty therefore attaches NEAR for its claims:

- The bounty record is measured. The rest of the deposit is the bounty's `storage_pool`.
- Each bond takes 600 bytes (0.006 NEAR) from the pool. This is an upper bound: a unit test
  measured 565 bytes for a decided claim with 64-character IDs and account names.
- When the pool cannot pay for one more claim, new bonds are refunded (`no_storage`).
- `withdraw_remaining` sends the unused pool back to the creator. The storage of claim records
  stays paid, because the records are the public trust record.

`escrow.storage_deposit(claims)` gives the deposit: 0.907 NEAR for 150 claims.

## Bonds

A bond is accepted only if all of these are true. Otherwise it is refunded with the reason.

| Check | Refund reason |
| --- | --- |
| The token is the configured USDC, one token, amount above 0 | `wrong_token`, `zero_amount` |
| `msg` parses | `bad_msg` |
| The claim ID is valid and not used before (on any bounty) | `bad_claim_id`, `claim_exists` |
| The bounty exists, is open, and the deadline has not passed | `unknown_bounty`, `bounty_closed`, `deadline_passed` |
| The balance covers one reward and its fee (RULES.md) | `budget_too_low` |
| `ceil(base_bond / 2) <= bond <= 3 * base_bond` (the `trusted` to `very_poor` multipliers) | `bond_out_of_range` |
| The hunter is not the judge | `bad_hunter` |
| The scout share is 1 to `scout_share_max_bps`; the scout is not the judge or the escrow | `bad_scout` |
| The storage pool can pay one more claim | `no_storage` |

The contract does not know the hunter's reputation. Arsune still calculates the exact bond.
The contract makes sure that the bond is inside the range of the published multipliers.

## Rulings

`rule(claim_id, outcome, evidence_hash)` can be called only by the bounty's judge, and only
once for each claim. `evidence_hash` is the SHA-256 (64 lowercase hex characters) of Arsune's
evidence bundle. The contract stores it and puts it in the `rule` event.

| Outcome | Money (enforced by the contract) |
| --- | --- |
| `proven` | If the balance covers `reward + fee`: the balance pays both. The hunter gets `reward - scout_part + bond`, the scout gets `scout_part = reward * share_bps / 10000` (rounded down), and the fee account gets the fee. If the balance does not cover it, the outcome becomes `unfunded`. |
| `rejected` | The bond goes into the bounty balance. |
| `duplicate`, `unverified` | The bond goes back to the hunter. |
| `unfunded` | The bond goes back to the hunter. The judge can use it only when the balance cannot pay `reward + fee`. |

- The `rule` event has `ruling` (what the judge asked) and `outcome` (the result).
- After a paid claim, if the balance cannot pay one more reward and fee, the bounty closes
  (`budget_spent`). An unfunded claim closes the bounty (`unfunded`). This is the rule in
  RULES.md.
- A `fund` deposit to a bounty that closed for `budget_spent` or `unfunded` is kept. When the
  balance then pays one reward and its fee, the bounty opens again (`reopen` event). This is
  RULES.md "Closing and top-ups". A close by the owner or by the deadline is final: a deposit
  after it is refunded (`bounty_closed`).
- Payout memos are `arsune:reward:<claim>`, `arsune:scout:<claim>`, `arsune:fee:<claim>` and
  `arsune:refund:<claim>`.

## Liveness and closing

| Call | Who | When | Money |
| --- | --- | --- | --- |
| `reclaim_bond(claim_id)` | Anyone | The claim is open and `rule_timeout_ns` has passed since the bond | The bond goes to the hunter. The outcome is `expired`. |
| `close_bounty(bounty_id)` | The owner, with 1 yoctoNEAR | The bounty is open, or closed for `budget_spent` or `unfunded` | None. The close is final: new funds and bonds are refunded. Open claims are still ruled. |
| `withdraw_remaining(bounty_id)` | Anyone | The owner closed the bounty or the deadline passed, and no claim is open | The balance goes to the owner. The unused storage NEAR goes to the creator. |

A close for `budget_spent` or `unfunded` does not allow `withdraw_remaining` before the
deadline. A top-up can still open such a bounty, and it then needs its storage pool. The owner
calls `close_bounty` to make the close final and take the rest early.
| `retry_payout(account_id)` | Anyone | Money is owed to the account | The owed amount goes to that account. |

A call that anyone can make has a fixed receiver. This lets a hunter or an owner that has only
an Ethereum (0x) account get its money: any account can start the transfer. After the
deadline, anyone can clear stale claims with `reclaim_bond` and then send the remaining balance
to the owner.

## Failed payouts

Each payout is `intents.near.mt_transfer(receiver, token, amount, memo)` with 1 yoctoNEAR and
20 Tgas, then a private callback `on_payout` (10 Tgas).

- If the transfer fails (for example, the Verifier paused `mt_transfer`), the callback adds the
  amount to `owed[receiver]` and logs `payout_failed`. The money is not lost and cannot be
  spent twice. `retry_payout` sends it later.
- The callback cannot panic, so this rollback is always saved.
- The ruling itself stays. The judge cannot rule the claim again.

## Trust model

**Arsune (the judge) can:**

- Decide the outcome of each claim once, and choose the evidence hash.
- Create bounties for owners. The owner must check the terms (`get_bounty`) before funding.
- Start `withdraw_remaining`, `reclaim_bond` and `retry_payout` like anyone else.

**Arsune (the judge) cannot:**

- Withdraw bounty money, or send money to itself or to an account of its choice. Each receiver
  comes from the terms (owner, fee account) or from the hunter's signed bond (hunter, scout).
- Change the terms, the reward, the fee, the scout share or the bond of a claim.
- Rule a claim twice, or rule after the hunter reclaimed the bond.
- Call a funded proven claim `unfunded`.
- Post bonds or be a scout on its own bounties.
- Keep a bond forever: after the timeout, anyone can return it to the hunter.
- Upgrade the contract. There is no owner, no admin and no upgrade method, and the deploy
  script creates the escrow account without an access key.

**What remains a matter of trust:**

- The judge decides the facts. A judge that works with a hunter can call false claims
  `proven` and pay rewards from the bounty, or call true claims `rejected`. The evidence hash
  makes each such ruling visible and checkable after the fact. It does not prevent it.
- When the judge is also the fee account (Arsune's case), the judge earns a fee only for
  proven claims. This is the existing business model; the evidence hash makes it auditable.
- `intents.near` holds the tokens. Its DAO roles can pause transfers, lock accounts and force
  transfers (`mt_force_transfer`, `mt_force_withdraw` exist in the code). The escrow is as safe
  as NEAR Intents itself, and no safer.

## Events

NEP-297 logs: `EVENT_JSON:{"standard":"arsune-escrow","version":"1.0.0","event":...,"data":{...}}`.

| Event | Data |
| --- | --- |
| `create` | `bounty_id`, `owner`, `judge`, `fee_account`, `reward`, `base_bond`, `fee_bps`, `scout_share_max_bps`, `deadline_ns`, `rule_timeout_ns` |
| `fund` | `bounty_id`, `sender_id`, `amount`, `balance` |
| `bond` | `bounty_id`, `claim_id`, `hunter`, `amount`, `scout` |
| `rule` | `bounty_id`, `claim_id`, `judge`, `ruling`, `outcome`, `evidence_hash`, `hunter_amount`, `scout_amount`, `fee_amount`, `forfeited` |
| `expire` | `bounty_id`, `claim_id`, `hunter`, `bond` |
| `payout` | `receiver_id`, `amount`, `memo` (logged when the transfer is confirmed) |
| `payout_failed` | `receiver_id`, `amount`, `memo`, `owed` |
| `refund` | `sender_id`, `token_ids`, `amounts`, `reason` (a deposit was returned) |
| `close` | `bounty_id`, `reason` (`owner`, `budget_spent`, `unfunded`, `deadline`) |
| `reopen` | `bounty_id`, `balance` (a top-up opened a bounty that closed for a spent budget) |
| `withdraw` | `bounty_id`, `owner`, `amount`, `storage_refund` |

Amounts are decimal strings (U128). Views: `get_config`, `get_bounty`, `get_claim`,
`list_claims(bounty_id, from_index, limit)` (at most 100 per page), `get_owed`, and the NEP-330
`contract_source_metadata`.

## How anyone checks a ruling

1. Read the ruling: `get_claim(claim_id)` or the `rule` event of the transaction. It gives the
   outcome and `evidence_hash`.
2. Get Arsune's evidence bundle for the claim: `GET /api/claims/{claim_id}/evidence`. The API
   serves the stored bytes unchanged. `GET /api/claims/{claim_id}` gives the claim with the
   same `evidence_hash`.
3. Hash it: the SHA-256 of the response body must equal `evidence_hash`
   (`curl -s <api>/api/claims/<id>/evidence | sha256sum`). The body is canonical JSON: compact,
   sorted keys, UTF-8 (`escrow.canonical_json`), so `escrow.evidence_hash(bundle)` of the parsed
   bundle gives the same hash.
4. Run the check again: the bundle has the claim, the URLs, the viewports, the observations and
   the checker's result. Screenshots are named by their sha256. Any browser can open the same
   pages. A different result shows a wrong ruling, and the chain shows who made it and when.

`uv run python -m app.verify <api_url> <claim_id>` does all four steps (README, "Anyone can
verify a ruling"). It reads the escrow with read-only RPC views only. A site that changed after
the ruling also gives a different fresh result, so a mismatch is a reason to look, not a proof.

## Build

```sh
contracts/escrow/build.sh    # Docker only; prints sha256, code hash and size
contracts/escrow/test.sh     # fmt, clippy, unit tests, build, sandbox tests
```

- The build uses `sourcescan/cargo-near:0.22.0-rust-1.97.1@sha256:7467038b…` and
  `cargo near build non-reproducible-wasm --locked`: the same image and command that built
  `intents.near`. `ESCROW_CACHE_DIR` keeps the cargo caches (default `/tmp/arsune-escrow-cache`).
- Tests run in a local image made from it, with OpenSSL headers (near-workspaces), clippy and
  rustfmt. Nothing is installed on the host.
- For a build that explorers can verify, commit and push, then run
  `cargo near build reproducible-wasm` in `contracts/escrow`. `Cargo.toml` has the
  `reproducible_build` section. That build also embeds the commit, so its hash differs.

Result on 2026-11-09 (after the top-up change):

| | |
| --- | --- |
| wasm | `contracts/escrow/target/near/arsune_escrow.wasm` |
| size | 224,153 bytes (ABI embedded, 2.7 KB of it; `opt-level = "z"` was the smallest) |
| sha256 | `e556b06aed47fd4a26bb86932c4c7869aa62f33e57c9e170a043e3411f954b18` |
| code hash | `GSF2obxd3HuyheiNTE69wB3yDTh7Z9vREmDFi24Yr14s` |
| Reproducible | The first build (223,323 bytes, before the top-up change) gave the same bytes again from an empty cache. The current build was not built twice. |

## Test evidence

**Unit tests (72, `src/tests.rs`):** every limit of `create_bounty`; funding; every refund
reason; the bond range; one bond per claim ID; the budget check; the storage pool; the storage
bound of one claim; each outcome with exact amounts and memos; scout rounding; unfunded and the
close reasons; top-ups that open a bounty after `budget_spent` and `unfunded`, a smaller top-up
that keeps it closed, and the final owner close; judge-only and once-only rulings; `expired`
and bad hashes refused; reclaim before and after the timeout; close and withdraw rules; the
withdrawal receivers; failed payouts to `owed` and the retry; paging; and the liabilities
invariant.

**Sandbox tests (11, `integration-tests/tests/sandbox.rs`).** They use near-workspaces 0.24 and
nearcore sandbox 2.14.0, the mainnet version. They run the real `intents.near` wasm, read from
mainnet with `view_code` and checked against its code hash. The test USDC is the near/intents
`fungible-token.wasm`, deposited into the Verifier with `ft_transfer_call`. The escrow is
locked: its access key is deleted before the tests.

| Test | What it shows |
| --- | --- |
| `fund_bond_and_proven_pay_hunter_scout_and_fee` | Exact balances for hunter, scout, fee account and escrow. `rule` with three payouts burnt 20 Tgas. |
| `signed_transfer_intent_with_msg_posts_a_bond` | A NEP-413 signed `transfer` with `msg`, relayed with `execute_intents`, calls `mt_on_transfer` with the signer as the hunter. A bad `msg` is refunded in full. |
| `rejected_duplicate_and_unverified_settle_bonds` | Forfeit into the balance; refunds. |
| `proven_without_budget_becomes_unfunded` | Bond refund, no fee, bounty closed, new bonds refunded. |
| `top_up_reopens_a_bounty_whose_budget_ran_out` | After `budget_spent`, a small top-up is kept and bonds are still refunded; a top-up to one reward and fee opens the bounty (`reopen`) and the next claim is paid. No withdrawal before the owner's close; after it, a top-up is refunded and the withdrawal works. |
| `only_the_judge_rules_and_only_once` | Owner, hunter, scout and others are refused. A second ruling is refused. No balance changes. |
| `hunter_reclaims_bond_after_judge_timeout` | Refused before the timeout; after it, any caller returns the bond to the hunter. |
| `remaining_money_goes_only_to_the_owner_after_deadline` | Refused before the deadline for the judge and the owner. After it, the judge starts the withdrawal, but the money goes to the owner and the judge's balance does not change. Storage NEAR is refunded. |
| `unexpected_deposits_are_refunded` | Bad JSON, extra keys, unknown bounty, bond out of range, judge as scout or hunter, another token: all refunded in full. A forged `mt_on_transfer` is refused. |
| `failed_payouts_roll_back_into_owed_and_retry_pays` | With `mt_transfer` paused on the Verifier, the three payouts fail and become owed. A retry while paused stays owed. After unpause, the retries pay the exact amounts. |
| `escrow_is_locked_and_callbacks_are_private` | `on_payout` and `new` are refused, the account has no key, and a plain transfer adds surplus that no bounty owns. |

The sandbox tests also check that the escrow's Verifier balance equals its liabilities (plus the
surplus of the plain transfer in the last test).

**Python (48, `backend/tests/test_escrow_client.py`, fake RPC):** terms and limits, deposit
messages, NEP-413 and ERC-191 signed deposits and their checks, the refusal of `msg` by the
existing Arsune transfer check, evidence hashing, call arguments, gas and deposits, refund and
event handling, and the deploy script offline (plan, refusals, one locked transaction).

**Escrow mode (6, `backend/tests/test_settlement_escrow_mode.py`, in-memory chain):** the bounty
is created once with Arsune's terms; deposits need the escrow `msg`; bonds name the scout; the
rule transaction is recorded on the reward, bond, scout and fee rows; a forfeit enters the
budget only after the rule; an unknown rule result is never sent again; an escrow outcome that
differs goes to review; no near_intents bond cap; withdraw closes for good; other terms on the
escrow stop the opening; `app.verify` compares the bundle with the escrow record; swarm agents
sign escrow deposits.

## End-to-end run on a local sandbox (2026-11-09)

A local nearcore sandbox node (2.14.0, the binary that near-workspaces uses) ran the test
USDC, the real `intents.near` wasm (defuse 0.4.5) and this escrow wasm, locked with no access
key. The Arsune API, coordinator and worker ran in `escrow` mode against it, on the controlled
demo site, with a real browser check for each claim. Owner and hunters were implicit accounts
that signed NEP-413 deposits; Arsune relayed them.

1. `POST /verify` created the bounty on the escrow (judge and fee account Arsune, 10 claims of
   storage).
2. The owner's signed deposit with `{"fund":...}` funded 5 USDC.
3. A broken link with a 25% scout: `rule proven`. A link that works: `rule rejected`. The
   same broken link again: duplicate at arrival, `rule duplicate`.
4. The owner signed `close_bounty`; `POST /withdraw` sent the rest to the owner.

USDC changes inside `intents.near`: owner -5.00 then +4.00; first hunter +0.75 (reward 1.00
minus scout 0.25; bond back); scout +0.25; Arsune +0.10 (fee); false hunter -0.10 (bond);
duplicate hunter 0. Before the withdrawal, the escrow held 4.00 USDC, equal to its
`liabilities` and to Arsune's budget for the bounty; after it, 0. Each claim's escrow record had
the outcome and the `evidence_hash` that Arsune published, and `app.verify` reproduced all three
rulings (the README shows one output). `scripts/sandbox_escrow_e2e.py` repeats this run; its
docstring gives the steps. It refuses any node whose validator is not the sandbox `test.near`.

## Deployment cost

`scripts/near_escrow_deploy.py` makes one transaction: `CreateAccount escrow.arsune-swarm.near`,
`Transfer` (storage), `DeployContract`, `FunctionCall new`. It has no `AddKey`, so the account
never has a key. The swarm parent signs the actions as a NEP-366 delegate action. Arsune
relays them and pays the gas and the transfer. Without `--i-approve-mainnet` the script only
reads the chain and prints the plan. It refuses if the name exists, the parent key is missing,
the wasm exports differ from the expected list, the total is above `--max-near` (3 NEAR), or
Arsune holds too little NEAR. After the deploy it checks the code hash, that the account has no
keys, and the config.

Costs with the 224,153-byte wasm, the live runtime config (protocol 88) and a gas price of
1e8 yoctoNEAR (computed with the script's own cost functions, offline):

| Item | NEAR |
| --- | --- |
| Storage of the code (100 KB = 1 NEAR) | 2.241530 |
| Storage of the account record and state (500 bytes, upper bound) | 0.005000 |
| Spare NEAR in the escrow (1 yoctoNEAR per payout, owed records) | 0.050000 |
| Account creation charge | 0.007000 |
| Gas, upper bound (61.0 Tgas) | 0.006096 |
| **Total spent by Arsune** | **2.309626** |
| Held up front for gas, refunded down to use | 0.060963 |
| **Arsune must hold** | **2.370589** |

Each bounty then costs its creator 0.006 NEAR for each claim record (0.907 NEAR is attached for
150 claims; the unused part comes back at `withdraw_remaining`). In escrow mode Arsune creates
each bounty when the owner's proof is checked, and attaches storage for
`ARSUNE_ESCROW_CLAIMS` claims (default 50: 0.307 NEAR). The judge must hold 0.11 NEAR
while it sends `rule` (110 Tgas attached), and the real cost of one ruling is about 0.002 NEAR.

## Differences from the first design, and why

| First design | Built | Why |
| --- | --- | --- |
| The judge passes the scout in `rule` | The hunter names the scout in the signed bond `msg` | A judge that chooses the scout could send up to 50% of every reward to itself. Today the hunter names the scout in its claim, so the flow stays the same. |
| The judge is set at deployment | Each bounty names its judge | The escrow then needs no admin. Arsune creates bounties with judge = its account. |
| `base_bond_max`, bond up to 3x | `base_bond`, bond from `ceil(base/2)` to `3 * base` | These are the published reputation multipliers. The minimum also stops cheap bond spam. |
| Callbacks roll back the ruling | Failed payouts become `owed` and anyone can retry | A ruling must stay final. A rollback would let the judge rule again. |
| Only the owner withdraws, only the hunter reclaims | Anyone can start them, the receiver is fixed | 0x accounts cannot sign NEAR transactions. The owner can clear stale claims after the deadline. |
| Storage not specified | The creator prepays a storage pool | Bonds from `intents.near` cannot carry NEAR. |

## Open gaps

- **Funding outside Arsune.** Anyone can fund an escrow bounty with `mt_transfer_call` and the
  `fund` msg. The escrow accepts it, but Arsune's budget does not see it, so the escrow balance
  is then above Arsune's budget. Arsune never pays more than its budget, but a claim that Arsune
  rules `unfunded` while the escrow can pay is refused by the contract and goes to review.
- **No automatic retry of a ruling.** A `rule` transaction with an unknown or failed result
  sends the claim to review, as in `near_intents` mode. The contract refuses a second ruling,
  so a retry cannot pay twice, but an operator starts it.
- **1Click funding** is not available in escrow mode: 1Click delivers to Arsune's own account,
  not to the escrow with a `msg`.
- **Funds without `msg`.** A plain transfer to the escrow belongs to no bounty and stays in the
  escrow. There is no sweep, because any sweep needs an admin.
- **Storage spam.** Bonds of the minimum size can use up a bounty's storage pool. The per-hunter
  limit of three open claims (RULES.md) is checked by Arsune, not by the contract.
- **0x owners** cannot call `close_bounty`. They must wait for the deadline.
- **intents.near can change.** The sandbox runs defuse 0.4.5. If mainnet upgrades, the fixture
  hash check in `test.sh` fails; then build defuse at the new rev and run the tests again.
- **Not tested:** ERC-191 signed deposits on the sandbox (only in Python unit tests), and a
  notification `min_gas` of exactly 5 Tgas (the client refuses intents that set `min_gas`).
- **No audit.** No mainnet deployment yet. The deploy script was tested offline only.
- **Verifiable build** needs a commit and `cargo near build reproducible-wasm`; the current
  wasm has `build_info: null` in its NEP-330 metadata.
