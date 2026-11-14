"""USDC settlement inside the NEAR Intents Verifier contract (intents.near, mainnet only).

Every money movement is a signed `transfer` intent passed to `execute_intents`.
Hunter agents build the bond they send to Arsune with `sign_transfer` (NEAR key,
NEP-413) or `sign_transfer_erc191` (Ethereum key, ERC-191, signer = its 0x address).
Arsune signs its own payouts with NEP-413 and uses `verify_signed_transfer`,
`simulate` and `execute`. See docs/SETTLEMENT.md for the payload formats and sources.
"""

import base64
import binascii
import hashlib
import json
import secrets
import struct
from datetime import UTC, datetime, timedelta

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from pydantic import SecretStr

from app.core.config import Settings, settings
from app.settlement import erc191
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    TGAS,
    NearRpc,
    decode_ed25519,
    encode_ed25519,
    function_call_action,
    load_signing_key,
    outcome_logs,
    public_key_text,
)

NEP413_TAG = 2**31 + 413
VERSIONED_NONCE_MAGIC = bytes.fromhex("5628f6c6")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Mainnet transfer-only execute_intents burn about 4-8 Tgas and add_public_key about 3.3 Tgas
# (nearblocks samples, docs/SETTLEMENT.md). Attached gas must be held up front at the minimum
# gas purchase price (0.001 NEAR/Tgas), so keep it a few times the real need, not 100+ Tgas.
EXECUTE_GAS = 30 * TGAS
EXECUTE_DEPOSIT = 0  # execute_intents is not payable; only gas is spent
ADD_KEY_GAS = 10 * TGAS
PAY_DEADLINE = timedelta(minutes=5)
SIGNED_KEYS = {"standard", "payload", "public_key", "signature"}
PAYLOAD_KEYS = {"recipient", "nonce", "message"}
MESSAGE_KEYS = {"signer_id", "deadline", "intents"}
# An erc191 payload is one JSON string; the public key is recovered from the signature.
ERC191_SIGNED_KEYS = {"standard", "payload", "signature"}
ERC191_PAYLOAD_KEYS = MESSAGE_KEYS | {"verifying_contract", "nonce"}
TRANSFER_KEYS = {"intent", "receiver_id", "tokens", "memo"}


def _rpc(rpc: NearRpc | None) -> NearRpc:
    return rpc or NearRpc(settings.near_rpc_url)


def _contract(contract: str | None) -> str:
    return contract or settings.intents_contract


def nep413_hash(message: str, nonce: bytes, recipient: str) -> bytes:
    """sha256(borsh(tag, payload)) with no callbackUrl, as in defuse-nep413."""

    def string(value: str) -> bytes:
        data = value.encode()
        return struct.pack("<I", len(data)) + data

    data = struct.pack("<I", NEP413_TAG) + string(message) + nonce + string(recipient) + b"\x00"
    return hashlib.sha256(data).digest()


def format_deadline(deadline: datetime) -> str:
    return deadline.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def versioned_nonce(salt: bytes, deadline: datetime) -> bytes:
    """V1 nonce: magic | version 0 | salt | deadline ns (i64 LE) | 15 random bytes."""
    if len(salt) != 4:
        raise SettlementError("salt must be 4 bytes")
    nanos = (deadline - EPOCH) // timedelta(microseconds=1) * 1000
    return (
        VERSIONED_NONCE_MAGIC + b"\x00" + salt + struct.pack("<q", nanos) + secrets.token_bytes(15)
    )


def check_amount(amount: int) -> None:
    if type(amount) is not int or amount <= 0:
        raise SettlementError("amount must be a positive integer of the token's smallest unit")


def current_salt(*, rpc: NearRpc | None = None, contract: str | None = None) -> bytes:
    return bytes.fromhex(_rpc(rpc).view(_contract(contract), "current_salt", {}))


def _signing_inputs(
    deadline: datetime, salt: bytes | None, contract: str | None, rpc: NearRpc | None
) -> tuple[datetime, bytes, str]:
    if deadline.tzinfo is None:
        raise SettlementError("deadline must be timezone-aware")
    contract = _contract(contract)
    if salt is None:
        salt = current_salt(rpc=rpc, contract=contract)
    return deadline.astimezone(UTC), salt, contract


def sign_intents(
    account_id: str,
    private_key: str | SecretStr,
    intents: list[dict],
    deadline: datetime,
    *,
    salt: bytes | None = None,
    contract: str | None = None,
    rpc: NearRpc | None = None,
) -> dict:
    """Return a NEP-413 signed payload for execute_intents / simulate_intents.

    `salt` is the Verifier's current 4-byte salt; it is fetched over RPC when omitted.
    """
    key = load_signing_key(private_key)
    payload = nep413_payload(account_id, intents, deadline, salt=salt, contract=contract, rpc=rpc)
    nonce = base64.b64decode(payload["nonce"])
    signature = key.sign(nep413_hash(payload["message"], nonce, payload["recipient"])).signature
    return {
        "standard": "nep413",
        "payload": payload,
        "public_key": public_key_text(key),
        "signature": encode_ed25519(signature),
    }


def nep413_payload(
    account_id: str,
    intents: list[dict],
    deadline: datetime,
    *,
    salt: bytes | None = None,
    contract: str | None = None,
    rpc: NearRpc | None = None,
) -> dict:
    """The unsigned NEP-413 payload {recipient, nonce (base64), message} for these intents.

    A wallet's signMessage(message, recipient, nonce bytes) signs exactly what
    `sign_intents` signs, so its signature passes `verify_signed_transfer`.
    """
    deadline, salt, contract = _signing_inputs(deadline, salt, contract, rpc)
    message = json.dumps(
        {"deadline": format_deadline(deadline), "intents": intents, "signer_id": account_id},
        separators=(",", ":"),
        sort_keys=True,
    )
    nonce = versioned_nonce(salt, deadline)
    return {"recipient": contract, "nonce": base64.b64encode(nonce).decode(), "message": message}


def sign_transfer(
    account_id: str,
    private_key: str | SecretStr,
    receiver_id: str,
    token_id: str,
    amount: int,
    memo: str,
    deadline: datetime,
    *,
    salt: bytes | None = None,
    contract: str | None = None,
    rpc: NearRpc | None = None,
) -> dict:
    """Return a signed `transfer` of `amount` (smallest unit) of `token_id` to `receiver_id`."""
    intent = transfer_intent(receiver_id, token_id, amount, memo)
    return sign_intents(
        account_id, private_key, [intent], deadline, salt=salt, contract=contract, rpc=rpc
    )


def transfer_intent(receiver_id: str, token_id: str, amount: int, memo: str) -> dict:
    check_amount(amount)
    return {
        "intent": "transfer",
        "receiver_id": receiver_id,
        "tokens": {token_id: str(amount)},
        "memo": memo,
    }


def sign_intents_erc191(
    private_key: str | SecretStr,
    intents: list[dict],
    deadline: datetime,
    salt: bytes | None = None,
    *,
    contract: str | None = None,
    rpc: NearRpc | None = None,
) -> dict:
    """Return an ERC-191 signed payload from an Ethereum key (hex). The signer_id is the
    key's lowercase 0x address, an implicit account that needs no key registration."""
    key = erc191.load_evm_key(private_key)
    deadline, salt, contract = _signing_inputs(deadline, salt, contract, rpc)
    payload = json.dumps(
        {
            "deadline": format_deadline(deadline),
            "intents": intents,
            "nonce": base64.b64encode(versioned_nonce(salt, deadline)).decode(),
            "signer_id": erc191.key_address(key),
            "verifying_contract": contract,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    signature = erc191.sign(key, payload)
    return {
        "standard": "erc191",
        "payload": payload,
        "signature": erc191.encode_signature(signature),
    }


def sign_transfer_erc191(
    private_key_hex: str | SecretStr,
    receiver_id: str,
    token_id: str,
    amount: int,
    memo: str,
    deadline: datetime,
    salt: bytes | None = None,
    *,
    contract: str | None = None,
    rpc: NearRpc | None = None,
) -> dict:
    """`sign_transfer` for a hunter that holds only an Ethereum key."""
    intent = transfer_intent(receiver_id, token_id, amount, memo)
    return sign_intents_erc191(
        private_key_hex, [intent], deadline, salt, contract=contract, rpc=rpc
    )


def _no_duplicate_keys(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise SettlementError("signed message has duplicate JSON keys")
    return dict(pairs)


def _exact_keys(value: object, keys: set[str], what: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise SettlementError(f"{what} must have exactly the keys {sorted(keys)}")
    return value


def _parse_deadline(text: object) -> datetime:
    if not isinstance(text, str):
        raise SettlementError("deadline must be a string")
    try:
        deadline = datetime.fromisoformat(text)
    except ValueError as error:
        raise SettlementError("deadline is not an ISO 8601 timestamp") from error
    if deadline.tzinfo is None:
        raise SettlementError("deadline has no timezone")
    return deadline


def _decode_nonce(text: object) -> bytes:
    try:
        nonce = base64.b64decode(text, validate=True)
    except (TypeError, binascii.Error) as error:
        raise SettlementError("nonce is not base64") from error
    if len(nonce) != 32 or base64.b64encode(nonce).decode() != text:
        raise SettlementError("nonce must be 32 bytes in canonical base64")
    return nonce


def _load_json(text: str, what: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except ValueError as error:
        raise SettlementError(f"{what} is not valid JSON") from error


def signer_and_nonce(signed: dict) -> tuple[str, str]:
    """(signer_id, base64 nonce) of a nep413 or erc191 payload, as intents_executed lists them."""
    try:
        if signed["standard"] == "erc191":
            payload = json.loads(signed["payload"])
            return payload["signer_id"], payload["nonce"]
        return json.loads(signed["payload"]["message"])["signer_id"], signed["payload"]["nonce"]
    except (KeyError, TypeError, ValueError) as error:
        raise SettlementError("signed intent is not a nep413 or erc191 payload") from error


def signed_nonce(signed: dict) -> str:
    return signer_and_nonce(signed)[1]


def _verify_nep413(signed: dict, contract: str) -> dict:
    """Check the envelope and ed25519 signature; return the signed message."""
    signed = _exact_keys(signed, SIGNED_KEYS, "signed intent")
    payload = _exact_keys(signed["payload"], PAYLOAD_KEYS, "payload")
    if payload["recipient"] != contract:
        raise SettlementError("signed intent is for another verifying contract")
    nonce = _decode_nonce(payload["nonce"])
    public_key = decode_ed25519(signed["public_key"], 32, "public_key")
    signature = decode_ed25519(signed["signature"], 64, "signature")
    if not isinstance(payload["message"], str):
        raise SettlementError("message must be a JSON string")
    digest = nep413_hash(payload["message"], nonce, payload["recipient"])
    try:
        VerifyKey(public_key).verify(digest, signature)
    except BadSignatureError as error:
        raise SettlementError("signature does not match the signed message") from error
    return _exact_keys(_load_json(payload["message"], "message"), MESSAGE_KEYS, "message")


def _verify_erc191(signed: dict, contract: str) -> dict:
    """Recover the signer's 0x address; return the payload only if it is the signer_id.

    Only implicit 0x accounts are accepted: a secp256k1 key registered on a named
    account would pass the Verifier but not this check.
    """
    signed = _exact_keys(signed, ERC191_SIGNED_KEYS, "signed intent")
    if not isinstance(signed["payload"], str):
        raise SettlementError("erc191 payload must be a JSON string")
    signer = erc191.recover_address(signed["payload"], erc191.decode_signature(signed["signature"]))
    payload = _exact_keys(_load_json(signed["payload"], "payload"), ERC191_PAYLOAD_KEYS, "payload")
    if payload["signer_id"] != signer:
        raise SettlementError("signature does not match the signer_id address")
    if payload["verifying_contract"] != contract:
        raise SettlementError("signed intent is for another verifying contract")
    _decode_nonce(payload["nonce"])
    return payload


def verify_signed_transfer(
    signed: dict,
    *,
    sender: str,
    receiver: str,
    token_id: str,
    amount: int,
    memo: str,
    contract: str | None = None,
    now: datetime | None = None,
) -> None:
    """Raise SettlementError unless `signed` is a valid, unexpired NEP-413 or ERC-191
    transfer of exactly `amount` of `token_id` from `sender` to `receiver` with exactly `memo`.

    Key ownership, balance, salt and nonce reuse are checked by simulate().
    """
    check_amount(amount)
    if not isinstance(signed, dict):
        raise SettlementError("signed intent must be a JSON object")
    if signed.get("standard") == "nep413":
        message = _verify_nep413(signed, _contract(contract))
    elif signed.get("standard") == "erc191":
        message = _verify_erc191(signed, _contract(contract))
    else:
        raise SettlementError("signed intent must use the nep413 or erc191 standard")
    if message["signer_id"] != sender:
        raise SettlementError("signer_id is not the expected sender")
    if _parse_deadline(message["deadline"]) <= (now or datetime.now(UTC)):
        raise SettlementError("signed intent deadline has passed")
    intents = message["intents"]
    if not isinstance(intents, list) or len(intents) != 1:
        raise SettlementError("signed message must hold exactly one intent")
    intent = _exact_keys(intents[0], TRANSFER_KEYS, "transfer intent")
    if intent["intent"] != "transfer":
        raise SettlementError("intent must be a transfer")
    if intent["receiver_id"] != receiver:
        raise SettlementError("transfer receiver is not the expected account")
    if intent["tokens"] != {token_id: str(amount)}:
        raise SettlementError("transfer token or amount does not match")
    if intent["memo"] != memo:
        raise SettlementError("transfer memo does not match")


def _executed_nonces(entries: list[dict]) -> set[tuple[str, str]]:
    return {(entry["account_id"], entry["nonce"]) for entry in entries}


def _expected_nonces(signed_list: list[dict]) -> set[tuple[str, str]]:
    return {signer_and_nonce(signed) for signed in signed_list}


def simulate(
    signed_list: list[dict], *, rpc: NearRpc | None = None, contract: str | None = None
) -> dict:
    """Run simulate_intents (read-only). Raise SettlementError with the Verifier's reason."""
    result = _rpc(rpc).view(_contract(contract), "simulate_intents", {"signed": signed_list})
    if result.get("invariant_violated"):
        raise SettlementError(f"simulation is unbalanced: {result['invariant_violated']}")
    if not _expected_nonces(signed_list) <= _executed_nonces(result.get("intents_executed", [])):
        raise SettlementError("simulation did not execute every signed intent")
    return result


def executed_intents(outcome: dict) -> set[tuple[str, str]]:
    """(account_id, nonce) pairs from the dip4 `intents_executed` events of an outcome."""
    executed: set[tuple[str, str]] = set()
    for log in outcome_logs(outcome):
        if not log.startswith("EVENT_JSON:"):
            continue
        event = json.loads(log.removeprefix("EVENT_JSON:"))
        if event.get("standard") == "dip4" and event.get("event") == "intents_executed":
            executed |= _executed_nonces(event["data"])
    return executed


def execute(
    signed_list: list[dict],
    *,
    account_id: str | None = None,
    private_key: str | SecretStr | None = None,
    rpc: NearRpc | None = None,
    contract: str | None = None,
    gas: int = EXECUTE_GAS,
) -> str:
    """Send execute_intents from the relayer account and return the tx hash once the final
    outcome shows success and every signed intent in the executed events."""
    key = load_signing_key(private_key or settings.near_private_key)
    tx_hash, outcome = _rpc(rpc).call_function(
        account_id or settings.near_account_id,
        key,
        _contract(contract),
        "execute_intents",
        {"signed": signed_list},
        gas=gas,
        deposit=EXECUTE_DEPOSIT,
    )
    if not _expected_nonces(signed_list) <= executed_intents(outcome):
        raise SettlementError(f"transaction {tx_hash} succeeded without executing every intent")
    return tx_hash


def balance(
    account_id: str,
    token_id: str | None = None,
    *,
    rpc: NearRpc | None = None,
    contract: str | None = None,
) -> int:
    args = {"account_id": account_id, "token_id": token_id or settings.bounty_token}
    return int(_rpc(rpc).view(_contract(contract), "mt_balance_of", args))


def has_public_key(
    account_id: str, public_key: str, *, rpc: NearRpc | None = None, contract: str | None = None
) -> bool:
    args = {"account_id": account_id, "public_key": public_key}
    return bool(_rpc(rpc).view(_contract(contract), "has_public_key", args))


def add_public_key_action(public_key: str) -> bytes:
    """FunctionCall action for intents.near add_public_key (1 yoctoNEAR), e.g. for relay()."""
    args = json.dumps({"public_key": public_key}, separators=(",", ":")).encode()
    return function_call_action("add_public_key", args, ADD_KEY_GAS, 1)


def register_public_key(
    account_id: str,
    private_key: str | SecretStr,
    public_key: str | None = None,
    *,
    rpc: NearRpc | None = None,
    contract: str | None = None,
) -> str:
    """Call add_public_key (1 yoctoNEAR) so a named account's key can sign intents.

    Implicit (64-hex) accounts do not need this. Sends a real mainnet transaction.
    """
    key = load_signing_key(private_key)
    tx_hash, _ = _rpc(rpc).call_function(
        account_id,
        key,
        _contract(contract),
        "add_public_key",
        {"public_key": public_key or public_key_text(key)},
        gas=ADD_KEY_GAS,
        deposit=1,
    )
    return tx_hash


class NearIntentsSettlement:
    """Settlement over NEAR Intents. The memo is the bounty layer's idempotency key and is
    passed through verbatim. An OutcomeUnknown error means the transfer may have happened."""

    mode = "near_intents"

    def __init__(self, config: Settings | None = None, rpc: NearRpc | None = None):
        config = config or settings
        if not config.near_account_id or not config.near_private_key.get_secret_value():
            raise SettlementError(
                "near_intents settlement needs ARSUNE_NEAR_ACCOUNT_ID and ARSUNE_NEAR_PRIVATE_KEY"
            )
        load_signing_key(config.near_private_key)
        self.account_id = config.near_account_id
        self._private_key = config.near_private_key
        self.contract = config.intents_contract
        self.token = config.bounty_token
        self.max_amount = config.max_payment_amount
        self.rpc = rpc or NearRpc(config.near_rpc_url)

    def _guard(self, amount: int) -> None:
        check_amount(amount)
        if amount > self.max_amount:
            raise SettlementError(f"amount {amount} is above max_payment_amount {self.max_amount}")

    def _settle(self, signed: dict) -> str:
        simulate([signed], rpc=self.rpc, contract=self.contract)
        return execute(
            [signed],
            account_id=self.account_id,
            private_key=self._private_key,
            rpc=self.rpc,
            contract=self.contract,
        )

    def collect(self, signed_intent: dict, *, sender: str, amount: int, memo: str) -> str:
        self._guard(amount)
        verify_signed_transfer(
            signed_intent,
            sender=sender,
            receiver=self.account_id,
            token_id=self.token,
            amount=amount,
            memo=memo,
            contract=self.contract,
        )
        return self._settle(signed_intent)

    def pay(self, receiver: str, amount: int, memo: str) -> str:
        self._guard(amount)
        if receiver == self.account_id:
            raise SettlementError("cannot pay Arsune's own account")
        signed = sign_transfer(
            self.account_id,
            self._private_key,
            receiver,
            self.token,
            amount,
            memo,
            datetime.now(UTC) + PAY_DEADLINE,
            contract=self.contract,
            rpc=self.rpc,
        )
        return self._settle(signed)
