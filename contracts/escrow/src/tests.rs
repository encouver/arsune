use super::*;
use near_sdk::mock::MockAction;
use near_sdk::serde_json::{json, Value};
use near_sdk::test_utils::{get_created_receipts, get_logs, VMContextBuilder};
use near_sdk::{testing_env, PromiseResult};

const TOKEN: &str = "nep141:usdc.near";
const T0: u64 = 1_800_000_000 * SECOND;
const HOUR: u64 = 3600 * SECOND;
const DAY: u64 = 24 * HOUR;
const REWARD: u128 = 1_000_000;
const FEE: u128 = 100_000; // 10%
const BASE_BOND: u128 = 100_000;
const ONE_NEAR: u128 = 10u128.pow(24);
const HASH: &str = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

fn acc(name: &str) -> AccountId {
    name.parse().unwrap()
}

fn call(predecessor: &str, deposit: u128, now: u64) {
    testing_env!(VMContextBuilder::new()
        .current_account_id(acc("escrow.near"))
        .predecessor_account_id(acc(predecessor))
        .signer_account_id(acc(predecessor))
        .attached_deposit(NearToken::from_yoctonear(deposit))
        .block_timestamp(now)
        .build());
}

fn callback(result: PromiseResult) {
    testing_env!(
        VMContextBuilder::new()
            .current_account_id(acc("escrow.near"))
            .predecessor_account_id(acc("escrow.near"))
            .block_timestamp(T0)
            .build(),
        near_sdk::test_vm_config(),
        near_sdk::RuntimeFeesConfig::test(),
        Default::default(),
        vec![result],
    );
}

fn setup() -> Escrow {
    call("deployer.near", 0, T0);
    Escrow::new(acc("intents.near"), TOKEN.to_string())
}

struct Terms {
    caller: &'static str,
    bounty_id: &'static str,
    owner: &'static str,
    judge: &'static str,
    fee_account: &'static str,
    reward: u128,
    base_bond: u128,
    fee_bps: u16,
    scout_max: u16,
    deadline: u64,
    timeout: u64,
    deposit: u128,
}

fn terms() -> Terms {
    Terms {
        caller: "judge.near",
        bounty_id: "b1",
        owner: "owner.near",
        judge: "judge.near",
        fee_account: "fee.near",
        reward: REWARD,
        base_bond: BASE_BOND,
        fee_bps: 1000,
        scout_max: 5000,
        deadline: T0 + DAY,
        timeout: HOUR,
        deposit: ONE_NEAR,
    }
}

fn create_with(e: &mut Escrow, t: Terms) -> BountyView {
    call(t.caller, t.deposit, T0);
    e.create_bounty(
        t.bounty_id.to_string(),
        acc(t.owner),
        acc(t.judge),
        acc(t.fee_account),
        U128(t.reward),
        U128(t.base_bond),
        t.fee_bps,
        t.scout_max,
        U64(t.deadline),
        U64(t.timeout),
    )
}

fn create(e: &mut Escrow) -> BountyView {
    create_with(e, terms())
}

fn deposit_at(e: &mut Escrow, now: u64, sender: &str, amount: u128, msg: &str) -> Vec<U128> {
    call("intents.near", 0, now);
    let result = e.mt_on_transfer(
        acc(sender),
        vec![acc(sender)],
        vec![TOKEN.to_string()],
        vec![U128(amount)],
        msg.to_string(),
    );
    match result {
        PromiseOrValue::Value(refund) => refund,
        PromiseOrValue::Promise(_) => panic!("mt_on_transfer must return a value"),
    }
}

fn fund(e: &mut Escrow, amount: u128) -> Vec<U128> {
    deposit_at(e, T0, "owner.near", amount, r#"{"fund":"b1"}"#)
}

fn bond_msg(claim_id: &str, scout: Option<(&str, u16)>) -> String {
    let mut bond = json!({"bounty_id": "b1", "claim_id": claim_id});
    if let Some((account_id, share_bps)) = scout {
        bond["scout"] = json!({"account_id": account_id, "share_bps": share_bps});
    }
    json!({ "bond": bond }).to_string()
}

fn bond(e: &mut Escrow, claim_id: &str, amount: u128, scout: Option<(&str, u16)>) -> Vec<U128> {
    deposit_at(e, T0 + 1, "hunter.near", amount, &bond_msg(claim_id, scout))
}

fn accepted(refund: Vec<U128>) {
    assert_eq!(refund, vec![U128(0)], "deposit should be accepted");
}

fn refunded(refund: Vec<U128>, amount: u128, reason: &str) {
    assert_eq!(refund, vec![U128(amount)], "deposit should be refunded");
    let refunds = events("refund");
    assert_eq!(refunds.len(), 1);
    assert_eq!(refunds[0]["reason"], reason);
}

fn rule(e: &mut Escrow, claim_id: &str, outcome: Outcome) -> RuleView {
    call("judge.near", 0, T0 + 2);
    e.rule(claim_id.to_string(), outcome, HASH.to_string())
}

/// (receiver, amount, memo) of every mt_transfer the last call scheduled.
fn transfers() -> Vec<(String, u128, String)> {
    let mut found = Vec::new();
    for receipt in get_created_receipts() {
        for action in receipt.actions {
            if let MockAction::FunctionCallWeight {
                method_name,
                args,
                attached_deposit,
                ..
            } = action
            {
                if method_name != b"mt_transfer" {
                    continue;
                }
                assert_eq!(receipt.receiver_id, acc("intents.near"));
                assert_eq!(attached_deposit, ONE_YOCTO);
                let args: Value = near_sdk::serde_json::from_slice(&args).unwrap();
                assert_eq!(args["token_id"], TOKEN);
                assert_eq!(args["approval"], Value::Null);
                found.push((
                    args["receiver_id"].as_str().unwrap().to_string(),
                    args["amount"].as_str().unwrap().parse().unwrap(),
                    args["memo"].as_str().unwrap().to_string(),
                ));
            }
        }
    }
    found
}

fn events(name: &str) -> Vec<Value> {
    get_logs()
        .iter()
        .filter_map(|log| log.strip_prefix("EVENT_JSON:"))
        .map(|text| near_sdk::serde_json::from_str::<Value>(text).unwrap())
        .filter(|event| event["event"] == name)
        .map(|event| {
            assert_eq!(event["standard"], "arsune-escrow");
            assert_eq!(event["version"], "1.0.0");
            event["data"].clone()
        })
        .collect()
}

fn bounty(e: &Escrow) -> BountyView {
    e.get_bounty("b1".to_string()).unwrap()
}

/// A funded bounty (2 rewards with fees) and one open claim with the base bond.
fn funded_claim(scout: Option<(&str, u16)>) -> Escrow {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, 2 * (REWARD + FEE)));
    accepted(bond(&mut e, "c1", BASE_BOND, scout));
    e
}

fn assert_solvent(e: &Escrow) {
    // Every token the escrow holds belongs to a bounty balance, a held bond or an owed payout.
    let b = bounty(e);
    let owed: u128 = ["hunter.near", "scout.near", "fee.near", "owner.near"]
        .iter()
        .map(|a| e.get_owed(acc(a)).0)
        .sum();
    assert_eq!(e.liabilities, b.balance.0 + b.bonds_held.0 + owed);
}

// ---- create_bounty ----

#[test]
fn create_bounty_stores_immutable_terms_and_storage_pool() {
    let mut e = setup();
    let view = create(&mut e);
    assert_eq!(view.owner, acc("owner.near"));
    assert_eq!(view.judge, acc("judge.near"));
    assert_eq!(view.fee.0, FEE);
    assert_eq!(view.min_bond.0, BASE_BOND / 2);
    assert_eq!(view.max_bond.0, BASE_BOND * 3);
    assert_eq!(view.status, "open");
    assert_eq!(view.storage_payer, acc("judge.near"));
    assert!(view.storage_pool.0 < ONE_NEAR && view.storage_pool.0 > ONE_NEAR / 2);
    assert_eq!(
        view.claims_left.0 as u128,
        view.storage_pool.0 / claim_storage_cost()
    );
    let created = events("create");
    assert_eq!(created[0]["bounty_id"], "b1");
    assert_eq!(created[0]["fee_bps"], 1000);
}

#[test]
fn owner_can_create_bounty() {
    let mut e = setup();
    let view = create_with(
        &mut e,
        Terms {
            caller: "owner.near",
            ..terms()
        },
    );
    assert_eq!(view.storage_payer, acc("owner.near"));
}

#[test]
#[should_panic(expected = "only the owner or the judge can create a bounty")]
fn stranger_cannot_create_bounty() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            caller: "mallory.near",
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "fee_bps must be at most 2000")]
fn fee_above_cap_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            fee_bps: 2001,
            ..terms()
        },
    );
}

#[test]
fn fee_at_cap_is_accepted() {
    let mut e = setup();
    let view = create_with(
        &mut e,
        Terms {
            fee_bps: 2000,
            ..terms()
        },
    );
    assert_eq!(view.fee.0, REWARD / 5);
}

#[test]
#[should_panic(expected = "scout_share_max_bps must be at most 5000")]
fn scout_cap_above_half_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            scout_max: 5001,
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "reward must be positive")]
fn zero_reward_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            reward: 0,
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "reward must be positive")]
fn huge_reward_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            reward: MAX_AMOUNT + 1,
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "base_bond must be positive")]
fn zero_bond_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            base_bond: 0,
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "deadline must be in the future")]
fn past_deadline_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            deadline: T0,
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "rule_timeout_ns must be 60 s to 30 days")]
fn short_timeout_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            timeout: MIN_RULE_TIMEOUT_NS - 1,
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "rule_timeout_ns must be 60 s to 30 days")]
fn long_timeout_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            timeout: MAX_RULE_TIMEOUT_NS + 1,
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "bounty_id must be 1 to 64 characters")]
fn bad_bounty_id_is_refused() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            bounty_id: "B1",
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "bounty_id is already used")]
fn bounty_id_is_used_once() {
    let mut e = setup();
    create(&mut e);
    create(&mut e);
}

#[test]
#[should_panic(expected = "the escrow and the intents contract cannot be parties")]
fn escrow_cannot_be_fee_account() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            fee_account: "escrow.near",
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "the escrow and the intents contract cannot be parties")]
fn intents_cannot_be_owner() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            owner: "intents.near",
            ..terms()
        },
    );
}

#[test]
#[should_panic(expected = "the bounty record and one claim")]
fn storage_deposit_is_required() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            deposit: claim_storage_cost(),
            ..terms()
        },
    );
}

// ---- deposits ----

#[test]
fn fund_adds_to_balance() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, 500));
    let funded = events("fund");
    assert_eq!(funded[0]["sender_id"], "owner.near");
    assert_eq!(funded[0]["balance"], "500");
    accepted(deposit_at(
        &mut e,
        T0,
        "sponsor.near",
        700,
        r#"{"fund":"b1"}"#,
    ));
    let view = bounty(&e);
    assert_eq!(view.balance.0, 1200);
    assert_eq!(view.funded_total.0, 1200);
    assert_eq!(e.get_config().liabilities.0, 1200);
}

#[test]
#[should_panic(expected = "only the intents contract can notify deposits")]
fn only_intents_can_notify() {
    let mut e = setup();
    create(&mut e);
    call("mallory.near", 0, T0);
    let _ = e.mt_on_transfer(
        acc("mallory.near"),
        vec![acc("mallory.near")],
        vec![TOKEN.to_string()],
        vec![U128(1)],
        r#"{"fund":"b1"}"#.to_string(),
    );
}

#[test]
fn malformed_messages_are_refunded() {
    let mut e = setup();
    create(&mut e);
    for msg in [
        "",
        "b1",
        "{}",
        r#"{"fund":1}"#,
        r#"{"fund":"b1","bond":{"bounty_id":"b1","claim_id":"c1"}}"#,
        r#"{"bond":{"bounty_id":"b1"}}"#,
        r#"{"bond":{"bounty_id":"b1","claim_id":"c1","extra":1}}"#,
        r#"{"withdraw":"b1"}"#,
    ] {
        refunded(deposit_at(&mut e, T0, "owner.near", 10, msg), 10, "bad_msg");
    }
    assert_eq!(bounty(&e).balance.0, 0);
    assert_eq!(e.get_config().liabilities.0, 0);
}

#[test]
fn wrong_token_is_refunded() {
    let mut e = setup();
    create(&mut e);
    call("intents.near", 0, T0);
    let cases = [
        (vec!["nep141:wrap.near"], vec![10]),
        (vec![TOKEN, TOKEN], vec![10, 10]),
        (vec![TOKEN], vec![10, 10]),
    ];
    for (tokens, amounts) in cases {
        call("intents.near", 0, T0);
        let amounts: Vec<U128> = amounts.into_iter().map(U128).collect();
        let result = e.mt_on_transfer(
            acc("owner.near"),
            vec![acc("owner.near")],
            tokens.iter().map(|t| t.to_string()).collect(),
            amounts.clone(),
            r#"{"fund":"b1"}"#.to_string(),
        );
        match result {
            PromiseOrValue::Value(refund) => assert_eq!(refund, amounts),
            PromiseOrValue::Promise(_) => panic!(),
        }
        assert_eq!(events("refund")[0]["reason"], "wrong_token");
    }
    call("intents.near", 0, T0);
    let result = e.mt_on_transfer(
        acc("owner.near"),
        vec![acc("other.near")],
        vec![TOKEN.to_string()],
        vec![U128(10)],
        r#"{"fund":"b1"}"#.to_string(),
    );
    assert!(matches!(result, PromiseOrValue::Value(v) if v == vec![U128(10)]));
    refunded(
        deposit_at(&mut e, T0, "owner.near", 0, r#"{"fund":"b1"}"#),
        0,
        "zero_amount",
    );
    assert_eq!(bounty(&e).balance.0, 0);
}

#[test]
fn fund_to_unknown_closed_or_expired_bounty_is_refunded() {
    let mut e = setup();
    create(&mut e);
    refunded(
        deposit_at(&mut e, T0, "owner.near", 5, r#"{"fund":"nope"}"#),
        5,
        "unknown_bounty",
    );
    refunded(
        deposit_at(&mut e, T0 + DAY, "owner.near", 5, r#"{"fund":"b1"}"#),
        5,
        "deadline_passed",
    );
    call("owner.near", 1, T0);
    e.close_bounty("b1".to_string());
    refunded(fund(&mut e, 5), 5, "bounty_closed");
    assert_eq!(bounty(&e).balance.0, 0);
}

#[test]
fn bond_opens_claim() {
    let e = funded_claim(Some(("scout.near", 2500)));
    let claim = e.get_claim("c1".to_string()).unwrap();
    assert_eq!(claim.hunter, acc("hunter.near"));
    assert_eq!(claim.bond.0, BASE_BOND);
    assert_eq!(claim.scout.as_ref().unwrap().share_bps, 2500);
    assert_eq!(claim.outcome, None);
    assert_eq!(claim.reclaim_after_ns.0, T0 + 1 + HOUR);
    let view = bounty(&e);
    assert_eq!(view.open_claims, 1);
    assert_eq!(view.claim_count, 1);
    assert_eq!(view.bonds_held.0, BASE_BOND);
    assert_eq!(view.balance.0, 2 * (REWARD + FEE));
    let bonded = events("bond");
    assert_eq!(bonded[0]["claim_id"], "c1");
    assert_eq!(bonded[0]["scout"]["account_id"], "scout.near");
    assert_solvent(&e);
}

#[test]
fn bond_range_follows_reputation_multipliers() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE));
    refunded(
        bond(&mut e, "c1", BASE_BOND / 2 - 1, None),
        BASE_BOND / 2 - 1,
        "bond_out_of_range",
    );
    refunded(
        bond(&mut e, "c1", 3 * BASE_BOND + 1, None),
        3 * BASE_BOND + 1,
        "bond_out_of_range",
    );
    accepted(bond(&mut e, "c1", BASE_BOND / 2, None));
    accepted(bond(&mut e, "c2", 3 * BASE_BOND, None));
    accepted(bond(&mut e, "c3", 2 * BASE_BOND, None));
    assert_eq!(bounty(&e).bonds_held.0, BASE_BOND / 2 + 5 * BASE_BOND);
}

#[test]
fn odd_base_bond_rounds_minimum_up() {
    let mut e = setup();
    let view = create_with(
        &mut e,
        Terms {
            base_bond: 3,
            ..terms()
        },
    );
    assert_eq!(view.min_bond.0, 2);
    assert_eq!(view.max_bond.0, 9);
}

#[test]
fn one_bond_per_claim_id() {
    let mut e = funded_claim(None);
    refunded(
        bond(&mut e, "c1", BASE_BOND, None),
        BASE_BOND,
        "claim_exists",
    );
    assert_eq!(bounty(&e).claim_count, 1);
}

#[test]
fn bond_needs_budget_for_one_reward_and_fee() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE - 1));
    refunded(
        bond(&mut e, "c1", BASE_BOND, None),
        BASE_BOND,
        "budget_too_low",
    );
    accepted(fund(&mut e, 1));
    accepted(bond(&mut e, "c1", BASE_BOND, None));
}

#[test]
fn bad_bonds_are_refunded() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE));
    let cases = [
        (bond_msg("C1", None), "hunter.near", "bad_claim_id"),
        (
            bond_msg(&"x".repeat(65), None),
            "hunter.near",
            "bad_claim_id",
        ),
        (
            bond_msg("c1", Some(("scout.near", 0))),
            "hunter.near",
            "bad_scout",
        ),
        (
            bond_msg("c1", Some(("scout.near", 5001))),
            "hunter.near",
            "bad_scout",
        ),
        (
            bond_msg("c1", Some(("judge.near", 100))),
            "hunter.near",
            "bad_scout",
        ),
        (
            bond_msg("c1", Some(("escrow.near", 100))),
            "hunter.near",
            "bad_scout",
        ),
        (bond_msg("c1", None), "judge.near", "bad_hunter"),
        (
            json!({"bond": {"bounty_id": "b9", "claim_id": "c1"}}).to_string(),
            "hunter.near",
            "unknown_bounty",
        ),
    ];
    for (msg, hunter, reason) in cases {
        refunded(
            deposit_at(&mut e, T0, hunter, BASE_BOND, &msg),
            BASE_BOND,
            reason,
        );
    }
    refunded(
        deposit_at(
            &mut e,
            T0 + DAY,
            "hunter.near",
            BASE_BOND,
            &bond_msg("c1", None),
        ),
        BASE_BOND,
        "deadline_passed",
    );
    assert_eq!(bounty(&e).claim_count, 0);
    assert_eq!(e.get_config().liabilities.0, REWARD + FEE);
}

#[test]
fn scout_share_is_capped_by_terms() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            scout_max: 1000,
            ..terms()
        },
    );
    accepted(fund(&mut e, REWARD + FEE));
    refunded(
        bond(&mut e, "c1", BASE_BOND, Some(("scout.near", 1001))),
        BASE_BOND,
        "bad_scout",
    );
    accepted(bond(&mut e, "c1", BASE_BOND, Some(("scout.near", 1000))));
}

#[test]
fn bonds_stop_when_storage_pool_is_spent() {
    let mut e = setup();
    // Room for the bounty record and exactly two claims.
    let first = create_with(&mut e, terms());
    let record = ONE_NEAR - first.storage_pool.0;
    let mut t = terms();
    t.bounty_id = "b2";
    t.deposit = record + 2 * claim_storage_cost();
    create_with(&mut e, t);
    let msg = |claim: &str| json!({"bond": {"bounty_id": "b2", "claim_id": claim}}).to_string();
    accepted(deposit_at(
        &mut e,
        T0,
        "owner.near",
        2 * (REWARD + FEE),
        r#"{"fund":"b2"}"#,
    ));
    accepted(deposit_at(&mut e, T0, "hunter.near", BASE_BOND, &msg("c1")));
    accepted(deposit_at(&mut e, T0, "hunter.near", BASE_BOND, &msg("c2")));
    refunded(
        deposit_at(&mut e, T0, "hunter.near", BASE_BOND, &msg("c3")),
        BASE_BOND,
        "no_storage",
    );
}

#[test]
fn claim_storage_bound_holds_for_longest_names() {
    let mut e = setup();
    let long_bounty = "b".repeat(64);
    let long_claim = "c".repeat(64);
    let long_account = format!("{}.near", "h".repeat(59));
    let long_scout = format!("{}.near", "s".repeat(59));
    call("judge.near", ONE_NEAR, T0);
    e.create_bounty(
        long_bounty.clone(),
        acc("owner.near"),
        acc("judge.near"),
        acc("fee.near"),
        U128(REWARD),
        U128(BASE_BOND),
        1000,
        5000,
        U64(T0 + DAY),
        U64(HOUR),
    );
    let fund_msg = json!({ "fund": long_bounty }).to_string();
    accepted(deposit_at(
        &mut e,
        T0,
        "owner.near",
        REWARD + FEE,
        &fund_msg,
    ));
    e.bounties.flush();
    let before = env::storage_usage();
    let msg = json!({"bond": {"bounty_id": long_bounty, "claim_id": long_claim,
        "scout": {"account_id": long_scout, "share_bps": 5000}}})
    .to_string();
    accepted(deposit_at(&mut e, T0, &long_account, BASE_BOND, &msg));
    call("judge.near", 0, T0 + 2);
    e.rule(long_claim, Outcome::Proven, HASH.to_string());
    e.claims.flush();
    e.claim_slots.flush();
    e.bounties.flush();
    let used = u128::from(env::storage_usage() - before);
    println!("claim storage with the longest names: {used} bytes");
    assert!(used <= CLAIM_STORAGE_BYTES, "a claim used {used} bytes");
}

// ---- rulings ----

#[test]
fn proven_pays_hunter_scout_and_fee() {
    let mut e = funded_claim(Some(("scout.near", 2500)));
    let view = rule(&mut e, "c1", Outcome::Proven);
    let scout = REWARD / 4;
    let hunter = REWARD - scout + BASE_BOND;
    assert_eq!(view.outcome, Some(Outcome::Proven));
    assert_eq!(view.hunter_amount.0, hunter);
    assert_eq!(view.scout_amount.0, scout);
    assert_eq!(view.fee_amount.0, FEE);
    assert_eq!(
        transfers(),
        vec![
            ("hunter.near".into(), hunter, "arsune:reward:c1".into()),
            ("scout.near".into(), scout, "arsune:scout:c1".into()),
            ("fee.near".into(), FEE, "arsune:fee:c1".into()),
        ]
    );
    let b = bounty(&e);
    assert_eq!(b.balance.0, REWARD + FEE);
    assert_eq!(b.rewards_total.0, REWARD);
    assert_eq!(b.fees_total.0, FEE);
    assert_eq!(b.open_claims, 0);
    assert_eq!(b.bonds_held.0, 0);
    assert_eq!(b.status, "open");
    let ruled = events("rule");
    assert_eq!(ruled[0]["outcome"], "proven");
    assert_eq!(ruled[0]["ruling"], "proven");
    assert_eq!(ruled[0]["evidence_hash"], HASH);
    assert_eq!(ruled[0]["judge"], "judge.near");
    let claim = e.get_claim("c1".to_string()).unwrap();
    assert_eq!(claim.outcome, Some(Outcome::Proven));
    assert_eq!(claim.evidence_hash.as_deref(), Some(HASH));
    assert_eq!(claim.decided_ns, Some(U64(T0 + 2)));
    assert_solvent(&e);
}

#[test]
fn proven_without_scout_or_fee_sends_one_transfer() {
    let mut e = setup();
    create_with(
        &mut e,
        Terms {
            fee_bps: 0,
            ..terms()
        },
    );
    accepted(fund(&mut e, REWARD));
    accepted(bond(&mut e, "c1", BASE_BOND, None));
    rule(&mut e, "c1", Outcome::Proven);
    assert_eq!(
        transfers(),
        vec![(
            "hunter.near".into(),
            REWARD + BASE_BOND,
            "arsune:reward:c1".into()
        )]
    );
}

#[test]
fn scout_share_rounds_down() {
    let mut e = funded_claim(Some(("scout.near", 3333)));
    let view = rule(&mut e, "c1", Outcome::Proven);
    assert_eq!(view.scout_amount.0, 333_300);
    assert_eq!(view.hunter_amount.0, REWARD - 333_300 + BASE_BOND);
}

#[test]
fn last_paid_claim_closes_spent_bounty() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE + 5));
    accepted(bond(&mut e, "c1", BASE_BOND, None));
    rule(&mut e, "c1", Outcome::Proven);
    let b = bounty(&e);
    assert_eq!(b.balance.0, 5);
    assert_eq!(b.status, "closed");
    assert_eq!(b.close_reason, Some(CloseReason::BudgetSpent));
    assert_eq!(events("close")[0]["reason"], "budget_spent");
}

#[test]
fn proven_without_budget_becomes_unfunded_and_refunds_bond() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE));
    accepted(bond(&mut e, "c1", BASE_BOND, None));
    accepted(bond(&mut e, "c2", BASE_BOND, None));
    rule(&mut e, "c1", Outcome::Proven);
    let view = rule(&mut e, "c2", Outcome::Proven);
    assert_eq!(view.outcome, Some(Outcome::Unfunded));
    assert_eq!(
        transfers(),
        vec![("hunter.near".into(), BASE_BOND, "arsune:refund:c2".into())]
    );
    let ruled = events("rule");
    assert_eq!(ruled[0]["ruling"], "proven");
    assert_eq!(ruled[0]["outcome"], "unfunded");
    let b = bounty(&e);
    assert_eq!(b.balance.0, 0);
    assert_eq!(b.rewards_total.0, REWARD);
    assert_solvent(&e);
}

#[test]
fn forfeited_bond_stays_after_last_paid_claim() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE));
    accepted(bond(&mut e, "c1", BASE_BOND, None));
    accepted(bond(&mut e, "c2", BASE_BOND, None));
    rule(&mut e, "c1", Outcome::Rejected);
    rule(&mut e, "c2", Outcome::Proven);
    let b = bounty(&e);
    assert_eq!(b.balance.0, BASE_BOND);
    assert_eq!(b.close_reason, Some(CloseReason::BudgetSpent));
}

#[test]
fn explicit_unfunded_closes_bounty_with_reason() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE));
    accepted(bond(&mut e, "c1", BASE_BOND, None));
    accepted(bond(&mut e, "c2", BASE_BOND, None));
    rule(&mut e, "c1", Outcome::Proven);
    // The bounty closed after c1 (budget spent); an explicit unfunded keeps that reason.
    let view = rule(&mut e, "c2", Outcome::Unfunded);
    assert_eq!(view.outcome, Some(Outcome::Unfunded));
    assert_eq!(bounty(&e).close_reason, Some(CloseReason::BudgetSpent));
}

#[test]
fn unfunded_closes_open_bounty() {
    // The Unfunded close reason needs a short balance while the bounty is still open.
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE));
    accepted(bond(&mut e, "c1", BASE_BOND, None));
    let mut b = e.bounties.get("b1").unwrap().clone();
    b.balance = REWARD; // as if part of the budget were already spent
    e.bounties.insert("b1".to_string(), b);
    let view = rule(&mut e, "c1", Outcome::Proven);
    assert_eq!(view.outcome, Some(Outcome::Unfunded));
    assert_eq!(bounty(&e).close_reason, Some(CloseReason::Unfunded));
    assert_eq!(events("close")[0]["reason"], "unfunded");
}

#[test]
#[should_panic(expected = "the bounty can pay this claim")]
fn judge_cannot_call_funded_claim_unfunded() {
    let mut e = funded_claim(None);
    rule(&mut e, "c1", Outcome::Unfunded);
}

#[test]
fn rejected_bond_goes_to_bounty() {
    let mut e = funded_claim(None);
    let view = rule(&mut e, "c1", Outcome::Rejected);
    assert_eq!(view.forfeited.0, BASE_BOND);
    assert!(transfers().is_empty());
    let b = bounty(&e);
    assert_eq!(b.balance.0, 2 * (REWARD + FEE) + BASE_BOND);
    assert_eq!(b.forfeited_total.0, BASE_BOND);
    assert_eq!(b.bonds_held.0, 0);
    assert_solvent(&e);
}

#[test]
fn duplicate_and_unverified_refund_bond() {
    let mut e = funded_claim(None);
    accepted(bond(&mut e, "c2", 2 * BASE_BOND, None));
    rule(&mut e, "c1", Outcome::Duplicate);
    assert_eq!(
        transfers(),
        vec![("hunter.near".into(), BASE_BOND, "arsune:refund:c1".into())]
    );
    rule(&mut e, "c2", Outcome::Unverified);
    assert_eq!(
        transfers(),
        vec![(
            "hunter.near".into(),
            2 * BASE_BOND,
            "arsune:refund:c2".into()
        )]
    );
    let b = bounty(&e);
    assert_eq!(b.balance.0, 2 * (REWARD + FEE));
    assert_eq!(b.status, "open");
    assert_solvent(&e);
}

#[test]
#[should_panic(expected = "the claim is already decided")]
fn claim_is_ruled_once() {
    let mut e = funded_claim(None);
    rule(&mut e, "c1", Outcome::Rejected);
    rule(&mut e, "c1", Outcome::Proven);
}

#[test]
#[should_panic(expected = "only the judge can rule")]
fn owner_cannot_rule() {
    let mut e = funded_claim(None);
    call("owner.near", 0, T0 + 2);
    e.rule("c1".to_string(), Outcome::Rejected, HASH.to_string());
}

#[test]
#[should_panic(expected = "only the judge can rule")]
fn hunter_cannot_rule() {
    let mut e = funded_claim(None);
    call("hunter.near", 0, T0 + 2);
    e.rule("c1".to_string(), Outcome::Proven, HASH.to_string());
}

#[test]
#[should_panic(expected = "expired is not a ruling")]
fn judge_cannot_rule_expired() {
    let mut e = funded_claim(None);
    rule(&mut e, "c1", Outcome::Expired);
}

#[test]
#[should_panic(expected = "evidence_hash must be 64 lowercase hex characters")]
fn evidence_hash_must_be_sha256_hex() {
    let mut e = funded_claim(None);
    call("judge.near", 0, T0 + 2);
    e.rule("c1".to_string(), Outcome::Proven, HASH.to_uppercase());
}

#[test]
#[should_panic(expected = "unknown claim")]
fn unknown_claim_cannot_be_ruled() {
    let mut e = funded_claim(None);
    rule(&mut e, "c9", Outcome::Proven);
}

// ---- liveness ----

#[test]
#[should_panic(expected = "the judge can still rule this claim")]
fn bond_cannot_be_reclaimed_before_timeout() {
    let mut e = funded_claim(None);
    call("hunter.near", 0, T0 + HOUR);
    e.reclaim_bond("c1".to_string());
}

#[test]
fn bond_is_reclaimed_after_timeout() {
    let mut e = funded_claim(None);
    call("anyone.near", 0, T0 + 1 + HOUR);
    assert_eq!(e.reclaim_bond("c1".to_string()).0, BASE_BOND);
    assert_eq!(
        transfers(),
        vec![("hunter.near".into(), BASE_BOND, "arsune:expire:c1".into())]
    );
    assert_eq!(events("expire")[0]["hunter"], "hunter.near");
    let claim = e.get_claim("c1".to_string()).unwrap();
    assert_eq!(claim.outcome, Some(Outcome::Expired));
    assert_eq!(claim.evidence_hash, None);
    let b = bounty(&e);
    assert_eq!(b.open_claims, 0);
    assert_eq!(b.bonds_held.0, 0);
    assert_solvent(&e);
}

#[test]
#[should_panic(expected = "the claim is already decided")]
fn reclaimed_claim_cannot_be_ruled() {
    let mut e = funded_claim(None);
    call("hunter.near", 0, T0 + 1 + HOUR);
    e.reclaim_bond("c1".to_string());
    rule(&mut e, "c1", Outcome::Rejected);
}

#[test]
#[should_panic(expected = "the claim is already decided")]
fn ruled_claim_cannot_be_reclaimed() {
    let mut e = funded_claim(None);
    rule(&mut e, "c1", Outcome::Rejected);
    call("hunter.near", 0, T0 + 1 + HOUR);
    e.reclaim_bond("c1".to_string());
}

// ---- close and withdraw ----

#[test]
#[should_panic(expected = "only the owner can close the bounty")]
fn judge_cannot_close() {
    let mut e = funded_claim(None);
    call("judge.near", 1, T0);
    e.close_bounty("b1".to_string());
}

#[test]
#[should_panic(expected = "Requires attached deposit of exactly 1 yoctoNEAR")]
fn close_needs_one_yocto() {
    let mut e = funded_claim(None);
    call("owner.near", 0, T0);
    e.close_bounty("b1".to_string());
}

#[test]
#[should_panic(expected = "the bounty is open until the owner closes it or the deadline passes")]
fn nobody_withdraws_before_deadline() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, 10));
    call("owner.near", 0, T0 + DAY - 1);
    e.withdraw_remaining("b1".to_string());
}

#[test]
#[should_panic(expected = "claims are still open")]
fn open_claims_block_withdraw() {
    let mut e = funded_claim(None);
    call("owner.near", 0, T0 + DAY);
    e.withdraw_remaining("b1".to_string());
}

#[test]
fn withdraw_after_deadline_pays_owner_and_storage_payer() {
    let mut e = funded_claim(None);
    rule(&mut e, "c1", Outcome::Rejected);
    let pool = bounty(&e).storage_pool.0;
    // The judge triggers it, but the money can only go to the owner.
    call("judge.near", 0, T0 + DAY);
    let amount = e.withdraw_remaining("b1".to_string()).0;
    assert_eq!(amount, 2 * (REWARD + FEE) + BASE_BOND);
    assert_eq!(
        transfers(),
        vec![("owner.near".into(), amount, "arsune:withdraw:b1".into())]
    );
    let refunds: Vec<_> = get_created_receipts()
        .into_iter()
        .filter(|r| r.receiver_id == acc("judge.near"))
        .flat_map(|r| r.actions)
        .collect();
    assert!(matches!(
        refunds.as_slice(),
        [MockAction::Transfer { deposit, .. }] if deposit.as_yoctonear() == pool
    ));
    let w = events("withdraw");
    assert_eq!(w[0]["owner"], "owner.near");
    assert_eq!(w[0]["storage_refund"], pool.to_string());
    assert_eq!(events("close")[0]["reason"], "deadline");
    let b = bounty(&e);
    assert_eq!(b.balance.0, 0);
    assert_eq!(b.withdrawn_total.0, amount);
    assert_eq!(b.close_reason, Some(CloseReason::Deadline));
    assert_eq!(e.get_config().liabilities.0, 0);
}

#[test]
#[should_panic(expected = "nothing to withdraw")]
fn withdraw_happens_once() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, 10));
    call("owner.near", 0, T0 + DAY);
    e.withdraw_remaining("b1".to_string());
    e.withdraw_remaining("b1".to_string());
}

#[test]
fn owner_close_allows_early_withdraw_after_claims_end() {
    let mut e = funded_claim(None);
    call("owner.near", 1, T0 + 5);
    e.close_bounty("b1".to_string());
    assert_eq!(events("close")[0]["reason"], "owner");
    refunded(
        bond(&mut e, "c2", BASE_BOND, None),
        BASE_BOND,
        "bounty_closed",
    );
    // The open claim is still ruled and paid after the close.
    rule(&mut e, "c1", Outcome::Proven);
    assert_eq!(transfers().len(), 2);
    call("owner.near", 0, T0 + 10);
    assert_eq!(e.withdraw_remaining("b1".to_string()).0, REWARD + FEE);
    assert_eq!(bounty(&e).close_reason, Some(CloseReason::Owner));
}

#[test]
fn stale_claims_can_be_cleared_so_owner_can_withdraw() {
    let mut e = funded_claim(None);
    call("owner.near", 0, T0 + DAY + 1);
    e.reclaim_bond("c1".to_string());
    assert_eq!(e.withdraw_remaining("b1".to_string()).0, 2 * (REWARD + FEE));
}

// ---- payout failure ----

#[test]
fn failed_payout_becomes_owed_and_can_be_retried() {
    let mut e = funded_claim(None);
    rule(&mut e, "c1", Outcome::Proven);
    let liabilities = e.get_config().liabilities.0;
    callback(PromiseResult::Failed);
    let memo = "arsune:reward:c1".to_string();
    assert!(!e.on_payout(acc("hunter.near"), U128(REWARD + BASE_BOND), memo.clone()));
    assert_eq!(e.get_owed(acc("hunter.near")).0, REWARD + BASE_BOND);
    assert_eq!(
        e.get_config().liabilities.0,
        liabilities + REWARD + BASE_BOND
    );
    let failed = events("payout_failed");
    assert_eq!(failed[0]["memo"], memo);
    assert_eq!(failed[0]["owed"], (REWARD + BASE_BOND).to_string());
    callback(PromiseResult::Successful(vec![]));
    assert!(e.on_payout(acc("fee.near"), U128(FEE), "arsune:fee:c1".to_string()));
    assert_eq!(events("payout")[0]["receiver_id"], "fee.near");
    assert_solvent(&e);

    call("anyone.near", 0, T0 + 3);
    assert_eq!(e.retry_payout(acc("hunter.near")).0, REWARD + BASE_BOND);
    assert_eq!(
        transfers(),
        vec![(
            "hunter.near".into(),
            REWARD + BASE_BOND,
            "arsune:retry:hunter.near".into()
        )]
    );
    assert_eq!(e.get_owed(acc("hunter.near")).0, 0);
    assert_solvent(&e);
}

#[test]
fn second_failure_adds_to_owed() {
    let mut e = funded_claim(None);
    rule(&mut e, "c1", Outcome::Duplicate);
    callback(PromiseResult::Failed);
    e.on_payout(acc("hunter.near"), U128(5), "a".to_string());
    callback(PromiseResult::Failed);
    e.on_payout(acc("hunter.near"), U128(7), "b".to_string());
    assert_eq!(e.get_owed(acc("hunter.near")).0, 12);
}

#[test]
#[should_panic(expected = "nothing is owed to this account")]
fn retry_needs_owed_amount() {
    let mut e = setup();
    call("anyone.near", 0, T0);
    e.retry_payout(acc("hunter.near"));
}

// ---- views ----

#[test]
fn list_claims_pages_in_bond_order() {
    let mut e = setup();
    create(&mut e);
    accepted(fund(&mut e, REWARD + FEE));
    for i in 0..5 {
        accepted(bond(&mut e, &format!("c{i}"), BASE_BOND, None));
    }
    let ids = |page: Vec<ClaimView>| page.into_iter().map(|c| c.claim_id).collect::<Vec<_>>();
    assert_eq!(ids(e.list_claims("b1".into(), None, Some(2))), ["c0", "c1"]);
    assert_eq!(
        ids(e.list_claims("b1".into(), Some(2), Some(2))),
        ["c2", "c3"]
    );
    assert_eq!(ids(e.list_claims("b1".into(), Some(4), None)), ["c4"]);
    assert!(e.list_claims("b1".into(), Some(9), None).is_empty());
    assert_eq!(e.list_claims("b1".into(), None, Some(1000)).len(), 5);
    assert!(e.get_claim("c9".into()).is_none());
    assert!(e.get_bounty("b9".into()).is_none());
}

#[test]
fn config_view_states_the_limits() {
    let e = setup();
    let config = e.get_config();
    assert_eq!(config.intents_contract, acc("intents.near"));
    assert_eq!(config.token_id, TOKEN);
    assert_eq!(config.max_fee_bps, 2000);
    assert_eq!(config.max_scout_share_bps, 5000);
    assert_eq!(config.max_bond_multiplier.0, 3);
    assert_eq!(
        config.claim_storage_cost.0,
        CLAIM_STORAGE_BYTES * env::storage_byte_cost().as_yoctonear()
    );
}

#[test]
fn hash_round_trips() {
    assert_eq!(hex(&parse_hash(HASH)), HASH);
}
