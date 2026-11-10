"""Ethereum wallet (ERC-191 `personal_sign`) keys and signatures for NEAR Intents.

intents.near recovers the secp256k1 public key from
keccak256("\\x19Ethereum Signed Message:\\n" + len(payload) + payload) and a 65-byte
r | s | v signature with v in {0, 1}. The signer's implicit account is
"0x" + lowercase hex(keccak256(public key x | y)[12:]), the Ethereum address.
Sources: near/intents crates/signatures/erc191 and core/src/public_key.rs.
"""

import re

import base58
from coincurve import PrivateKey, PublicKey
from Crypto.Hash import keccak
from pydantic import SecretStr

from app.settlement.base import SettlementError
from app.settlement.near_tx import secret_text

SECP256K1 = "secp256k1:"
CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
EVM_ADDRESS = re.compile(r"0x[0-9a-f]{40}")
PRIVATE_KEY = re.compile(r"(0x)?[0-9a-fA-F]{64}")


def keccak256(data: bytes) -> bytes:
    return keccak.new(digest_bits=256, data=data).digest()


def erc191_hash(message: str) -> bytes:
    data = message.encode()
    return keccak256(b"\x19Ethereum Signed Message:\n" + str(len(data)).encode() + data)


def evm_address(public_key: bytes) -> str:
    """Lowercase 0x address of a 64-byte uncompressed public key (no 0x04 prefix)."""
    return "0x" + keccak256(public_key)[12:].hex()


def load_evm_key(private_key: str | SecretStr) -> PrivateKey:
    """Accept a 32-byte private key as 64 hex characters, with or without 0x."""
    text = secret_text(private_key)
    if not PRIVATE_KEY.fullmatch(text):
        raise SettlementError("EVM private key must be 32 bytes of hex")
    try:
        return PrivateKey(bytes.fromhex(text.removeprefix("0x")))
    except ValueError as error:
        raise SettlementError("EVM private key is out of range") from error


def key_address(key: PrivateKey) -> str:
    return evm_address(key.public_key.format(compressed=False)[1:])


def generate_evm_key() -> tuple[str, str]:
    """Return (0x private key hex, lowercase 0x address)."""
    key = PrivateKey()
    return "0x" + key.secret.hex(), key_address(key)


def sign(key: PrivateKey, message: str) -> bytes:
    """65-byte r | s | v with low s and v in {0, 1} (libsecp256k1, RFC 6979 nonces)."""
    return key.sign_recoverable(erc191_hash(message), hasher=None)


def normalize_signature(signature: bytes) -> bytes:
    """Map a wallet's `personal_sign` v of 27/28 to the 0/1 that the Verifier expects."""
    if len(signature) != 65:
        raise SettlementError("secp256k1 signature must be 65 bytes")
    v = signature[64]
    if v in (27, 28):
        v -= 27
    if v not in (0, 1):
        raise SettlementError(f"unknown recovery byte v={signature[64]}")
    return signature[:64] + bytes([v])


def encode_signature(signature: bytes) -> str:
    return SECP256K1 + base58.b58encode(signature).decode()


def decode_signature(text: object) -> bytes:
    if not isinstance(text, str) or not text.startswith(SECP256K1):
        raise SettlementError("signature must be a secp256k1: base58 string")
    try:
        raw = base58.b58decode(text[len(SECP256K1) :])
    except ValueError as error:
        raise SettlementError("signature is not valid base58") from error
    if len(raw) != 65:
        raise SettlementError(f"signature must be 65 bytes, got {len(raw)}")
    return raw


def recover_address(message: str, signature: bytes) -> str:
    """Return the 0x address that signed `message`, with the Verifier's own limits:
    v must be 0 or 1, and s must be in the lower half (no malleable twin)."""
    if signature[64] not in (0, 1):
        raise SettlementError("signature recovery byte v must be 0 or 1 (normalize 27/28)")
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:64], "big")
    if not 0 < r < CURVE_ORDER or not 0 < s <= CURVE_ORDER // 2:
        raise SettlementError("signature r or s is out of range (s must be low)")
    try:
        public_key = PublicKey.from_signature_and_message(
            signature, erc191_hash(message), hasher=None
        )
    except ValueError as error:
        raise SettlementError("signature does not match the signed message") from error
    return evm_address(public_key.format(compressed=False)[1:])
