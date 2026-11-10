//! The escrow against the real NEAR Intents Verifier (defuse 0.4.5, the exact wasm that
//! intents.near runs on mainnet) in a local sandbox. USDC is a NEP-141 test token deposited
//! into the Verifier, so the escrow's money is a NEP-245 balance exactly as on mainnet.
//!
//! Wasm paths come from ESCROW_WASM, DEFUSE_WASM and FT_WASM (set by ../test.sh).

use std::collections::BTreeMap;

use anyhow::{ensure, Result};
use base64::Engine as _;
use ed25519_dalek::{Signer, SigningKey};
use near_workspaces::network::Sandbox;
use near_workspaces::result::ExecutionFinalResult;
use near_workspaces::types::{Gas, NearToken};
use near_workspaces::{Account, AccountId, Contract, Worker};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

const ONE_YOCTO: NearToken = NearToken::from_yoctonear(1);
const SECOND: u64 = 1_000_000_000;
const REWARD: u128 = 1_000_000;
const FEE: u128 = 100_000;
const BOND: u128 = 100_000;
const HASH: &str = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
const RULE_GAS: Gas = Gas::from_tgas(200);
// 2099-01-01T00:00:00Z: far enough for any test run, and a valid versioned-nonce deadline.
const DEADLINE: &str = "2099-01-01T00:00:00Z";
const DEADLINE_SECONDS: i64 = 4_070_908_800;

fn wasm(var: &str) -> Vec<u8> {
    let path = std::env::var(var).unwrap_or_else(|_| panic!("set {var} (see ../test.sh)"));
    std::fs::read(&path).unwrap_or_else(|e| panic!("cannot read {path}: {e}"))
}

struct Env {
    worker: Worker<Sandbox>,
    root: Account,
    ft: Contract,
    intents: Contract,
    escrow: AccountId,
    owner: Account,
    judge: Account,
    fee: Account,
    hunter: Account,
    scout: Account,
    mallory: Account,
    token: String,
}

struct Terms {
    reward: u128,
    fee_bps: u16,
    deadline_in: u64,
    timeout: u64,
}

impl Default for Terms {
    fn default() -> Self {
        Terms {
            reward: REWARD,
            fee_bps: 1000,
            deadline_in: 24 * 3600 * SECOND,
            timeout: 3600 * SECOND,
        }
    }
}

async fn ok(result: ExecutionFinalResult) -> Result<ExecutionFinalResult> {
    ensure!(
        result.is_success(),
        "transaction failed: {:?}",
        result.failures()
    );
    Ok(result)
}

async fn done(result: ExecutionFinalResult) -> Result<()> {
    ok(result).await.map(drop)
}

fn failure_text(result: &ExecutionFinalResult) -> String {
    format!("{:?}", result.failures())
}

fn events(result: &ExecutionFinalResult, name: &str) -> Vec<Value> {
    result
        .logs()
        .iter()
        .filter_map(|log| log.strip_prefix("EVENT_JSON:"))
        .map(|text| serde_json::from_str::<Value>(text).unwrap())
        .filter(|event| event["standard"] == "arsune-escrow" && event["event"] == name)
        .map(|event| event["data"].clone())
        .collect()
}

impl Env {
    async fn new() -> Result<Env> {
        let worker = near_workspaces::sandbox().await?;
        let root = worker.root_account()?;
        let ft = root
            .create_subaccount("usdc")
            .initial_balance(NearToken::from_near(10))
            .transact()
            .await?
            .into_result()?
            .deploy(&wasm("FT_WASM"))
            .await?
            .into_result()?;
        done(
            ft.call("new_default_meta")
                .args_json(json!({"owner_id": ft.id(), "total_supply": "1000000000000000"}))
                .transact()
                .await?,
        )
        .await?;
        let intents = root
            .create_subaccount("intents")
            .initial_balance(NearToken::from_near(50))
            .transact()
            .await?
            .into_result()?
            .deploy(&wasm("DEFUSE_WASM"))
            .await?
            .into_result()?;
        let roles =
            json!({"super_admins": [root.id()], "admins": {}, "grantees": {"DAO": [root.id()]}});
        done(
            intents
                .call("new")
                .args_json(json!({"config": {
                    "wnear_id": "wrap.test.near",
                    "fees": {"fee": 0, "fee_collector": root.id()},
                    "roles": roles,
                }}))
                .max_gas()
                .transact()
                .await?,
        )
        .await?;
        done(
            root.call(ft.id(), "storage_deposit")
                .args_json(json!({"account_id": intents.id()}))
                .deposit(NearToken::from_millinear(10))
                .transact()
                .await?,
        )
        .await?;
        let mut accounts = Vec::new();
        for name in ["owner", "judge", "fee", "hunter", "scout", "mallory"] {
            let account = root
                .create_subaccount(name)
                .initial_balance(NearToken::from_near(20))
                .transact()
                .await?
                .into_result()?;
            accounts.push(account);
        }
        let [owner, judge, fee, hunter, scout, mallory] =
            <[Account; 6]>::try_from(accounts).map_err(|_| anyhow::anyhow!("six accounts"))?;
        let escrow = root
            .create_subaccount("escrow")
            .initial_balance(NearToken::from_near(5))
            .transact()
            .await?
            .into_result()?
            .deploy(&wasm("ESCROW_WASM"))
            .await?
            .into_result()?;
        let token = format!("nep141:{}", ft.id());
        done(
            escrow
                .call("new")
                .args_json(json!({"intents_contract": intents.id(), "token_id": token}))
                .transact()
                .await?,
        )
        .await?;
        // Lock the escrow as on mainnet: no access key, so only its code moves its money.
        let key = escrow.as_account().secret_key().public_key();
        done(
            escrow
                .as_account()
                .batch(escrow.id())
                .delete_key(key)
                .transact()
                .await?,
        )
        .await?;
        ensure!(escrow.view_access_keys().await?.is_empty());
        let env = Env {
            escrow: escrow.id().clone(),
            worker,
            root,
            ft,
            intents,
            owner,
            judge,
            fee,
            hunter,
            scout,
            mallory,
            token,
        };
        for (account, amount) in [
            (env.owner.id(), 100_000_000u128),
            (env.hunter.id(), 10_000_000),
            (env.mallory.id(), 10_000_000),
        ] {
            env.mint(account, amount).await?;
        }
        Ok(env)
    }

    /// Deposit test USDC into the Verifier for `account` (ft_transfer_call with msg = account).
    async fn mint(&self, account: &AccountId, amount: u128) -> Result<()> {
        done(
            self.ft
                .call("ft_transfer_call")
                .args_json(json!({
                    "receiver_id": self.intents.id(),
                    "amount": amount.to_string(),
                    "msg": account,
                }))
                .deposit(ONE_YOCTO)
                .gas(Gas::from_tgas(100))
                .transact()
                .await?,
        )
        .await?;
        Ok(())
    }

    async fn now(&self) -> Result<u64> {
        Ok(self.worker.view_block().await?.timestamp())
    }

    /// Produce blocks until the chain clock has moved by at least `ns`.
    async fn advance(&self, ns: u64) -> Result<()> {
        let target = self.now().await? + ns;
        while self.now().await? < target {
            self.worker.fast_forward(20).await?;
        }
        Ok(())
    }

    async fn balance(&self, account: &AccountId) -> Result<u128> {
        let value: String = self
            .intents
            .view("mt_balance_of")
            .args_json(json!({"account_id": account, "token_id": self.token}))
            .await?
            .json()?;
        Ok(value.parse()?)
    }

    async fn view(&self, method: &str, args: Value) -> Result<Value> {
        Ok(self
            .worker
            .view(&self.escrow, method)
            .args_json(args)
            .await?
            .json()?)
    }

    async fn bounty(&self, id: &str) -> Result<Value> {
        self.view("get_bounty", json!({"bounty_id": id})).await
    }

    async fn claim(&self, id: &str) -> Result<Value> {
        self.view("get_claim", json!({"claim_id": id})).await
    }

    async fn liabilities(&self) -> Result<u128> {
        let config = self.view("get_config", json!({})).await?;
        Ok(config["liabilities"].as_str().unwrap().parse()?)
    }

    /// Every token the escrow holds is accounted for, and nothing more is promised.
    async fn assert_solvent(&self) -> Result<()> {
        let held = self.balance(&self.escrow).await?;
        let owed = self.liabilities().await?;
        ensure!(held == owed, "escrow holds {held} but owes {owed}");
        Ok(())
    }

    async fn create(&self, id: &str, terms: Terms) -> Result<()> {
        let deadline = self.now().await? + terms.deadline_in;
        let result = self
            .judge
            .call(&self.escrow, "create_bounty")
            .args_json(json!({
                "bounty_id": id,
                "owner": self.owner.id(),
                "judge": self.judge.id(),
                "fee_account": self.fee.id(),
                "reward": terms.reward.to_string(),
                "base_bond": BOND.to_string(),
                "fee_bps": terms.fee_bps,
                "scout_share_max_bps": 5000,
                "deadline_ns": deadline.to_string(),
                "rule_timeout_ns": terms.timeout.to_string(),
            }))
            .deposit(NearToken::from_near(1))
            .transact()
            .await?;
        done(result).await
    }

    /// mt_transfer_call to the escrow. Returns (used amount, result).
    async fn deposit(
        &self,
        from: &Account,
        amount: u128,
        msg: &str,
    ) -> Result<(u128, ExecutionFinalResult)> {
        self.deposit_token(from, &self.token.clone(), amount, msg)
            .await
    }

    async fn deposit_token(
        &self,
        from: &Account,
        token: &str,
        amount: u128,
        msg: &str,
    ) -> Result<(u128, ExecutionFinalResult)> {
        let result = ok(from
            .call(self.intents.id(), "mt_transfer_call")
            .args_json(json!({
                "receiver_id": self.escrow,
                "token_id": token,
                "amount": amount.to_string(),
                "msg": msg,
            }))
            .deposit(ONE_YOCTO)
            .gas(Gas::from_tgas(150))
            .transact()
            .await?)
        .await?;
        let used: Vec<String> = result.clone().json()?;
        Ok((used[0].parse()?, result))
    }

    async fn fund(&self, bounty: &str, amount: u128) -> Result<()> {
        let msg = json!({ "fund": bounty }).to_string();
        let (used, _) = self.deposit(&self.owner, amount, &msg).await?;
        ensure!(used == amount, "funding was refunded");
        Ok(())
    }

    async fn bond(&self, bounty: &str, claim: &str, scout: Option<u16>) -> Result<()> {
        let msg = bond_msg(bounty, claim, scout.map(|bps| (self.scout.id(), bps)));
        let (used, _) = self.deposit(&self.hunter, BOND, &msg).await?;
        ensure!(used == BOND, "bond was refunded");
        Ok(())
    }

    async fn rule(&self, by: &Account, claim: &str, outcome: &str) -> Result<ExecutionFinalResult> {
        Ok(by
            .call(&self.escrow, "rule")
            .args_json(json!({"claim_id": claim, "outcome": outcome, "evidence_hash": HASH}))
            .gas(RULE_GAS)
            .transact()
            .await?)
    }

    async fn balances(&self, accounts: &[&AccountId]) -> Result<Vec<u128>> {
        let mut found = Vec::new();
        for account in accounts {
            found.push(self.balance(account).await?);
        }
        Ok(found)
    }

    /// A NEP-413 signed `transfer` intent from `signer` to the escrow, with `msg`.
    async fn signed_transfer(&self, signer: &Account, amount: u128, msg: &str) -> Result<Value> {
        let salt: String = self.intents.view("current_salt").await?.json()?;
        let mut nonce = Vec::with_capacity(32);
        nonce.extend_from_slice(&[0x56, 0x28, 0xf6, 0xc6, 0x00]);
        nonce.extend_from_slice(&hex_bytes(&salt));
        nonce.extend_from_slice(&(DEADLINE_SECONDS * 1_000_000_000).to_le_bytes());
        nonce.extend_from_slice(&Sha256::digest(msg.as_bytes())[..15]);
        let mut tokens = BTreeMap::new();
        tokens.insert(self.token.clone(), amount.to_string());
        let message = json!({
            "deadline": DEADLINE,
            "intents": [{
                "intent": "transfer",
                "receiver_id": self.escrow,
                "tokens": tokens,
                "memo": "arsune:bond",
                "msg": msg,
            }],
            "signer_id": signer.id(),
        })
        .to_string();
        let recipient = self.intents.id().to_string();
        let mut data = (2u32.pow(31) + 413).to_le_bytes().to_vec();
        borsh_string(&mut data, &message);
        data.extend_from_slice(&nonce);
        borsh_string(&mut data, &recipient);
        data.push(0); // callback_url: None
        let key = signing_key(signer);
        let signature = key.sign(&Sha256::digest(&data));
        Ok(json!({
            "standard": "nep413",
            "payload": {
                "recipient": recipient,
                "nonce": base64::engine::general_purpose::STANDARD.encode(&nonce),
                "message": message,
            },
            "public_key": signer.secret_key().public_key().to_string(),
            "signature": format!("ed25519:{}", bs58::encode(signature.to_bytes()).into_string()),
        }))
    }

    async fn register_key(&self, account: &Account) -> Result<()> {
        done(
            account
                .call(self.intents.id(), "add_public_key")
                .args_json(json!({"public_key": account.secret_key().public_key().to_string()}))
                .deposit(ONE_YOCTO)
                .transact()
                .await?,
        )
        .await
    }

    /// Arsune (here the judge account) relays signed intents with execute_intents.
    async fn relay(&self, signed: Value) -> Result<ExecutionFinalResult> {
        ok(self
            .judge
            .call(self.intents.id(), "execute_intents")
            .args_json(json!({ "signed": [signed] }))
            .gas(Gas::from_tgas(200))
            .transact()
            .await?)
        .await
    }

    async fn set_paused(&self, paused: bool) -> Result<()> {
        let method = if paused {
            "pa_pause_feature"
        } else {
            "pa_unpause_feature"
        };
        done(
            self.root
                .call(self.intents.id(), method)
                .args_json(json!({"key": "mt_transfer"}))
                .transact()
                .await?,
        )
        .await?;
        Ok(())
    }
}

fn bond_msg(bounty: &str, claim: &str, scout: Option<(&AccountId, u16)>) -> String {
    let mut bond = json!({"bounty_id": bounty, "claim_id": claim});
    if let Some((account_id, share_bps)) = scout {
        bond["scout"] = json!({"account_id": account_id, "share_bps": share_bps});
    }
    json!({ "bond": bond }).to_string()
}

fn borsh_string(out: &mut Vec<u8>, text: &str) {
    out.extend_from_slice(&(text.len() as u32).to_le_bytes());
    out.extend_from_slice(text.as_bytes());
}

fn hex_bytes(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect()
}

fn signing_key(account: &Account) -> SigningKey {
    let text = account.secret_key().to_string();
    let raw = bs58::decode(text.trim_start_matches("ed25519:"))
        .into_vec()
        .unwrap();
    SigningKey::from_bytes(raw[..32].try_into().unwrap())
}

#[tokio::test]
async fn fund_bond_and_proven_pay_hunter_scout_and_fee() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", 2 * (REWARD + FEE)).await?;
    env.bond("b1", "c1", Some(2500)).await?;
    env.assert_solvent().await?;
    let parties = [env.hunter.id(), env.scout.id(), env.fee.id(), &env.escrow];
    let before = env.balances(&parties).await?;

    let result = ok(env.rule(&env.judge, "c1", "proven").await?).await?;
    println!(
        "rule proven with 3 payouts burnt {} Tgas",
        result.total_gas_burnt.as_tgas()
    );
    let after = env.balances(&parties).await?;
    let scout = REWARD / 4;
    assert_eq!(after[0] - before[0], REWARD - scout + BOND);
    assert_eq!(after[1] - before[1], scout);
    assert_eq!(after[2] - before[2], FEE);
    assert_eq!(before[3] - after[3], REWARD + FEE + BOND);
    let payouts = events(&result, "payout");
    assert_eq!(payouts.len(), 3);
    assert_eq!(payouts[0]["memo"], "arsune:reward:c1");
    let rule = &events(&result, "rule")[0];
    assert_eq!(rule["outcome"], "proven");
    assert_eq!(rule["evidence_hash"], HASH);
    let claim = env.claim("c1").await?;
    assert_eq!(claim["outcome"], "proven");
    assert_eq!(claim["evidence_hash"], HASH);
    let bounty = env.bounty("b1").await?;
    assert_eq!(bounty["balance"], (REWARD + FEE).to_string());
    assert_eq!(bounty["open_claims"], 0);
    env.assert_solvent().await
}

#[tokio::test]
async fn signed_transfer_intent_with_msg_posts_a_bond() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", REWARD + FEE).await?;
    env.register_key(&env.hunter).await?;
    let before = env.balance(env.hunter.id()).await?;

    // The hunter signs; Arsune relays. intents.near calls mt_on_transfer with the signer.
    let msg = bond_msg("b1", "c1", None);
    let result = env
        .relay(env.signed_transfer(&env.hunter, BOND, &msg).await?)
        .await?;
    for outcome in result.receipt_outcomes() {
        if outcome.executor_id == env.escrow {
            println!(
                "escrow mt_on_transfer burnt {} Tgas",
                outcome.gas_burnt.as_tgas()
            );
        }
    }
    let bonded = events(&result, "bond");
    assert_eq!(bonded.len(), 1);
    assert_eq!(bonded[0]["hunter"], env.hunter.id().as_str());
    assert_eq!(env.claim("c1").await?["hunter"], env.hunter.id().as_str());
    assert_eq!(before - env.balance(env.hunter.id()).await?, BOND);

    // A signed transfer whose msg the escrow does not accept is refunded in full.
    let result = env
        .relay(
            env.signed_transfer(&env.hunter, BOND, r#"{"bond":"nope"}"#)
                .await?,
        )
        .await?;
    assert_eq!(events(&result, "refund")[0]["reason"], "bad_msg");
    assert_eq!(before - env.balance(env.hunter.id()).await?, BOND);
    env.assert_solvent().await
}

#[tokio::test]
async fn rejected_duplicate_and_unverified_settle_bonds() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", REWARD + FEE).await?;
    for claim in ["c1", "c2", "c3"] {
        env.bond("b1", claim, None).await?;
    }
    let start = env.balance(env.hunter.id()).await?;

    let result = ok(env.rule(&env.judge, "c1", "rejected").await?).await?;
    assert!(events(&result, "payout").is_empty());
    assert_eq!(env.balance(env.hunter.id()).await?, start);
    let bounty = env.bounty("b1").await?;
    assert_eq!(bounty["balance"], (REWARD + FEE + BOND).to_string());
    assert_eq!(bounty["forfeited_total"], BOND.to_string());

    for (claim, outcome) in [("c2", "duplicate"), ("c3", "unverified")] {
        let result = ok(env.rule(&env.judge, claim, outcome).await?).await?;
        assert_eq!(
            events(&result, "payout")[0]["memo"],
            format!("arsune:refund:{claim}")
        );
        assert_eq!(env.claim(claim).await?["outcome"], outcome);
    }
    assert_eq!(env.balance(env.hunter.id()).await?, start + 2 * BOND);
    env.assert_solvent().await
}

#[tokio::test]
async fn proven_without_budget_becomes_unfunded() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", REWARD + FEE).await?;
    env.bond("b1", "c1", None).await?;
    env.bond("b1", "c2", None).await?;
    done(env.rule(&env.judge, "c1", "proven").await?).await?;
    let before = env.balances(&[env.hunter.id(), env.fee.id()]).await?;

    let result = ok(env.rule(&env.judge, "c2", "proven").await?).await?;
    let rule = &events(&result, "rule")[0];
    assert_eq!(rule["ruling"], "proven");
    assert_eq!(rule["outcome"], "unfunded");
    let after = env.balances(&[env.hunter.id(), env.fee.id()]).await?;
    assert_eq!(after[0] - before[0], BOND);
    assert_eq!(after[1], before[1]);
    let bounty = env.bounty("b1").await?;
    assert_eq!(bounty["status"], "closed");
    assert_eq!(bounty["balance"], "0");
    // A closed bounty refunds new bonds.
    let (used, result) = env
        .deposit(&env.hunter, BOND, &bond_msg("b1", "c3", None))
        .await?;
    assert_eq!(used, 0);
    assert_eq!(events(&result, "refund")[0]["reason"], "bounty_closed");
    env.assert_solvent().await
}

#[tokio::test]
async fn only_the_judge_rules_and_only_once() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", REWARD + FEE).await?;
    env.bond("b1", "c1", Some(5000)).await?;
    let parties = [env.hunter.id(), env.scout.id(), &env.escrow];
    let before = env.balances(&parties).await?;
    for account in [&env.owner, &env.hunter, &env.scout, &env.mallory] {
        let result = env.rule(account, "c1", "proven").await?;
        assert!(!result.is_success());
        assert!(failure_text(&result).contains("only the judge can rule"));
    }
    assert_eq!(env.balances(&parties).await?, before);
    done(env.rule(&env.judge, "c1", "rejected").await?).await?;
    let again = env.rule(&env.judge, "c1", "proven").await?;
    assert!(failure_text(&again).contains("the claim is already decided"));
    assert_eq!(env.balances(&parties).await?, before);
    env.assert_solvent().await
}

#[tokio::test]
async fn hunter_reclaims_bond_after_judge_timeout() -> Result<()> {
    let env = Env::new().await?;
    let timeout = 60 * SECOND;
    env.create(
        "b1",
        Terms {
            timeout,
            ..Terms::default()
        },
    )
    .await?;
    env.fund("b1", REWARD + FEE).await?;
    env.bond("b1", "c1", None).await?;
    let before = env.balance(env.hunter.id()).await?;
    let early = env
        .mallory
        .call(&env.escrow, "reclaim_bond")
        .args_json(json!({"claim_id": "c1"}))
        .gas(Gas::from_tgas(100))
        .transact()
        .await?;
    assert!(failure_text(&early).contains("the judge can still rule this claim"));

    env.advance(timeout).await?;
    // Anyone can trigger it; the bond can only go to the hunter.
    let result = ok(env
        .mallory
        .call(&env.escrow, "reclaim_bond")
        .args_json(json!({"claim_id": "c1"}))
        .gas(Gas::from_tgas(100))
        .transact()
        .await?)
    .await?;
    assert_eq!(events(&result, "expire").len(), 1);
    assert_eq!(env.balance(env.hunter.id()).await? - before, BOND);
    assert_eq!(env.claim("c1").await?["outcome"], "expired");
    let late = env.rule(&env.judge, "c1", "rejected").await?;
    assert!(failure_text(&late).contains("the claim is already decided"));
    env.assert_solvent().await
}

#[tokio::test]
async fn remaining_money_goes_only_to_the_owner_after_deadline() -> Result<()> {
    let env = Env::new().await?;
    let deadline_in = 120 * SECOND;
    env.create(
        "b1",
        Terms {
            deadline_in,
            ..Terms::default()
        },
    )
    .await?;
    env.fund("b1", REWARD + FEE).await?;
    env.bond("b1", "c1", None).await?;
    done(env.rule(&env.judge, "c1", "rejected").await?).await?;
    let withdraw = |account: &Account| {
        account
            .call(&env.escrow, "withdraw_remaining")
            .args_json(json!({"bounty_id": "b1"}))
            .gas(Gas::from_tgas(100))
            .transact()
    };
    for account in [&env.judge, &env.owner] {
        let early = withdraw(account).await?;
        assert!(failure_text(&early).contains("open until the owner closes it"));
    }
    let parties = [env.owner.id(), env.judge.id(), env.fee.id()];
    let before = env.balances(&parties).await?;
    let escrow_near = env.worker.view_account(&env.escrow).await?.balance;

    env.advance(deadline_in).await?;
    // The judge triggers the withdrawal, but the money goes to the owner.
    let result = ok(withdraw(&env.judge).await?).await?;
    let after = env.balances(&parties).await?;
    assert_eq!(after[0] - before[0], REWARD + FEE + BOND);
    assert_eq!(after[1], before[1]);
    assert_eq!(after[2], before[2]);
    let w = &events(&result, "withdraw")[0];
    let storage: u128 = w["storage_refund"].as_str().unwrap().parse()?;
    assert!(storage > 0);
    let escrow_after = env.worker.view_account(&env.escrow).await?.balance;
    assert!(escrow_near.as_yoctonear() - escrow_after.as_yoctonear() >= storage);
    assert!(failure_text(&withdraw(&env.owner).await?).contains("nothing to withdraw"));
    assert_eq!(env.balance(&env.escrow).await?, 0);
    env.assert_solvent().await
}

#[tokio::test]
async fn unexpected_deposits_are_refunded() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", REWARD + FEE).await?;
    let start = env.balance(env.hunter.id()).await?;
    let cases = [
        ("not json".to_string(), BOND, "bad_msg"),
        (r#"{"fund":"b1","extra":1}"#.to_string(), BOND, "bad_msg"),
        (json!({"fund": "b9"}).to_string(), BOND, "unknown_bounty"),
        (
            bond_msg("b1", "c1", None),
            3 * BOND + 1,
            "bond_out_of_range",
        ),
        (
            bond_msg("b1", "c1", None),
            BOND / 2 - 1,
            "bond_out_of_range",
        ),
        (
            bond_msg("b1", "c1", Some((env.judge.id(), 10))),
            BOND,
            "bad_scout",
        ),
    ];
    for (msg, amount, reason) in cases {
        let (used, result) = env.deposit(&env.hunter, amount, &msg).await?;
        assert_eq!(used, 0, "{msg} was kept");
        assert_eq!(events(&result, "refund")[0]["reason"], reason);
    }
    // The judge cannot post a bond on its own bounty.
    env.mint(env.judge.id(), BOND).await?;
    let (used, _) = env
        .deposit(&env.judge, BOND, &bond_msg("b1", "c1", None))
        .await?;
    assert_eq!(used, 0);
    // Another token is refunded even with a valid message.
    let other = env
        .root
        .create_subaccount("other")
        .initial_balance(NearToken::from_near(5))
        .transact()
        .await?
        .into_result()?
        .deploy(&wasm("FT_WASM"))
        .await?
        .into_result()?;
    done(
        other
            .call("new_default_meta")
            .args_json(json!({"owner_id": other.id(), "total_supply": "1000000"}))
            .transact()
            .await?,
    )
    .await?;
    done(
        env.root
            .call(other.id(), "storage_deposit")
            .args_json(json!({"account_id": env.intents.id()}))
            .deposit(NearToken::from_millinear(10))
            .transact()
            .await?,
    )
    .await?;
    done(
        other
            .call("ft_transfer_call")
            .args_json(
                json!({"receiver_id": env.intents.id(), "amount": "1000", "msg": env.hunter.id()}),
            )
            .deposit(ONE_YOCTO)
            .gas(Gas::from_tgas(100))
            .transact()
            .await?,
    )
    .await?;
    let other_token = format!("nep141:{}", other.id());
    let (used, result) = env
        .deposit_token(
            &env.hunter,
            &other_token,
            1000,
            &json!({"fund": "b1"}).to_string(),
        )
        .await?;
    assert_eq!(used, 0);
    assert_eq!(events(&result, "refund")[0]["reason"], "wrong_token");
    assert_eq!(env.balance(env.hunter.id()).await?, start);
    // Only intents.near can notify a deposit.
    let forged = env
        .mallory
        .call(&env.escrow, "mt_on_transfer")
        .args_json(json!({
            "sender_id": env.mallory.id(),
            "previous_owner_ids": [env.mallory.id()],
            "token_ids": [env.token],
            "amounts": ["1000000"],
            "msg": json!({"fund": "b1"}).to_string(),
        }))
        .transact()
        .await?;
    assert!(failure_text(&forged).contains("only the intents contract can notify deposits"));
    assert_eq!(
        env.bounty("b1").await?["balance"],
        (REWARD + FEE).to_string()
    );
    env.assert_solvent().await
}

#[tokio::test]
async fn failed_payouts_roll_back_into_owed_and_retry_pays() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", REWARD + FEE).await?;
    env.bond("b1", "c1", Some(2000)).await?;
    let parties = [env.hunter.id(), env.scout.id(), env.fee.id(), &env.escrow];
    let before = env.balances(&parties).await?;

    // intents.near pauses mt_transfer: every payout of the ruling fails.
    env.set_paused(true).await?;
    let result = ok(env.rule(&env.judge, "c1", "proven").await?).await?;
    assert_eq!(events(&result, "payout_failed").len(), 3);
    assert!(events(&result, "payout").is_empty());
    assert_eq!(env.balances(&parties).await?, before);
    // The ruling stands; the money is owed, not lost or spendable twice.
    assert_eq!(env.claim("c1").await?["outcome"], "proven");
    let scout = REWARD / 5;
    let owed = |account: &AccountId| env.view("get_owed", json!({"account_id": account}));
    assert_eq!(
        owed(env.hunter.id()).await?,
        (REWARD - scout + BOND).to_string()
    );
    assert_eq!(owed(env.scout.id()).await?, scout.to_string());
    assert_eq!(owed(env.fee.id()).await?, FEE.to_string());
    env.assert_solvent().await?;

    // While still paused, a retry fails again and stays owed.
    let retry = |account: &AccountId| {
        env.mallory
            .call(&env.escrow, "retry_payout")
            .args_json(json!({"account_id": account}))
            .gas(Gas::from_tgas(100))
            .transact()
    };
    let result = ok(retry(env.hunter.id()).await?).await?;
    assert_eq!(events(&result, "payout_failed").len(), 1);
    env.assert_solvent().await?;

    env.set_paused(false).await?;
    for account in [env.hunter.id(), env.scout.id(), env.fee.id()] {
        let result = ok(retry(account).await?).await?;
        assert_eq!(events(&result, "payout").len(), 1);
        assert_eq!(owed(account).await?, "0");
    }
    let after = env.balances(&parties).await?;
    assert_eq!(after[0] - before[0], REWARD - scout + BOND);
    assert_eq!(after[1] - before[1], scout);
    assert_eq!(after[2] - before[2], FEE);
    assert!(failure_text(&retry(env.hunter.id()).await?).contains("nothing is owed"));
    env.assert_solvent().await
}

#[tokio::test]
async fn escrow_is_locked_and_callbacks_are_private() -> Result<()> {
    let env = Env::new().await?;
    env.create("b1", Terms::default()).await?;
    env.fund("b1", REWARD + FEE).await?;
    let forged = env
        .mallory
        .call(&env.escrow, "on_payout")
        .args_json(json!({"receiver_id": env.mallory.id(), "amount": "1000", "memo": "x"}))
        .transact()
        .await?;
    assert!(failure_text(&forged).contains("is private"));
    let reinit = env
        .mallory
        .call(&env.escrow, "new")
        .args_json(json!({"intents_contract": env.mallory.id(), "token_id": "x"}))
        .transact()
        .await?;
    assert!(!reinit.is_success());
    let keys = env.worker.view_access_keys(&env.escrow).await?;
    assert!(keys.is_empty());
    // Plain transfers without msg are not attributed to any bounty (documented gap).
    done(
        env.mallory
            .call(env.intents.id(), "mt_transfer")
            .args_json(json!({"receiver_id": env.escrow, "token_id": env.token, "amount": "7"}))
            .deposit(ONE_YOCTO)
            .transact()
            .await?,
    )
    .await?;
    assert_eq!(
        env.balance(&env.escrow).await?,
        env.liabilities().await? + 7
    );
    Ok(())
}
