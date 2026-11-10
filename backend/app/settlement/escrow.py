"""Client for the Arsune bounty escrow contract (contracts/escrow, docs/ESCROW.md).

The escrow holds bounty money and bonds as its own USDC balance inside intents.near. A deposit
is a NEP-245 transfer to the escrow account whose `msg` names the bounty or the claim:

- `{"fund":"<bounty_id>"}` adds to a bounty balance;
- `{"bond":{"bounty_id":...,"claim_id":...,"scout":{"account_id":...,"share_bps":...}}}` posts
  a claim bond (the scout is optional and is fixed by the hunter's signature).

intents.near then calls `mt_on_transfer` on the escrow and refunds whatever the escrow returns,
so a deposit the escrow does not accept goes back in full. A depositor with a NEAR account can
call `mt_transfer_call` itself; a hunter signs a `transfer` intent with `msg` (NEP-413 or
ERC-191) that Arsune relays with `execute_intents`.

Only the judge calls `rule`; amounts and receivers follow from the on-chain terms.
This module only builds and sends the calls. It is not wired into the bounty flow yet.
"""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from nacl.signing import SigningKey
from pydantic import SecretStr

from app.core.config import settings
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.near_tx import TGAS, NearRpc, outcome_logs

EVENT_STANDARD = "arsune-escrow"
MAX_FEE_BPS = 2_000
MAX_SCOUT_SHARE_BPS = 5_000
MAX_BOND_MULTIPLIER = 3
SECOND_NS = 10**9
MIN_RULE_TIMEOUT_NS = 60 * SECOND_NS
MAX_RULE_TIMEOUT_NS = 30 * 24 * 3600 * SECOND_NS
DEFAULT_RULE_TIMEOUT_NS = 24 * 3600 * SECOND_NS
MAX_AMOUNT = 10**30
OUTCOMES = ("proven", "rejected", "duplicate", "unverified", "unfunded")
ID = re.compile(r"[a-z0-9_-]{1,64}")
HASH = re.compile(r"[0-9a-f]{64}")
# Storage: 1e19 yoctoNEAR per byte on mainnet. A claim (decided, longest names) uses at most
# CLAIM_STORAGE_BYTES (checked by a contract unit test); the bounty record stays under 700.
STORAGE_BYTE_COST = 10**19
CLAIM_STORAGE_BYTES = 600
BOUNTY_RECORD_BYTES = 700
ONE_YOCTO = 1
# Gas. Attached gas is held up front at 0.001 NEAR per Tgas, so keep it near the need.
# `rule` reserves 3 x (20 Tgas mt_transfer + 10 Tgas callback); the sandbox burnt 20 Tgas.
CREATE_GAS = 30 * TGAS
RULE_GAS = 110 * TGAS
PAYOUT_CALL_GAS = 50 * TGAS  # reclaim_bond, withdraw_remaining, retry_payout: one payout
CLOSE_GAS = 10 * TGAS
# intents.near gives mt_on_transfer at least 30 Tgas by default, plus 15 Tgas to resolve.
DEPOSIT_GAS = 100 * TGAS
TRANSFER_KEYS = {"intent", "receiver_id", "tokens", "memo", "msg"}


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SettlementError(f"{what} must be 1 to 64 characters of a-z, 0-9, - and _")
    return value


def _check_amount(value: int, what: str) -> int:
    if type(value) is not int or not 0 < value <= MAX_AMOUNT:
        raise SettlementError(f"{what} must be a positive integer of the token's smallest unit")
    return value


def compact(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class BountyTerms:
    """Immutable terms of one escrow bounty. Amounts are in the token's smallest unit."""

    bounty_id: str
    owner: str
    judge: str
    fee_account: str
    reward: int
    base_bond: int
    deadline_ns: int
    fee_bps: int = 1_000
    scout_share_max_bps: int = MAX_SCOUT_SHARE_BPS
    rule_timeout_ns: int = DEFAULT_RULE_TIMEOUT_NS

    def __post_init__(self):
        _check_id(self.bounty_id, "bounty_id")
        _check_amount(self.reward, "reward")
        _check_amount(self.base_bond, "base_bond")
        if not 0 <= self.fee_bps <= MAX_FEE_BPS:
            raise SettlementError(f"fee_bps must be 0 to {MAX_FEE_BPS}")
        if not 0 <= self.scout_share_max_bps <= MAX_SCOUT_SHARE_BPS:
            raise SettlementError(f"scout_share_max_bps must be 0 to {MAX_SCOUT_SHARE_BPS}")
        if not MIN_RULE_TIMEOUT_NS <= self.rule_timeout_ns <= MAX_RULE_TIMEOUT_NS:
            raise SettlementError("rule_timeout_ns must be 60 s to 30 days")
        if self.deadline_ns <= 0:
            raise SettlementError("deadline_ns must be a positive timestamp in nanoseconds")

    @property
    def fee(self) -> int:
        return self.reward * self.fee_bps // 10_000

    @property
    def min_bond(self) -> int:
        return -(-self.base_bond // 2)

    @property
    def max_bond(self) -> int:
        return self.base_bond * MAX_BOND_MULTIPLIER

    def args(self) -> dict:
        """JSON arguments of create_bounty (U128 and U64 as decimal strings)."""
        return {
            "bounty_id": self.bounty_id,
            "owner": self.owner,
            "judge": self.judge,
            "fee_account": self.fee_account,
            "reward": str(self.reward),
            "base_bond": str(self.base_bond),
            "fee_bps": self.fee_bps,
            "scout_share_max_bps": self.scout_share_max_bps,
            "deadline_ns": str(self.deadline_ns),
            "rule_timeout_ns": str(self.rule_timeout_ns),
        }

    def matches(self, view: dict) -> bool:
        """True when an on-chain get_bounty view has exactly these terms."""
        return all(view.get(key) == value for key, value in self.args().items())


def deadline_ns(when: datetime) -> int:
    if when.tzinfo is None:
        raise SettlementError("deadline must be timezone-aware")
    return int(when.astimezone(UTC).timestamp()) * SECOND_NS


def storage_deposit(claims: int) -> int:
    """yoctoNEAR to attach to create_bounty for `claims` claims. Unused NEAR is refunded."""
    if type(claims) is not int or claims < 1:
        raise SettlementError("a bounty needs room for at least one claim")
    return (BOUNTY_RECORD_BYTES + claims * CLAIM_STORAGE_BYTES) * STORAGE_BYTE_COST


def fund_msg(bounty_id: str) -> str:
    return compact({"fund": _check_id(bounty_id, "bounty_id")})


def bond_msg(bounty_id: str, claim_id: str, scout: tuple[str, int] | None = None) -> str:
    bond = {
        "bounty_id": _check_id(bounty_id, "bounty_id"),
        "claim_id": _check_id(claim_id, "claim_id"),
    }
    if scout is not None:
        account_id, share_bps = scout
        if type(share_bps) is not int or not 1 <= share_bps <= MAX_SCOUT_SHARE_BPS:
            raise SettlementError(f"scout share_bps must be 1 to {MAX_SCOUT_SHARE_BPS}")
        bond["scout"] = {"account_id": account_id, "share_bps": share_bps}
    return compact({"bond": bond})


def deposit_intent(escrow: str, token_id: str, amount: int, msg: str, memo: str) -> dict:
    """A `transfer` intent to the escrow; its `msg` makes intents.near call mt_on_transfer."""
    _check_amount(amount, "amount")
    return {
        "intent": "transfer",
        "receiver_id": escrow,
        "tokens": {token_id: str(amount)},
        "memo": memo,
        "msg": msg,
    }


def sign_deposit(
    account_id: str,
    private_key: str | SecretStr,
    escrow: str,
    token_id: str,
    amount: int,
    msg: str,
    memo: str,
    deadline: datetime,
    *,
    salt: bytes | None = None,
    contract: str | None = None,
    rpc: NearRpc | None = None,
) -> dict:
    """NEP-413 signed deposit (NEAR key) that Arsune can relay with `relay_deposit`."""
    intent = deposit_intent(escrow, token_id, amount, msg, memo)
    return ni.sign_intents(
        account_id, private_key, [intent], deadline, salt=salt, contract=contract, rpc=rpc
    )


def sign_deposit_erc191(
    private_key_hex: str | SecretStr,
    escrow: str,
    token_id: str,
    amount: int,
    msg: str,
    memo: str,
    deadline: datetime,
    salt: bytes | None = None,
    *,
    contract: str | None = None,
    rpc: NearRpc | None = None,
) -> dict:
    """ERC-191 signed deposit for a hunter that holds only an Ethereum key."""
    intent = deposit_intent(escrow, token_id, amount, msg, memo)
    return ni.sign_intents_erc191(
        private_key_hex, [intent], deadline, salt, contract=contract, rpc=rpc
    )


def verify_deposit(
    signed: dict,
    *,
    sender: str,
    escrow: str,
    token_id: str,
    amount: int,
    msg: str,
    memo: str,
    contract: str | None = None,
    now: datetime | None = None,
) -> None:
    """Raise unless `signed` is one valid, unexpired transfer of exactly `amount` of `token_id`
    from `sender` to `escrow` with exactly this `msg` and `memo` (and no `min_gas`).

    The same envelope and signature checks as near_intents.verify_signed_transfer; only the
    transfer may (and must) carry the `msg` that the escrow reads.
    """
    _check_amount(amount, "amount")
    contract = contract or settings.intents_contract
    if not isinstance(signed, dict):
        raise SettlementError("signed intent must be a JSON object")
    # The envelope checks live in near_intents; they are shared, not duplicated.
    if signed.get("standard") == "nep413":
        message = ni._verify_nep413(signed, contract)
    elif signed.get("standard") == "erc191":
        message = ni._verify_erc191(signed, contract)
    else:
        raise SettlementError("signed intent must use the nep413 or erc191 standard")
    if message["signer_id"] != sender:
        raise SettlementError("signer_id is not the expected sender")
    if ni._parse_deadline(message["deadline"]) <= (now or datetime.now(UTC)):
        raise SettlementError("signed intent deadline has passed")
    intents = message["intents"]
    if not isinstance(intents, list) or len(intents) != 1:
        raise SettlementError("signed message must hold exactly one intent")
    expected = deposit_intent(escrow, token_id, amount, msg, memo)
    intent = ni._exact_keys(intents[0], TRANSFER_KEYS, "deposit intent")
    for key, value in expected.items():
        if intent[key] != value:
            raise SettlementError(f"deposit intent {key} does not match")


def mt_transfer_call_args(escrow: str, token_id: str, amount: int, msg: str) -> dict:
    """Arguments for intents.near mt_transfer_call (attach 1 yoctoNEAR, DEPOSIT_GAS)."""
    _check_amount(amount, "amount")
    return {"receiver_id": escrow, "token_id": token_id, "amount": str(amount), "msg": msg}


def canonical_json(bundle) -> bytes:
    """The bytes Arsune hashes: compact JSON, sorted keys, UTF-8."""
    return json.dumps(bundle, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()


def evidence_hash(bundle) -> str:
    """sha256 hex of the evidence bundle (bytes as given, anything else as canonical JSON)."""
    data = bundle if isinstance(bundle, bytes) else canonical_json(bundle)
    return hashlib.sha256(data).hexdigest()


def rule_args(claim_id: str, outcome: str, evidence: str) -> dict:
    if outcome not in OUTCOMES:
        raise SettlementError(f"outcome must be one of {', '.join(OUTCOMES)}")
    if not isinstance(evidence, str) or not HASH.fullmatch(evidence):
        raise SettlementError("evidence_hash must be 64 lowercase hex characters")
    return {
        "claim_id": _check_id(claim_id, "claim_id"),
        "outcome": outcome,
        "evidence_hash": evidence,
    }


def escrow_events(outcome: dict) -> list[dict]:
    """The escrow's NEP-297 events in a transaction outcome, as {"event", **data}."""
    found = []
    for log in outcome_logs(outcome):
        if not log.startswith("EVENT_JSON:"):
            continue
        try:
            event = json.loads(log.removeprefix("EVENT_JSON:"))
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("standard") == EVENT_STANDARD:
            found.append({"event": event.get("event"), **(event.get("data") or {})})
    return found


def _return_value(outcome: dict):
    value = base64.b64decode(outcome.get("status", {}).get("SuccessValue") or "")
    return json.loads(value) if value else None


@dataclass(frozen=True)
class EscrowCall:
    tx_hash: str
    result: object
    events: list[dict]


def _refused(events: list[dict]) -> None:
    refunds = [e for e in events if e["event"] == "refund"]
    if refunds:
        raise SettlementError(f"the escrow refunded the deposit: {refunds[0].get('reason')}")


class EscrowClient:
    """Views and calls of one deployed escrow. Callers pass the signing account and key."""

    def __init__(
        self,
        contract_id: str,
        *,
        rpc: NearRpc | None = None,
        intents_contract: str | None = None,
        token_id: str | None = None,
    ):
        self.contract_id = contract_id
        self.rpc = rpc or NearRpc(settings.near_rpc_url)
        self.intents_contract = intents_contract or settings.intents_contract
        self.token_id = token_id or settings.bounty_token

    def _view(self, method: str, args: dict):
        return self.rpc.view(self.contract_id, method, args)

    def get_config(self) -> dict:
        return self._view("get_config", {})

    def get_bounty(self, bounty_id: str) -> dict | None:
        return self._view("get_bounty", {"bounty_id": bounty_id})

    def get_claim(self, claim_id: str) -> dict | None:
        return self._view("get_claim", {"claim_id": claim_id})

    def list_claims(self, bounty_id: str, from_index: int = 0, limit: int = 100) -> list[dict]:
        args = {"bounty_id": bounty_id, "from_index": from_index, "limit": limit}
        return self._view("list_claims", args)

    def get_owed(self, account_id: str) -> int:
        return int(self._view("get_owed", {"account_id": account_id}))

    def check_terms(self, terms: BountyTerms) -> dict:
        """Refuse to fund or bond a bounty whose on-chain terms are not the expected ones,
        for example a bounty ID that someone else created first."""
        view = self.get_bounty(terms.bounty_id)
        if view is None:
            raise SettlementError(f"bounty {terms.bounty_id} does not exist on the escrow")
        if not terms.matches(view):
            raise SettlementError(f"bounty {terms.bounty_id} has other terms on the escrow")
        if self.get_config()["token_id"] != self.token_id:
            raise SettlementError("the escrow holds another token")
        return view

    def _call(self, signer_id, key, method, args, *, gas, deposit=0) -> EscrowCall:
        tx_hash, outcome = self.rpc.call_function(
            signer_id, key, self.contract_id, method, args, gas=gas, deposit=deposit
        )
        return EscrowCall(tx_hash, _return_value(outcome), escrow_events(outcome))

    def create_bounty(self, signer_id: str, key: SigningKey, terms: BountyTerms, claims: int):
        if signer_id not in (terms.owner, terms.judge):
            raise SettlementError("only the owner or the judge can create a bounty")
        return self._call(
            signer_id,
            key,
            "create_bounty",
            terms.args(),
            gas=CREATE_GAS,
            deposit=storage_deposit(claims),
        )

    def rule(self, judge_id: str, key: SigningKey, claim_id: str, outcome: str, evidence: str):
        call = self._call(
            judge_id, key, "rule", rule_args(claim_id, outcome, evidence), gas=RULE_GAS
        )
        if not any(e["event"] == "rule" for e in call.events):
            raise SettlementError(f"transaction {call.tx_hash} has no rule event")
        return call

    def close_bounty(self, owner_id: str, key: SigningKey, bounty_id: str) -> EscrowCall:
        args = {"bounty_id": bounty_id}
        return self._call(owner_id, key, "close_bounty", args, gas=CLOSE_GAS, deposit=ONE_YOCTO)

    def withdraw_remaining(self, signer_id: str, key: SigningKey, bounty_id: str) -> EscrowCall:
        args = {"bounty_id": bounty_id}
        return self._call(signer_id, key, "withdraw_remaining", args, gas=PAYOUT_CALL_GAS)

    def reclaim_bond(self, signer_id: str, key: SigningKey, claim_id: str) -> EscrowCall:
        args = {"claim_id": claim_id}
        return self._call(signer_id, key, "reclaim_bond", args, gas=PAYOUT_CALL_GAS)

    def retry_payout(self, signer_id: str, key: SigningKey, account_id: str) -> EscrowCall:
        args = {"account_id": account_id}
        return self._call(signer_id, key, "retry_payout", args, gas=PAYOUT_CALL_GAS)

    def deposit(self, signer_id: str, key: SigningKey, amount: int, msg: str) -> EscrowCall:
        """mt_transfer_call from the signer's own intents.near balance."""
        args = mt_transfer_call_args(self.contract_id, self.token_id, amount, msg)
        tx_hash, outcome = self.rpc.call_function(
            signer_id,
            key,
            self.intents_contract,
            "mt_transfer_call",
            args,
            gas=DEPOSIT_GAS,
            deposit=ONE_YOCTO,
        )
        call = EscrowCall(tx_hash, _return_value(outcome), escrow_events(outcome))
        _refused(call.events)
        return call

    def relay_deposit(
        self,
        signed: dict,
        *,
        sender: str,
        amount: int,
        msg: str,
        memo: str,
        relayer_id: str | None = None,
        private_key: str | SecretStr | None = None,
    ) -> EscrowCall:
        """Check a hunter's or funder's signed deposit, simulate it, relay it with
        execute_intents, and raise if the escrow refunded it."""
        verify_deposit(
            signed,
            sender=sender,
            escrow=self.contract_id,
            token_id=self.token_id,
            amount=amount,
            msg=msg,
            memo=memo,
            contract=self.intents_contract,
        )
        ni.simulate([signed], rpc=self.rpc, contract=self.intents_contract)
        key = ni.load_signing_key(private_key or settings.near_private_key)
        tx_hash, outcome = self.rpc.call_function(
            relayer_id or settings.near_account_id,
            key,
            self.intents_contract,
            "execute_intents",
            {"signed": [signed]},
            gas=DEPOSIT_GAS,
            deposit=0,
        )
        if ni.signer_and_nonce(signed) not in ni.executed_intents(outcome):
            raise SettlementError(f"transaction {tx_hash} did not execute the deposit")
        call = EscrowCall(tx_hash, None, escrow_events(outcome))
        _refused(call.events)
        accepted = {"fund", "bond"}
        if not any(e["event"] in accepted for e in call.events):
            raise SettlementError(f"transaction {tx_hash} has no escrow deposit event")
        return call
