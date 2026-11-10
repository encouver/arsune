"""ERC-191 (Ethereum wallet) intents: published vectors, signing, checks, settlements,
bounty intake, and read-only mainnet checks (marker `network`)."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import base58
import pytest
from app.bounty import settlement as bounty_settlement
from app.core.config import Settings, settings
from app.core.schemas.bounties import CreateClaim
from app.settlement import erc191
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.dry import DrySettlement
from app.settlement.near_tx import NearRpc, generate_key
from app.v1.services import bounties as bounty_service
from fastapi import HTTPException
from pydantic import SecretStr

TOKEN = "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
SALT = bytes.fromhex("252812b3")
ARSUNE = "arsune.near"
MEMO = "arsune:bond:claim-1"
AMOUNT = 50_000
HUNTER_KEY, HUNTER = erc191.generate_evm_key()
OTHER_KEY, OTHER = erc191.generate_evm_key()


def future(minutes=5):
    return datetime.now(UTC) + timedelta(minutes=minutes)


def hunter_bond(receiver_id=ARSUNE, token_id=TOKEN, amount=AMOUNT, memo=MEMO, **options):
    deadline = options.pop("deadline", future())
    return ni.sign_transfer_erc191(
        HUNTER_KEY, receiver_id, token_id, amount, memo, deadline, SALT, **options
    )


def verify(signed, sender=HUNTER, **options):
    ni.verify_signed_transfer(
        signed, sender=sender, receiver=ARSUNE, token_id=TOKEN, amount=AMOUNT, memo=MEMO, **options
    )


def sign_payload(payload, key=HUNTER_KEY):
    signature = erc191.sign(erc191.load_evm_key(key), payload)
    return {
        "standard": "erc191",
        "payload": payload,
        "signature": erc191.encode_signature(signature),
    }


def edited(signed, edit, key=None):
    """Change the payload; re-sign it with `key`, or keep the old signature when key is None."""
    payload = json.loads(signed["payload"])
    edit(payload)
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return sign_payload(text, key) if key else {**signed, "payload": text}


def set_intent(field, value):
    return lambda payload: payload["intents"][0].__setitem__(field, value)


def with_signature(signed, raw):
    return {**signed, "signature": erc191.encode_signature(bytes(raw))}


def checksum_case(address):
    """An address with capital letters, like EIP-55. All hex letters are upper case, so a
    random address always gets at least one capital letter."""
    return "0x" + address[2:].upper()


# Published vectors -----------------------------------------------------------


def test_erc191_recover_vector_from_defuse_erc191_crate():
    # near/intents crates/signatures/erc191/src/lib.rs, test recover_ok
    public_key = bytes.fromhex(
        "85a66984273f338ce4ef7b85e5430b008307e8591bb7c1b980852cf6423770b8"
        "01f41e9438155eb53a5e20f748640093bb42ae3aeca035f7b7fd7a1a21f22f68"
    )
    signature = bytes.fromhex(
        "7800a70d05cde2c49ed546a6ce887ce6027c2c268c0285f6efef0cdfc4366b23"
        "643790f67a86468ee8301ed12cfffcb07c6530f90a9327ec057800fabd332e4701"
    )
    assert erc191.recover_address("Hello world!", signature) == erc191.evm_address(public_key)


def test_implicit_account_vector_from_defuse_public_key():
    # near/intents contracts/defuse/core/src/public_key.rs, test to_implicit_account_id
    public_key = base58.b58decode(
        "3aMVMxsoAnHUbweXMtdKaN1uJaNwsfKv7wnc97SDGjXhyK62VyJwhPUPLZefKVthcoUcuWK6cqkSU4M542ipNxS3"
    )
    assert erc191.evm_address(public_key) == "0xbff77166b39599e54e391156eef7b8191e02be92"


def test_personal_sign_vector_from_eth_account_with_v_28():
    # ethereum/eth-account eth_account/account.py, Account.recover_message doctest ("I♥SF")
    key = "0xb25c7db31feed9122727bf0939dc769a96564b2de4c4726d035b36ecf1e5b364"
    wallet_signature = bytes.fromhex(
        "e6ca9bba58c88611fad66a6ce8f996908195593807c4b38bd528d2cff09d4eb3"
        "3e5bfbbf4d3e39b1a2fd816a7680c19ebebaf3a141b239934ad43cb33fcec8ce1c"
    )
    address = "0x5ce9454909639d2d17a3f753ce7d93fa0b9ab12e"
    assert erc191.erc191_hash("I♥SF").hex() == (
        "1476abb745d423bf09273f1afd887d951181d25adc66c4834a70491911b7f750"
    )
    assert erc191.key_address(erc191.load_evm_key(key)) == address
    with pytest.raises(SettlementError, match="normalize"):
        erc191.recover_address("I♥SF", wallet_signature)
    normalized = erc191.normalize_signature(wallet_signature)
    assert normalized[64] == 1
    assert erc191.recover_address("I♥SF", normalized) == address
    # RFC 6979 nonces: our signature is the wallet's, already normalized.
    assert erc191.sign(erc191.load_evm_key(key), "I♥SF") == normalized


def test_near_intents_docs_example_signs_the_lowercase_address():
    # docs.near-intents.org/integration/verifier-contract/signing-intents.md (ERC-191 section).
    # The docs show a checksummed signer_id; intents.near rejects upper case in account IDs.
    signed = {
        "standard": "erc191",
        "payload": (
            '{"signer_id":"0xca67C1Bb3FD69857E5edaF6aA1c65371bF46A464","verifying_contract":'
            '"intents.near","deadline":"2025-05-26T13:24:16.983Z","nonce":'
            '"Vij2xgAlKBKzwGNqwogWQxiy87p9jW5Omfg+L9bXBDw=","intents":[{"intent":"token_diff",'
            '"diff":{"nep141:usdc.near":"-1000","nep141:usdt.near":"1000"}},{"intent":'
            '"ft_withdraw","token":"usdt.near","receiver_id":"bob.near","amount":"1000"}]}'
        ),
        "signature": "secp256k1:BoR53NKKJ2GfP8bETk427Xav8VMpUcZovLzXeq2EsUD7NnPH2sSmxWFEkDUrguJHHkiu3GwEQxxMo2Rm2ZTDHFygU",
    }
    signature = erc191.decode_signature(signed["signature"])
    recovered = erc191.recover_address(signed["payload"], signature)
    assert recovered == "0xca67c1bb3fd69857e5edaf6aa1c65371bf46a464"
    with pytest.raises(SettlementError, match="signer_id address"):
        verify(signed, sender=recovered)


# Keys and signatures ----------------------------------------------------------


def test_private_key_forms():
    raw = HUNTER_KEY.removeprefix("0x")
    for form in (HUNTER_KEY, raw, raw.upper(), SecretStr(HUNTER_KEY)):
        assert erc191.key_address(erc191.load_evm_key(form)) == HUNTER
    near_key, _ = generate_key()
    for bad, error in [
        (raw[:-2], "32 bytes"),
        ("0x" + "zz" * 32, "32 bytes"),
        (near_key, "32 bytes"),
        ("0" * 64, "out of range"),
        ("f" * 64, "out of range"),
    ]:
        with pytest.raises(SettlementError, match=error):
            erc191.load_evm_key(bad)


def test_normalize_signature():
    base = bytes(range(64))
    assert erc191.normalize_signature(base + b"\x1b")[64] == 0
    assert erc191.normalize_signature(base + b"\x1c")[64] == 1
    assert erc191.normalize_signature(base + b"\x00") == base + b"\x00"
    assert erc191.normalize_signature(base + b"\x01") == base + b"\x01"
    for v in (2, 26, 29, 37):
        with pytest.raises(SettlementError, match="recovery byte"):
            erc191.normalize_signature(base + bytes([v]))
    with pytest.raises(SettlementError, match="65 bytes"):
        erc191.normalize_signature(base)


# Sign and verify -------------------------------------------------------------


def test_sign_verify_round_trip_and_payload_layout():
    signed = hunter_bond()
    verify(signed)
    assert set(signed) == {"standard", "payload", "signature"}
    assert signed["standard"] == "erc191"
    assert HUNTER == HUNTER.lower() and erc191.EVM_ADDRESS.fullmatch(HUNTER)
    payload = json.loads(signed["payload"])
    assert signed["payload"] == json.dumps(payload, separators=(",", ":"), sort_keys=True)
    assert payload["signer_id"] == HUNTER
    assert payload["verifying_contract"] == "intents.near"
    nonce = base64.b64decode(payload["nonce"])
    assert len(nonce) == 32 and nonce[:5] == bytes.fromhex("5628f6c600") and nonce[5:9] == SALT
    assert payload["intents"] == [
        {"intent": "transfer", "receiver_id": ARSUNE, "tokens": {TOKEN: "50000"}, "memo": MEMO}
    ]
    signature = erc191.decode_signature(signed["signature"])
    assert signature[64] in (0, 1)
    assert int.from_bytes(signature[32:64], "big") <= erc191.CURVE_ORDER // 2
    assert ni.signer_and_nonce(signed) == (HUNTER, payload["nonce"])
    assert ni.signed_nonce(signed) == payload["nonce"]


def test_wallet_signature_with_v_27_or_28_verifies_after_normalizing():
    signed = hunter_bond()
    raw = erc191.decode_signature(signed["signature"])
    wallet = raw[:64] + bytes([raw[64] + 27])
    with pytest.raises(SettlementError, match="normalize"):
        verify(with_signature(signed, wallet))
    verify(with_signature(signed, erc191.normalize_signature(wallet)))


@pytest.mark.parametrize(
    "edit",
    [
        set_intent("tokens", {TOKEN: "50001"}),
        set_intent("memo", "arsune:bond:claim-2"),
        set_intent("receiver_id", "thief.near"),
        lambda payload: payload.__setitem__("signer_id", OTHER),
        lambda payload: payload.__setitem__("deadline", "2099-01-01T00:00:00.000000Z"),
        lambda payload: payload.__setitem__("nonce", base64.b64encode(bytes(32)).decode()),
        lambda payload: payload.__setitem__("verifying_contract", "intents.testnet"),
    ],
    ids=["amount", "memo", "receiver", "signer", "deadline", "nonce", "contract"],
)
def test_tampered_payload_breaks_signature(edit):
    with pytest.raises(SettlementError, match="signature"):
        verify(edited(hunter_bond(), edit))


def test_tampered_signature_fails():
    signed = hunter_bond()
    raw = erc191.decode_signature(signed["signature"])
    for index in (0, 40):
        flipped = bytearray(raw)
        flipped[index] ^= 1
        with pytest.raises(SettlementError, match="signature"):
            verify(with_signature(signed, flipped))
    s = int.from_bytes(raw[32:64], "big")
    high_s_twin = raw[:32] + (erc191.CURVE_ORDER - s).to_bytes(32, "big") + bytes([1 - raw[64]])
    with pytest.raises(SettlementError, match="s must be low"):
        verify(with_signature(signed, high_s_twin))
    with pytest.raises(SettlementError, match="out of range"):
        verify(with_signature(signed, bytes(32) + raw[32:]))
    # x = 7 is not on secp256k1, so no key can be recovered for any message.
    not_on_curve = (7).to_bytes(32, "big") + raw[32:]
    with pytest.raises(SettlementError, match="does not match the signed message"):
        verify(with_signature(signed, not_on_curve))
    with pytest.raises(SettlementError, match="65 bytes"):
        verify(with_signature(signed, raw[:64]))
    with pytest.raises(SettlementError, match="secp256k1"):
        verify({**signed, "signature": "ed25519:" + signed["signature"].split(":")[1]})


def test_wrong_signer_fails():
    claims_hunter = edited(hunter_bond(), lambda p: p.__setitem__("signer_id", HUNTER), OTHER_KEY)
    with pytest.raises(SettlementError, match="signer_id address"):
        verify(claims_hunter)
    by_other = ni.sign_transfer_erc191(OTHER_KEY, ARSUNE, TOKEN, AMOUNT, MEMO, future(), SALT)
    verify(by_other, sender=OTHER)
    with pytest.raises(SettlementError, match="expected sender"):
        verify(by_other)
    with pytest.raises(SettlementError, match="expected sender"):
        verify(hunter_bond(), sender=checksum_case(HUNTER))


@pytest.mark.parametrize(
    "signed_with,error",
    [
        ({"amount": AMOUNT - 1}, "amount"),
        ({"memo": "arsune:bond:claim-2"}, "memo"),
        ({"receiver_id": "thief.near"}, "receiver"),
        ({"token_id": "nep141:usdt.tether-token.near"}, "token"),
        ({"contract": "intents.testnet"}, "verifying contract"),
        ({"deadline": future(-1)}, "deadline"),
    ],
)
def test_correctly_signed_but_wrong_transfer_fails(signed_with, error):
    with pytest.raises(SettlementError, match=error):
        verify(hunter_bond(**signed_with))


def test_extra_fields_and_bad_shapes_are_rejected():
    def sign(intents):
        return ni.sign_intents_erc191(HUNTER_KEY, intents, future(), SALT)

    signed = hunter_bond()
    transfer = json.loads(signed["payload"])["intents"][0]
    with pytest.raises(SettlementError, match="exactly the keys"):
        verify(sign([{**transfer, "msg": "call receiver"}]))
    with pytest.raises(SettlementError, match="exactly one intent"):
        verify(sign([transfer, transfer]))
    with pytest.raises(SettlementError, match="exactly the keys"):
        verify(edited(signed, lambda payload: payload.__setitem__("extra", 1), HUNTER_KEY))
    with pytest.raises(SettlementError, match="exactly the keys"):
        verify({**signed, "public_key": "secp256k1:abc"})
    with pytest.raises(SettlementError, match="JSON string"):
        verify({**signed, "payload": json.loads(signed["payload"])})
    with pytest.raises(SettlementError, match="canonical base64"):
        verify(edited(signed, lambda p: p.__setitem__("nonce", "AAAA"), HUNTER_KEY))
    with pytest.raises(SettlementError, match="nep413 or erc191"):
        verify({**signed, "standard": "tip191"})
    with pytest.raises(SettlementError, match="JSON object"):
        verify([signed])
    doubled = signed["payload"][:-1] + f',"signer_id":"{HUNTER}"}}'
    with pytest.raises(SettlementError, match="duplicate"):
        verify(sign_payload(doubled))


# Settlements -----------------------------------------------------------------


class FakeRpc:
    """Answers simulate_intents and records execute_intents; never touches the network."""

    def __init__(self, executed=True):
        self.calls = []
        self.executed = executed

    def view(self, contract, method, args):
        if method == "current_salt":
            return SALT.hex()
        assert method == "simulate_intents"
        return {"intents_executed": entries(args["signed"]), "state": {}}

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        self.calls.append((signer_id, receiver_id, method, args))
        data = entries(args["signed"]) if self.executed else []
        event = {"standard": "dip4", "event": "intents_executed", "data": data}
        return "TxHash111", {
            "receipts_outcome": [{"outcome": {"logs": ["EVENT_JSON:" + json.dumps(event)]}}]
        }


def entries(signed_list):
    return [
        dict(zip(("account_id", "nonce"), ni.signer_and_nonce(signed), strict=True))
        for signed in signed_list
    ]


def arsune_settings():
    return Settings(_env_file=None, near_account_id=ARSUNE, near_private_key=generate_key()[0])


def test_dry_settlement_collects_erc191_bond_and_pays_0x_account():
    dry = DrySettlement(arsune_settings())
    assert dry.collect(hunter_bond(), sender=HUNTER, amount=AMOUNT, memo=MEMO).startswith("dry-")
    with pytest.raises(SettlementError, match="memo"):
        dry.collect(hunter_bond(), sender=HUNTER, amount=AMOUNT, memo="arsune:bond:claim-2")
    assert dry.pay(HUNTER, 150_000, "arsune:reward:claim-1").startswith("dry-")


def test_near_intents_settlement_collects_erc191_bond_and_pays_0x_account():
    rpc = FakeRpc()
    config = arsune_settings()
    settlement = ni.NearIntentsSettlement(config, rpc=rpc)
    bond = hunter_bond()
    assert settlement.collect(bond, sender=HUNTER, amount=AMOUNT, memo=MEMO) == "TxHash111"
    assert rpc.calls == [(ARSUNE, "intents.near", "execute_intents", {"signed": [bond]})]

    memo = "arsune:reward:claim-1"
    assert settlement.pay(HUNTER, 150_000, memo) == "TxHash111"
    (payout,) = rpc.calls[1][3]["signed"]
    assert payout["standard"] == "nep413"
    ni.verify_signed_transfer(
        payout, sender=ARSUNE, receiver=HUNTER, token_id=TOKEN, amount=150_000, memo=memo
    )


def test_execute_requires_the_erc191_nonce_in_the_executed_events():
    with pytest.raises(SettlementError, match="without executing"):
        ni.execute(
            [hunter_bond()], account_id=ARSUNE, private_key=generate_key()[0], rpc=FakeRpc(False)
        )


# Bounty intake ---------------------------------------------------------------


def test_bounty_intake_accepts_a_lowercase_0x_hunter():
    bounty_service.require_account(HUNTER, "hunter")
    with pytest.raises(HTTPException) as caught:
        bounty_service.require_account(checksum_case(HUNTER), "hunter")
    assert caught.value.status_code == 422
    assert f"Use the lowercase 0x address for the hunter: {HUNTER}." in caught.value.detail
    with pytest.raises(HTTPException) as caught:
        bounty_service.require_account("0x" + "G" * 40, "hunter")  # not hex
    assert caught.value.detail == "Use a valid NEAR account ID for the hunter."

    claim_id = str(uuid4())
    memo = f"arsune:bond:{claim_id}"
    bond = ni.sign_transfer_erc191(
        HUNTER_KEY,
        bounty_settlement.arsune_account(),
        settings.bounty_token,
        AMOUNT,
        memo,
        future(),
        SALT,
        contract=settings.intents_contract,
    )
    claim = CreateClaim(
        claim_id=claim_id,
        hunter_account=HUNTER,
        claim_type="broken_link",
        urls=["https://shop.example/a"],
        details={"source": "https://shop.example/a", "target": "https://shop.example/b"},
        explanation="The link is broken.",
        reproduction_steps=["Open the page."],
        bond_intent=bond,
    )
    bounty_settlement.check_transfer(
        claim.bond_intent, sender=claim.hunter_account, amount=AMOUNT, memo=memo
    )


# Mainnet, read-only ----------------------------------------------------------

LIVE_RPC = "https://free.rpc.fastnear.com"


@pytest.fixture(scope="module")
def rpc():
    client = NearRpc(LIVE_RPC, timeout=15)
    try:
        client.call("status", [])
    except SettlementError as error:
        pytest.skip(f"NEAR RPC unreachable: {error}")
    return client


def live_transfer(rpc):
    """1 unit of USDC from a throwaway Ethereum key to a fresh 0x account."""
    key, address = erc191.generate_evm_key()
    _, receiver = erc191.generate_evm_key()
    signed = ni.sign_transfer_erc191(
        key, receiver, TOKEN, 1, "arsune-live-check", future(), rpc=rpc, contract="intents.near"
    )
    return signed, address


def simulate_error(signed, rpc):
    with pytest.raises(SettlementError) as caught:
        ni.simulate([signed], rpc=rpc, contract="intents.near")
    return str(caught.value)


@pytest.mark.network
def test_live_erc191_transfer_fails_only_for_balance(rpc):
    signed, _ = live_transfer(rpc)
    assert "insufficient balance or overflow" in simulate_error(signed, rpc)


@pytest.mark.network
def test_live_erc191_signer_is_the_implicit_0x_account(rpc):
    key, address = erc191.generate_evm_key()
    signed = ni.sign_intents_erc191(key, [], future(), rpc=rpc, contract="intents.near")
    result = ni.simulate([signed], rpc=rpc, contract="intents.near")
    (executed,) = result["intents_executed"]
    assert (executed["account_id"], executed["nonce"]) == ni.signer_and_nonce(signed)
    assert executed["account_id"] == address
    intent_hash = base58.b58encode(erc191.erc191_hash(signed["payload"])).decode()
    assert executed["intent_hash"] == intent_hash


@pytest.mark.network
def test_live_tampered_signature_is_rejected(rpc):
    signed, address = live_transfer(rpc)
    raw = erc191.decode_signature(signed["signature"])
    unnormalized = raw[:64] + bytes([raw[64] + 27])
    assert "invalid signature" in simulate_error(with_signature(signed, unnormalized), rpc)
    s = int.from_bytes(raw[32:64], "big")
    high_s_twin = raw[:32] + (erc191.CURVE_ORDER - s).to_bytes(32, "big") + bytes([1 - raw[64]])
    assert "invalid signature" in simulate_error(with_signature(signed, high_s_twin), rpc)
    # A changed bit usually recovers some other key, which the 0x account does not hold.
    flipped = bytearray(raw)
    flipped[40] ^= 1
    try:
        erc191.recover_address(signed["payload"], bytes(flipped))
        expected = f"doesn't exist for account '{address}'"
    except SettlementError:
        expected = "invalid signature"
    assert expected in simulate_error(with_signature(signed, flipped), rpc)


@pytest.mark.network
def test_live_checksummed_address_is_not_an_account_id(rpc):
    key, address = erc191.generate_evm_key()
    signed = ni.sign_intents_erc191(key, [], future(), rpc=rpc, contract="intents.near")
    mixed = edited(signed, lambda p: p.__setitem__("signer_id", checksum_case(address)), key)
    assert "the Account ID contains an invalid character" in simulate_error(mixed, rpc)
