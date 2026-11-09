"""NEAR keys, JSON-RPC and function-call transactions.

Arsune only ever sends one action type (FunctionCall), so a tiny hand-written
Borsh encoder on top of PyNaCl is easier to audit than a full NEAR SDK.
Layout checked against the near-api-js serialization test vectors.
"""

import base64
import hashlib
import json
import struct
import threading
import time
from typing import Any

import base58
import httpx
from nacl.signing import SigningKey
from pydantic import SecretStr

from app.settlement.base import SettlementError

ED25519 = "ed25519:"
TGAS = 10**12
FINAL = "FINAL"
WAIT_SECONDS = 90
# One relayer key cannot send two transactions with the same access-key nonce.
_SEND_LOCK = threading.Lock()


class OutcomeUnknown(SettlementError):
    """The transaction may have executed. Do not retry blindly; look up tx_hash first."""

    def __init__(self, message: str, tx_hash: str):
        super().__init__(f"{message} (tx {tx_hash})")
        self.tx_hash = tx_hash


class RpcUnavailable(SettlementError):
    pass


class NearRpcError(SettlementError):
    def __init__(self, error: dict):
        cause = error.get("cause") or {}
        self.cause = cause.get("name") or error.get("name") or "UNKNOWN"
        detail = error.get("data") or cause.get("info") or error.get("message")
        super().__init__(f"NEAR RPC {self.cause}: {_short(detail)}")


def _short(value: Any, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text[:limit]


def secret_text(value: str | SecretStr) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def decode_ed25519(text: Any, size: int, what: str) -> bytes:
    if not isinstance(text, str) or not text.startswith(ED25519):
        raise SettlementError(f"{what} must be an ed25519: base58 string")
    try:
        raw = base58.b58decode(text[len(ED25519) :])
    except ValueError as error:
        raise SettlementError(f"{what} is not valid base58") from error
    if len(raw) != size:
        raise SettlementError(f"{what} must be {size} bytes, got {len(raw)}")
    return raw


def encode_ed25519(raw: bytes) -> str:
    return ED25519 + base58.b58encode(raw).decode()


def load_signing_key(private_key: str | SecretStr) -> SigningKey:
    """Accept NEAR's `ed25519:<base58 seed+public>` form or a bare 32-byte seed."""
    text = secret_text(private_key)
    if not text.startswith(ED25519):
        raise SettlementError("private key must start with ed25519:")
    try:
        raw = base58.b58decode(text[len(ED25519) :])
    except ValueError as error:
        raise SettlementError("private key is not valid base58") from error
    if len(raw) not in (32, 64):
        raise SettlementError("private key must be 32 or 64 bytes")
    key = SigningKey(raw[:32])
    if len(raw) == 64 and bytes(key.verify_key) != raw[32:]:
        raise SettlementError("private key does not match its public half")
    return key


def public_key_text(key: SigningKey) -> str:
    return encode_ed25519(bytes(key.verify_key))


def implicit_account_id(key: SigningKey) -> str:
    return bytes(key.verify_key).hex()


def generate_key() -> tuple[str, str]:
    """Return (private_key, public_key) in NEAR text form."""
    key = SigningKey.generate()
    return encode_ed25519(bytes(key) + bytes(key.verify_key)), public_key_text(key)


def _string(value: str) -> bytes:
    data = value.encode()
    return struct.pack("<I", len(data)) + data


def function_call_tx(
    signer_id: str,
    public_key: bytes,
    nonce: int,
    receiver_id: str,
    block_hash: bytes,
    method: str,
    args: bytes,
    gas: int,
    deposit: int,
) -> bytes:
    """Borsh-encode a v0 Transaction with a single FunctionCall action."""
    action = (
        b"\x02"
        + _string(method)
        + struct.pack("<I", len(args))
        + args
        + struct.pack("<Q", gas)
        + deposit.to_bytes(16, "little")
    )
    return (
        _string(signer_id)
        + b"\x00"
        + public_key
        + struct.pack("<Q", nonce)
        + _string(receiver_id)
        + block_hash
        + struct.pack("<I", 1)
        + action
    )


def sign_tx(tx: bytes, key: SigningKey) -> tuple[str, bytes]:
    """Return (tx hash, Borsh SignedTransaction)."""
    digest = hashlib.sha256(tx).digest()
    signature = key.sign(digest).signature
    return base58.b58encode(digest).decode(), tx + b"\x00" + signature


def check_outcome(outcome: dict, tx_hash: str) -> None:
    """Raise unless the final outcome shows every step of tx_hash succeeded."""
    if outcome.get("transaction", {}).get("hash") != tx_hash:
        raise SettlementError(f"RPC returned an outcome for another transaction than {tx_hash}")
    status = outcome.get("status")
    if not isinstance(status, dict) or "SuccessValue" not in status:
        raise SettlementError(f"transaction {tx_hash} failed: {_short(status)}")
    steps = [outcome.get("transaction_outcome", {})] + outcome.get("receipts_outcome", [])
    for step in steps:
        step_status = step.get("outcome", {}).get("status")
        if not isinstance(step_status, dict) or "Failure" in step_status:
            raise SettlementError(f"transaction {tx_hash} has a failed step: {_short(step_status)}")


def near_balance(rpc: "NearRpc", account_id: str) -> int:
    """Liquid NEAR of an account in yoctoNEAR; 0 if the account does not exist yet."""
    try:
        account = rpc.call(
            "query", {"request_type": "view_account", "finality": "final", "account_id": account_id}
        )
    except NearRpcError as error:
        if error.cause == "UNKNOWN_ACCOUNT":
            return 0
        raise
    return int(account["amount"])


def outcome_logs(outcome: dict) -> list[str]:
    return [log for step in outcome.get("receipts_outcome", []) for log in step["outcome"]["logs"]]


class NearRpc:
    def __init__(self, url: str, timeout: float = 30.0):
        self.url = url
        self.timeout = timeout

    def call(self, method: str, params: dict) -> Any:
        body = {"jsonrpc": "2.0", "id": "arsune", "method": method, "params": params}
        try:
            response = httpx.post(self.url, json=body, timeout=self.timeout)
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RpcUnavailable(f"NEAR RPC {method} failed: {error}") from error
        if isinstance(data, dict) and "error" in data:
            raise NearRpcError(data["error"])
        if not isinstance(data, dict) or "result" not in data:
            raise RpcUnavailable(f"NEAR RPC {method} gave HTTP {response.status_code}")
        return data["result"]

    def view(self, contract: str, method: str, args: dict) -> Any:
        result = self.call(
            "query",
            {
                "request_type": "call_function",
                "finality": "final",
                "account_id": contract,
                "method_name": method,
                "args_base64": base64.b64encode(json.dumps(args).encode()).decode(),
            },
        )
        if "error" in result:
            raise SettlementError(f"{contract}.{method} failed: {_short(result['error'])}")
        return json.loads(bytes(result["result"]).decode())

    def call_function(
        self,
        signer_id: str,
        key: SigningKey,
        receiver_id: str,
        method: str,
        args: dict,
        *,
        gas: int,
        deposit: int = 0,
    ) -> tuple[str, dict]:
        """Sign, send and wait for FINAL. Return (tx hash, outcome) only on full success."""
        with _SEND_LOCK:
            access = self.call(
                "query",
                {
                    "request_type": "view_access_key",
                    "finality": "final",
                    "account_id": signer_id,
                    "public_key": public_key_text(key),
                },
            )
            tx = function_call_tx(
                signer_id,
                bytes(key.verify_key),
                access["nonce"] + 1,
                receiver_id,
                base58.b58decode(access["block_hash"]),
                method,
                json.dumps(args, separators=(",", ":")).encode(),
                gas,
                deposit,
            )
            tx_hash, signed = sign_tx(tx, key)
            outcome = self._submit(base64.b64encode(signed).decode(), tx_hash, signer_id)
        check_outcome(outcome, tx_hash)
        return tx_hash, outcome

    def _submit(self, signed_b64: str, tx_hash: str, signer_id: str) -> dict:
        try:
            outcome = self.call("send_tx", {"signed_tx_base64": signed_b64, "wait_until": FINAL})
            if outcome.get("final_execution_status") == FINAL:
                return outcome
        except NearRpcError as error:
            if error.cause in {"INVALID_TRANSACTION", "REQUEST_VALIDATION_ERROR"}:
                raise SettlementError(f"transaction {tx_hash} was rejected: {error}") from error
        except RpcUnavailable:
            pass
        return self._wait_for_final(signed_b64, tx_hash, signer_id)

    def _wait_for_final(self, signed_b64: str, tx_hash: str, signer_id: str) -> dict:
        """Poll `tx`; re-broadcast the same signed bytes (same hash, cannot run twice)."""
        give_up = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < give_up:
            try:
                outcome = self.call(
                    "tx",
                    {"tx_hash": tx_hash, "sender_account_id": signer_id, "wait_until": FINAL},
                )
                if outcome.get("final_execution_status") == FINAL:
                    return outcome
            except (NearRpcError, RpcUnavailable):
                try:
                    self.call("send_tx", {"signed_tx_base64": signed_b64, "wait_until": "NONE"})
                except SettlementError:
                    pass
            time.sleep(2)
        raise OutcomeUnknown("no final outcome from NEAR RPC", tx_hash)
