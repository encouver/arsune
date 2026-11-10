# Settlement on NEAR Intents

Arsune pays proven claims and collects hunter bonds in USDC inside the NEAR Intents
Verifier contract `intents.near`. This contract is on mainnet only. There is no testnet
deployment. Every money movement is a signed `transfer` intent that a relayer
submits with `execute_intents`. Arsune signs with NEP-413 (NEAR key). Hunters and funders
sign with NEP-413 or, with only an Ethereum key, with ERC-191 (see "Ethereum wallets").

- Code: `backend/app/settlement/near_intents.py` (intents), `near_tx.py` (keys, RPC,
  transactions) and `erc191.py` (Ethereum keys and signatures).
- Interface: `backend/app/settlement/base.py` (`Settlement.collect` / `Settlement.pay`).
- `ARSUNE_SETTLEMENT_MODE=dry` (the default) moves no money. `near_intents` moves real USDC.
  `escrow` (opt-in) keeps the money in the escrow contract instead of Arsune's account (see
  "Escrow mode").

## Settings

| Variable | Default | Meaning |
| - | - | - |
| `ARSUNE_SETTLEMENT_MODE` | `dry` | `dry`, `near_intents` or `escrow` |
| `ARSUNE_NEAR_ACCOUNT_ID` | empty | Arsune account. It holds the USDC and pays the gas. |
| `ARSUNE_NEAR_PRIVATE_KEY` | empty | `ed25519:<base58>`. It signs the intents and the relayer transactions. Kept as a `SecretStr`. |
| `ARSUNE_NEAR_RPC_URL` | `https://free.rpc.fastnear.com` | Mainnet JSON-RPC |
| `ARSUNE_INTENTS_CONTRACT` | `intents.near` | Verifier contract |
| `ARSUNE_BOUNTY_TOKEN` | `nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1` | Native USDC, 6 decimals |
| `ARSUNE_MAX_PAYMENT_AMOUNT` | `1000000` (1 USDC) | `pay` and `collect` refuse larger amounts (`near_intents` only) |
| `ARSUNE_ESCROW_CONTRACT` | empty | Escrow account (`escrow` mode) |
| `ARSUNE_ESCROW_CLAIMS` | `50` | Claim records that Arsune prepays for each escrow bounty (0.006 NEAR each) |
| `ARSUNE_ESCROW_BOUNTY_DAYS` | `30` | Escrow deadline: the bounty's creation time plus this many days |

## Signed transfer format (confirmed on mainnet)

```json
{
  "standard": "nep413",
  "payload": {
    "recipient": "intents.near",
    "nonce": "<base64 of 32 bytes>",
    "message": "{\"deadline\":\"2026-11-09T19:36:32.845585Z\",\"intents\":[{\"intent\":\"transfer\",\"memo\":\"<memo>\",\"receiver_id\":\"<receiver>\",\"tokens\":{\"nep141:17208628...33a1\":\"1\"}}],\"signer_id\":\"<sender>\"}"
  },
  "public_key": "ed25519:<base58 32 bytes>",
  "signature": "ed25519:<base58 64 bytes>"
}
```

- The signature is ed25519 over `sha256(borsh(u32 2^31+413, message: string, nonce: [u8;32], recipient: string, callbackUrl: Option<string> = None))`.
- `message` is compact JSON with sorted keys. The amount is a decimal string in the smallest unit.
- The nonce is a versioned V1 nonce: `5628f6c6 | 00 | salt (4) | deadline ns (i64 LE) | 15 random bytes`.
  The salt is `current_salt` on `intents.near` (now `252812b3`). The nonce deadline is the intent deadline.
  The contract still accepts random "legacy" nonces, but the defuse README says that they will be prohibited.
- Implicit accounts (64 hex characters) can sign with their own key and need no registration.
  A named account must first call `add_public_key` on `intents.near` with 1 yoctoNEAR
  (`register_public_key`, implemented but never run).
- The `transfer` intent takes no protocol fee. The receiver gets the exact amount
  (`Transfer::execute_intent` in near/intents).

These references were checked by unit tests:

- The NEP-413 vector from `defuse-nep413` (`verify_ok`).
- The signed `transfer` example in the NEAR Intents docs.
- The versioned nonce example in the defuse README.
- The FunctionCall transaction (`transaction1.json`) and signed transaction vectors from near-api-js.

## What each step checks

`verify_signed_transfer` (local, before any RPC call). It dispatches on `standard`
(`nep413` or `erc191`; the `erc191` checks are in "Ethereum wallets"):

- The JSON has exactly the keys shown above, `standard == "nep413"`, and the recipient is the configured contract.
- The nonce is 32 bytes. The key is 32 bytes and the signature is 64 bytes.
- The signature is valid over the exact message text.
- The message has no duplicate JSON keys. `signer_id` is the expected sender. The deadline is in the future.
- There is exactly one intent. It is a `transfer` with only `intent`, `receiver_id`, `tokens` and `memo`.
  This blocks the `msg` field, because `msg` would trigger `mt_on_transfer` on the receiver.
- The receiver, `{token: amount}` and the memo are exact matches.

`simulate` (`simulate_intents`, read-only) gives the Verifier's own answer for key ownership,
balance, salt, nonce reuse and deadline. It also requires that every signed nonce is in
`intents_executed`.

`execute` signs a v0 transaction with one FunctionCall: `execute_intents`, 30 Tgas (callers can
pass `gas=`), 0 deposit.
It sends the transaction with `send_tx` and `wait_until: FINAL`. It returns the transaction hash
only when all of these are true:

- The outcome is for the hash that Arsune computed.
- The top-level status is `SuccessValue`.
- No transaction or receipt outcome is a `Failure`.
- Every `(signer_id, nonce)` is in a dip4 `intents_executed` event.

If the RPC times out or cannot be reached, `execute` polls `tx` and sends the same signed bytes
again. The bytes have the same hash, so the transaction cannot run twice. If the outcome is
still unknown after 90 s, it raises `OutcomeUnknown(tx_hash)`. If the RPC rejects the
transaction (`INVALID_TRANSACTION`), it raises a plain `SettlementError`.

`NearIntentsSettlement`:

- `collect` runs the guardrail, then verify, simulate and execute.
- `pay` runs the guardrail, signs a 5-minute transfer from Arsune, then runs simulate and execute.
- The memo goes into the intent exactly as given.

## Library choice

The settlement uses PyNaCl (ed25519) and base58, with about 40 lines of hand-written Borsh
for one action type, and the httpx client that the project already uses.

The alternatives:

- `py-near` is async only and brings aiohttp, loguru, eth-keys and a native `py-near-primitives` wheel.
- `near-api-py` has had no release since 2021.

Arsune sends only `FunctionCall` transactions, so the small encoder is easier to audit. Published
near-api-js vectors pin its output byte for byte.

For ERC-191 the settlement adds two libraries (see "Ethereum wallets"):

- `coincurve` (21.x, MIT/Apache-2.0): bindings to libsecp256k1, the Bitcoin Core library.
  It signs with RFC 6979 nonces and low s, gives recoverable signatures and recovers keys.
  Wheels exist for CPython 3.9 to 3.13. It has no runtime dependencies.
- `pycryptodome` (3.x, pinned `<4`): Keccak-256. Python's `hashlib.sha3_256` is NIST SHA-3,
  which pads differently, so it gives other hashes. `eth-hash` pins the same `<4` range.
  The 4.x series had five releases in the three weeks before 2026-11-09.

`eth-keys` and `eth-account` were not used. They add `eth-utils`, `eth-typing` and their
dependencies for about 40 lines of code.

## RPC

`https://free.rpc.fastnear.com` is the default. A probe of a `current_salt` view on 2026-11-09 gave:

| Endpoint | Result |
| - | - |
| `free.rpc.fastnear.com` | 0.21 s |
| `rpc.fastnear.com` | 0.21 s |
| `near.drpc.org` | 0.27 s |
| `rpc.mainnet.near.org` | 3.4 s (deprecated) |
| `near.lava.build` | 410, discontinued |
| `1rpc.io/near` | no `query` method |
| `near.blockpi.network` | needs an API key |

## Live read-only check (2026-11-09)

`backend/tests/test_settlement_near_live.py` (marker `network`) uses a throwaway implicit
account and `simulate_intents` only. It sends no transaction. The Verifier gave these answers:

| Signed input | Verifier answer |
| - | - |
| Transfer of 1 unit of USDC | `insufficient balance or overflow` (format, signature, salt and nonce accepted) |
| Signature with one changed bit | `invalid signature` |
| Amount changed after signing | `invalid signature` |
| Salt `00000000` | `invalid salt` |
| Deadline in the past | `deadline has expired` |
| Named account without a registered key | `public key '…' doesn't exist for account 'intents.near'` |
| `add_public_key` intent (needs no balance) | Success: `intents_executed` lists our nonce; event `public_key_added`; `state.fee = 1`, `current_salt = 252812b3` |

## Ethereum wallets

A hunter agent with only an Ethereum key (a `0x` address) can sign its bond and get paid.
The Verifier derives the account from the key: `0x` + lowercase hex of the last 20 bytes of
`keccak256(public key x | y)`. This is the Ethereum address. The account is implicit, so it
needs no `add_public_key`.

```python
from app.settlement.erc191 import generate_evm_key
from app.settlement.near_intents import sign_transfer_erc191

private_key, address = generate_evm_key()  # or an existing 0x... key
bond = sign_transfer_erc191(
    private_key, arsune_account, token, bond_amount, f"arsune:bond:{claim_id}",
    deadline, bytes.fromhex(salt),  # salt from GET /api/settlement
)
# Send the claim with hunter_account=address and bond_intent=bond.
```

### Signed transfer format (confirmed on mainnet)

```json
{
  "standard": "erc191",
  "payload": "{\"deadline\":\"2026-11-09T22:56:35.388528Z\",\"intents\":[{\"intent\":\"transfer\",\"memo\":\"<memo>\",\"receiver_id\":\"<receiver>\",\"tokens\":{\"nep141:17208628...33a1\":\"1\"}}],\"nonce\":\"<base64 of 32 bytes>\",\"signer_id\":\"0x<40 lowercase hex>\",\"verifying_contract\":\"intents.near\"}",
  "signature": "secp256k1:<base58 of 65 bytes r | s | v>"
}
```

- `payload` is one JSON string with `signer_id`, `verifying_contract`, `deadline`, `nonce` and
  `intents` (`DefusePayload` in near/intents). NEP-413 puts the same data in `recipient`,
  `nonce` and `message`. Arsune writes compact JSON with sorted keys. The Verifier accepts any
  key order.
- There is no `public_key`. The Verifier recovers the key from the signature.
- The signed hash is `keccak256("\x19Ethereum Signed Message:\n" + decimal byte length of payload + payload)`.
  This is what `personal_sign` signs. The Verifier's `intent_hash` is base58 of this hash
  (checked on mainnet).
- The signature is r | s | v (65 bytes), base58 with the prefix `secp256k1:`.
  - v must be 0 or 1. Wallets give 27 or 28. `erc191.normalize_signature` subtracts 27.
  - s must be in the lower half of the curve order. The Verifier calls `ecrecover` with the
    malleability check on. libsecp256k1 always signs with low s.
- The nonce, salt and deadline rules are the same as for NEP-413.
- `signer_id` must be lowercase, because NEAR account IDs have no upper case. The Verifier
  rejects a checksummed (EIP-55) address as signer or receiver. The ERC-191 example in the
  NEAR Intents docs shows a checksummed `signer_id`. Its signature is valid, but for the
  lowercase address.

### What `verify_signed_transfer` checks for `erc191`

- The JSON has exactly `standard`, `payload` and `signature`. `payload` is a string.
- The signature is 65 bytes, v is 0 or 1, 0 < r < n and 0 < s ≤ n/2.
- The address recovered from the signature is the `signer_id` of the payload. Thus only
  implicit 0x accounts can sign with ERC-191. A named account with a registered secp256k1
  key would pass the Verifier but not this check.
- The payload has no duplicate JSON keys and exactly the five keys above.
  `verifying_contract` is the configured contract. The nonce is 32 bytes in canonical base64.
- Then the NEP-413 rules apply: `signer_id` is the expected sender, the deadline is in the
  future, and there is exactly one `transfer` with the exact receiver, `{token: amount}` and memo.

`simulate` and `execute` get the `(signer_id, nonce)` of both standards from `signer_and_nonce`.
`DrySettlement` and `NearIntentsSettlement` accept ERC-191 bonds and funding with no other change.

### Payouts to a 0x account

`transfer` takes any NEAR `AccountId` as `receiver_id` (`Transfer` in
`core/src/intents/tokens.rs`). A lowercase 0x address is a valid account ID (an Eth-implicit
account). Arsune pays it with its usual NEP-413 `transfer`. The USDC stays in `intents.near`
for the 0x account. The holder of the Ethereum key can sign more intents with it, for example
a withdrawal.

### Bounty intake

`require_account` checks the hunter, sender, owner and scout accounts against the NEAR account
ID rule. A lowercase 0x address passes. A checksummed address gets 422 with "Use the lowercase
0x address for the hunter: 0x…" and the lowercase form. No schema change was necessary.

### Live read-only check (2026-11-09)

`backend/tests/test_settlement_evm.py` (marker `network`) uses `free.rpc.fastnear.com`, a
throwaway Ethereum key and `simulate_intents` only. It sends no transaction.

| Signed input | Verifier answer |
| - | - |
| ERC-191 transfer of 1 unit of USDC to a new 0x account | `insufficient balance or overflow` (format, signature, salt, nonce, signer and 0x receiver accepted) |
| Empty `intents` list (needs no balance) | Success: `intents_executed` lists the 0x address and our nonce. `intent_hash` is base58 of the ERC-191 hash. |
| v = 27 or 28 (not normalized) | `invalid signature` |
| High-s twin of a valid signature (n − s, other v) | `invalid signature` |
| One bit of s changed | `public key 'secp256k1:…' doesn't exist for account '0x…'` (another key is recovered) |
| Amount changed after signing | `public key 'secp256k1:…' doesn't exist for account '0x…'` |
| Checksummed `signer_id` or receiver | `JSON: invalid value: "0x6F41…", the Account ID contains an invalid character 'F' …` |

Published vectors in the unit tests:

- `recover_ok` in near/intents `crates/signatures/erc191` ("Hello world!").
- The secp256k1 case of `to_implicit_account_id` in `core/src/public_key.rs`.
- The ERC-191 example in the NEAR Intents signing docs.
- The eth-account `Account.recover_message` doctest ("I♥SF", v = 28): v normalization, the
  hash and the address. Our signature is the same as the wallet's (RFC 6979 nonces).

## First real payment

`scripts/near_intents_payment.py --receiver <account> --amount <units>`:

- It prints the plan and the balances: USDC in `intents.near` and NEAR for gas. It never prints the key.
- It refuses when one of these is true:
  - The settings are missing.
  - The amount is more than 1 USDC or more than `ARSUNE_MAX_PAYMENT_AMOUNT`.
  - A deposit would be attached.
  - Prepaid gas could cost more than 0.02 NEAR.
  - The USDC or NEAR balance is too small.
- It signs and sends only with `--i-approve-mainnet`.

Steps for an implicit Arsune account (64 hex characters):

1. Send NEAR to the account ID. This creates the account. See "Gas: what must be held up
   front" below: 0.2 NEAR covers the payment, the deposit, the swarm setup and the live race.
2. Put USDC into `intents.near` for the account. The cheapest way is from any wallet that
   holds native NEAR USDC: call `ft_transfer_call` on
   `17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1` with
   `{"receiver_id":"intents.near","amount":"1000000","msg":"<ARSUNE_NEAR_ACCOUNT_ID>"}`,
   1 yoctoNEAR and 50 Tgas. Because `msg` contains the account ID, the deposit goes to Arsune.
   - `intents.near` already has its USDC storage registration.
   - Arsune's account needs no registration on the USDC contract, because the USDC never lands
     on its NEAR-side balance.
   - If the USDC goes to the Arsune account on NEAR first, that account needs a
     `storage_deposit` on the USDC contract (0.00125 NEAR). Wallets usually pay this for the
     receiver. Then run `scripts/near_deposit_usdc.py --amount <units>`. The Arsune key signs
     `ft_transfer_call` to `intents.near` with `msg: ""`, 1 yoctoNEAR and 50 Tgas.
     - Without `--i-approve-mainnet`, the script only prints the plan and the balances: USDC on
       the FT contract, NEAR, and USDC in `intents.near`.
     - It refuses when one of these is true: the amount is more than 1 USDC or the account's
       USDC balance, the storage registration is missing (it then prints the exact
       `storage_deposit` fix), or gas could cost more than 0.02 NEAR.
     - With the flag, it sends the call and waits for the final outcome. It checks that
       `ft_transfer_call` kept the full amount and that the `intents.near` balance went up by
       that amount. Then it prints the transaction hash and a nearblocks link.
3. Run the script without the flag. Make sure that it shows the USDC and NEAR.
4. Run it again with `--i-approve-mainnet`. Use a small amount, for example 10000 (0.01 USDC),
   to a receiver that you control.

## Gas: what must be held up front

nearcore buys attached gas at `max(gas price, min_gas_purchase_price)`. That minimum is
1e9 yocto/gas (0.001 NEAR per Tgas, `parameters.yaml`), ten times the gas price of 1e8. The
difference is refunded one block later, so the real cost stays at the gas price.

- An account must hold 0.001 NEAR for each Tgas that it attaches. For 100 Tgas, that is 0.1 NEAR.
- Each new account also costs a fixed 0.007 NEAR (`account_creation_charge`). The protocol keeps
  this back from the gas refund.
- Accounts of 770 bytes or less need no storage balance (zero-balance accounts).

Attached gas is therefore kept near the real need, from nearblocks samples:

| Call | Real use | Attached | Held up front |
| - | - | - | - |
| `execute_intents` with only `transfer` intents | 3.6 to 8.2 Tgas | 30 Tgas (`EXECUTE_GAS`) | 0.03 NEAR |
| `add_public_key` | 3.3 Tgas | 10 Tgas (`ADD_KEY_GAS`) | 0.01 NEAR |
| `ft_transfer_call` deposit | about 9 Tgas (more than 30 Tgas must be attached) | 50 Tgas | 0.05 NEAR |

The payment and deposit scripts check the held amount against the NEAR balance. They check
the gas-price cost (at most 0.02 NEAR) against the budget.

## Swarm accounts

`scripts/near_swarm_accounts.py` gives each agent its own named account under one parent,
so every on-chain action traces back to one agent. Steps, each skipped when the chain shows it
is already done (the script resumes after a failure):

1. **Parent.** Arsune calls `create_account` on `near`, with
   `{"new_account_id": "<parent>.near", "new_public_key": "ed25519:..."}`, 0 deposit and 50 Tgas.
   - The `near` account runs near-linkdrop, which provides
     `create_account(new_account_id, new_public_key)` (payable).
   - The call returns `true` when the account is created. If creation fails, the contract
     refunds the deposit and returns `false`.
   - Before this step, the script checks the name with `view_account`. If the name exists and
     the key from the file is not on it, the script refuses.
2. **Sub-accounts.** The script creates `owner`, `hunter-1..N` and `scout-1..M` under the
   parent. Each is a `CreateAccount` + `AddKey(full access)` from the parent, with a fresh key.
   - The parent signs a NEP-366 `DelegateAction`. Arsune relays it and pays the gas.
   - On chain, the parent creates each sub-account.
3. **Key registration.** Each sub-account calls `add_public_key` on `intents.near` with
   1 yoctoNEAR, through the same relay. The predecessor is the sub-account itself.
4. **USDC.** One `execute_intents` (50 Tgas) carries one message that Arsune signs. The message
   has a `transfer` to `owner` (default 0.5 USDC) and to each hunter (default 0.02 USDC).
   The amounts are top-ups to those targets.

Arsune pays all gas and deposits. So the new accounts hold no NEAR and stay zero-balance
accounts. Funded sub-accounts would have tied up about 0.1 NEAR. That is more than the budget.

Keys:

- The script writes the keys to `ARSUNE_SWARM_ACCOUNTS_FILE` (default `.data/swarm-accounts.json`,
  mode 600) before it sends any transaction.
- An existing file keeps its keys. Roles that are not requested now also stay in the file.
- The swarm runner loads the accounts with
  `app.settlement.swarm_accounts.load_swarm_accounts()`. It returns `role -> SwarmAccount(role,
  account_id, public_key, private_key: SecretStr)`.

Without `--i-approve-mainnet`, the script makes only read-only calls and prints a cost table.
It refuses when one of these is true:

- Total NEAR is more than 0.12.
- Total USDC is more than 0.9.
- A target is more than `ARSUNE_MAX_PAYMENT_AMOUNT`.
- Arsune holds less than total NEAR plus the largest up-front hold, or less USDC than the total.
- A name is taken.

Default cost table at a gas price of 1e8. These are upper bounds: they assume that all attached
gas burns.

| Step | Tx | NEAR each | NEAR | Held up front |
| - | - | - | - | - |
| create `arsune-swarm.near` with `near.create_account` | 1 | 0.012220 | 0.012220 | 0.059196 |
| create sub-account (relayed) | 10 | 0.007974 | 0.079736 | 0.016736 |
| `add_public_key` on `intents.near` (relayed) | 10 | 0.001281 | 0.012812 | 0.012812 |
| `execute_intents`, 7 USDC transfers | 1 | 0.005220 | 0.005220 | 0.052196 |
| **Total** | 22 | | **0.109987 NEAR** | Arsune must hold 0.169 NEAR |

Total USDC is 0.62 (0.5 to the owner and 6 × 0.02 to the hunters). The expected real cost is
about 0.09 NEAR, mostly 11 × 0.007 NEAR for account creation.

## Escrow mode

`ARSUNE_SETTLEMENT_MODE=escrow` uses the escrow contract (`contracts/escrow`, [ESCROW.md](ESCROW.md))
through `EscrowSettlement` in `backend/app/settlement/escrow.py`. Arsune is the judge, the fee
account and the relayer. It never holds bounty money or bonds: the escrow holds them as its own
balance on `intents.near`, and the contract sends every payout. Tested in a local sandbox and
with an in-memory chain; not deployed on mainnet.

| Step | What Arsune does |
| - | - |
| `POST /api/bounties/{id}/verify` | After the owner's proof, `create_bounty` with the owner, judge = fee account = `ARSUNE_NEAR_ACCOUNT_ID`, the reward, base bond, fee (at most 2000 bps), deadline and storage for `ARSUNE_ESCROW_CLAIMS` claims. A bounty that already exists with the same terms is accepted; other terms stop the opening (502). Ledger row `escrow_open`. |
| `POST /api/bounties/{id}/fund` | Checks a signed `transfer` to the escrow with `msg` `{"fund":"<bounty_id>"}` and memo `arsune:fund:{fund_id}` (`escrow.verify_deposit`), then relays it with `execute_intents`. A deposit that the escrow refunds is a failed transfer (402). |
| `POST /api/bounties/{id}/claims` | The same for the bond: `msg` `{"bond":{"bounty_id":...,"claim_id":...,"scout":{"account_id":...,"share_bps":...}}}` (the scout only if the claim names one), memo `arsune:bond:{claim_id}`. |
| Worker | Decides the claim and publishes the evidence bundle, saves the ledger row of the ruling (`reward_out`, `bond_forfeit` or `bond_refund`) as `pending`, then sends `rule(claim_id, outcome, evidence_hash)`. On success the rule transaction ID goes on that row and on the `bond_refund`, `scout_share` and `fee` rows of the same payout. A forfeited bond enters Arsune's budget only then. |
| `POST /api/bounties/{id}/withdraw` | `withdraw_remaining` (anyone may call it; the receivers are fixed): after the owner's `close_bounty` or the deadline, the rest goes to the owner. Ledger row `withdraw`; the bounty closes with `closed_reason` `withdrawn` and takes no more top-ups. |

- Clients read `GET /api/settlement`. In escrow mode it adds `escrow_contract`, the receiver of
  every deposit. The salt is the Verifier's current salt, as in `near_intents` mode. Swarm
  agents sign escrow deposits with `escrow.sign_deposit` (`swarm/identity.py`).
- Outcomes sent to `rule`: `proven`, `rejected`, `duplicate`, `unverified`, and `unfunded` for a
  proven claim that Arsune's budget cannot pay. A claim whose check job stopped at its time or
  attempt limit gets `unverified`, with an evidence bundle that gives that reason. The `rule`
  event must give the same outcome and the same evidence hash; otherwise the claim goes to
  `needs_review`.
- Safety on retry is the same as in `near_intents` mode: the ruling's ledger row exists before
  the transaction, and a repeated run that finds it without a transaction ID sends nothing and
  holds the claim for review. The contract also refuses a second ruling for a claim.
- The `near_intents` limits do not apply: no `ARSUNE_MAX_PAYMENT_AMOUNT` check, and no bond cap
  (the cap could put a bond below the escrow's minimum of half the base bond).
- 1Click funding needs `near_intents` mode.
- Escrow ledger rows link to nearblocks like `near_intents` rows. On a local sandbox these
  links do not resolve.

## Known limits

- The bounty layer must not retry a payment after `OutcomeUnknown`. A signed intent is visible
  to the RPC provider during simulation and on chain during execution, so another relayer could
  submit the same intent. That intent can run only once (nonce), but Arsune's own transaction
  then fails with `nonce was already used`. Check the account balance or events before a manual retry.
- A process lock serializes relayer transactions. Two processes that share one key can collide
  on the access-key nonce. The second transaction is then rejected (no money moves) and raises
  `SettlementError`.

## Sources

- NEAR Intents docs:
  - [Verifier contract](https://docs.near-intents.org/integration/verifier-contract/introduction.md)
  - [Account abstraction](https://docs.near-intents.org/integration/verifier-contract/account-abstraction.md)
  - [Intent types and execution](https://docs.near-intents.org/integration/verifier-contract/intent-types-and-execution.md)
  - [Signing intents](https://docs.near-intents.org/integration/verifier-contract/signing-intents.md)
  - [Simulating intents](https://docs.near-intents.org/integration/verifier-contract/simulating-intents.md)
  - [Deposits](https://docs.near-intents.org/integration/verifier-contract/deposits-and-withdrawals/deposits.md)
  - [Balances](https://docs.near-intents.org/integration/verifier-contract/deposits-and-withdrawals/balances.md)
  - [Fees](https://docs.near-intents.org/resources/fees.md)
- [near/intents](https://github.com/near/intents) source:
  - `contracts/defuse/README.md` (salt, versioned nonces)
  - `contracts/defuse/core/src/engine/mod.rs` (check order)
  - `core/src/intents/tokens.rs` (`Transfer`)
  - `core/src/payload/nep413.rs`
  - `crates/signatures/nep413/src/lib.rs` (prehash, test vector)
  - `crates/signatures/erc191/src/lib.rs` (prehash, test vector) and
    `core/src/payload/{erc191,mod,multi}.rs` (payload format)
  - `crates/crypto/src/secp256k1.rs` (65-byte signature, v, low s) and
    `core/src/public_key.rs` (0x implicit account, test vector)
  - `contracts/defuse/src/contract/intents/state.rs` (implicit keys)
  - `core/src/error.rs`
- [near-api-js](https://github.com/near/near-api-js) `test/unit/transactions/serialize.test.ts` and `data/*.json`
- [NEAR RPC transactions](https://docs.near.org/api/rpc/transactions.md): `send_tx`, `tx`, `wait_until`, `TIMEOUT_ERROR`
- [NEP-413](https://github.com/near/NEPs/blob/master/neps/nep-0413.md)
- [EIP-191](https://eips.ethereum.org/EIPS/eip-191) and
  [eth-account](https://github.com/ethereum/eth-account) `eth_account/account.py` (`recover_message` doctest)
- [near-linkdrop](https://github.com/near/near-linkdrop) `src/lib.rs` (`create_account`, refund callback)
- [Creating named accounts](https://docs.near.org/protocol/tutorials/creating-implicit-and-named-accounts.md),
  [Gas](https://docs.near.org/protocol/transactions/gas.md) (buy and burn price, account creation charge),
  [Accounts for integrators](https://docs.near.org/integrations/accounts.md) (zero-balance accounts)
- nearcore `core/parameters/res/runtime_configs/parameters.yaml` (fees, `min_gas_purchase_price`)
  and `core/primitives/src/fees.rs` (the relayer pays delegate deposits)
- near-api-js `serialize.test.ts` vectors (multi-action transaction, signed delegate)
- nearblocks API samples: `add_public_key` tx FBTRk6jR… (3.3 Tgas), deposits DWv4AkrL… and
  Bn3iC9B1…, and 25 recent `execute_intents` on `intents.near`
- [Deprecation of near.org RPC endpoints](https://near.org/blog/deprecation-of-near-org-and-pagoda-co-rpc-endpoints)
- [NEAR RPC providers](https://docs.near.org/api/rpc/providers)
