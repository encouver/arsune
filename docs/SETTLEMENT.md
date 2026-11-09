# Settlement on NEAR Intents

Arsune pays proven claims and collects hunter bonds in USDC inside the NEAR Intents
Verifier contract `intents.near`. This contract is on mainnet only. There is no testnet
deployment. Every money movement is a NEP-413 signed `transfer` intent that a relayer
submits with `execute_intents`.

- Code: `backend/app/settlement/near_intents.py` (intents) and `near_tx.py` (keys, RPC,
  transactions).
- Interface: `backend/app/settlement/base.py` (`Settlement.collect` / `Settlement.pay`).
- `ARSUNE_SETTLEMENT_MODE=dry` (the default) moves no money. `near_intents` moves real USDC.

## Settings

| Variable | Default | Meaning |
| - | - | - |
| `ARSUNE_SETTLEMENT_MODE` | `dry` | `dry` or `near_intents` |
| `ARSUNE_NEAR_ACCOUNT_ID` | empty | Arsune account. It holds the USDC and pays the gas. |
| `ARSUNE_NEAR_PRIVATE_KEY` | empty | `ed25519:<base58>`. It signs the intents and the relayer transactions. Kept as a `SecretStr`. |
| `ARSUNE_NEAR_RPC_URL` | `https://free.rpc.fastnear.com` | Mainnet JSON-RPC |
| `ARSUNE_INTENTS_CONTRACT` | `intents.near` | Verifier contract |
| `ARSUNE_BOUNTY_TOKEN` | `nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1` | Native USDC, 6 decimals |
| `ARSUNE_MAX_PAYMENT_AMOUNT` | `1000000` (1 USDC) | `pay` and `collect` refuse larger amounts |

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

`verify_signed_transfer` (local, before any RPC call):

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

`execute` signs a v0 transaction with one FunctionCall: `execute_intents`, 100 Tgas, 0 deposit.
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

1. Send about 0.05 NEAR to the account ID. This creates the account. 100 Tgas prepaid is
   at most 0.01 NEAR at the current gas price (1e8 yocto/gas). Unused gas is refunded.
2. Put USDC into `intents.near` for the account. The cheapest way is from any wallet that
   holds native NEAR USDC: call `ft_transfer_call` on
   `17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1` with
   `{"receiver_id":"intents.near","amount":"1000000","msg":"<ARSUNE_NEAR_ACCOUNT_ID>"}`,
   1 yoctoNEAR and 100 Tgas. Because `msg` contains the account ID, the deposit goes to Arsune.
   - `intents.near` already has its USDC storage registration.
   - Arsune's account needs no registration on the USDC contract, because the USDC never lands
     on its NEAR-side balance.
   - If the USDC goes to the Arsune account on NEAR first, that account needs a
     `storage_deposit` on the USDC contract (0.00125 NEAR). Then the Arsune key signs
     `ft_transfer_call` with `msg: ""`.
3. Run the script without the flag. Make sure that it shows the USDC and NEAR.
4. Run it again with `--i-approve-mainnet`. Use a small amount, for example 10000 (0.01 USDC),
   to a receiver that you control.

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
  - `contracts/defuse/src/contract/intents/state.rs` (implicit keys)
  - `core/src/error.rs`
- [near-api-js](https://github.com/near/near-api-js) `test/unit/transactions/serialize.test.ts` and `data/*.json`
- [NEAR RPC transactions](https://docs.near.org/api/rpc/transactions.md): `send_tx`, `tx`, `wait_until`, `TIMEOUT_ERROR`
- [NEP-413](https://github.com/near/NEPs/blob/master/neps/nep-0413.md)
- [Deprecation of near.org RPC endpoints](https://near.org/blog/deprecation-of-near-org-and-pagoda-co-rpc-endpoints)
- [NEAR RPC providers](https://docs.near.org/api/rpc/providers)
