//! Arsune bounty escrow.
//!
//! The escrow holds bounty money and hunter bonds as its own NEP-245 balance on the NEAR
//! Intents Verifier (`intents.near`). Deposits arrive through `mt_on_transfer`. Only the judge
//! decides claims, but the money rules are fixed here: every amount and every receiver of a
//! ruling follows from immutable bounty terms and the hunter-signed bond. The judge can never
//! move bounty money to itself or to an account of its choice. There is no owner, no admin
//! and no upgrade method. See docs/ESCROW.md.

use near_sdk::json_types::{U128, U64};
use near_sdk::store::LookupMap;
use near_sdk::{
    env, ext_contract, near, require, AccountId, BorshStorageKey, Gas, NearToken, PanicOnDefault,
    Promise, PromiseError, PromiseOrValue,
};

mod events;
mod types;

pub use events::Event;
pub use types::*;

pub const MAX_FEE_BPS: u16 = 2_000;
pub const MAX_SCOUT_SHARE_BPS: u16 = 5_000;
pub const MAX_BOND_MULTIPLIER: u128 = 3;
/// Bounds amounts so that `reward * fee_bps` and `base_bond * 3` cannot overflow.
pub const MAX_AMOUNT: u128 = 10u128.pow(30);
const SECOND: u64 = 1_000_000_000;
pub const MIN_RULE_TIMEOUT_NS: u64 = 60 * SECOND;
pub const MAX_RULE_TIMEOUT_NS: u64 = 30 * 24 * 3600 * SECOND;
pub const MAX_ID_LEN: usize = 64;
pub const MAX_PAGE: u32 = 100;
/// Upper bound of the storage that one bond adds (claim record, decided, plus its list slot)
/// with the longest IDs and account names. A unit test checks the bound.
pub const CLAIM_STORAGE_BYTES: u128 = 600;
/// intents.near `mt_transfer` burns a few Tgas; a payout that runs out of gas only becomes an
/// owed payout that anyone can retry.
const GAS_MT_TRANSFER: Gas = Gas::from_tgas(20);
const GAS_ON_PAYOUT: Gas = Gas::from_tgas(10);
const ONE_YOCTO: NearToken = NearToken::from_yoctonear(1);

#[ext_contract(ext_intents)]
#[allow(dead_code)]
trait Intents {
    fn mt_transfer(
        &mut self,
        receiver_id: AccountId,
        token_id: String,
        amount: U128,
        approval: Option<(AccountId, u64)>,
        memo: Option<String>,
    );
}

#[near(serializers = [borsh])]
#[derive(BorshStorageKey)]
enum Key {
    Bounties,
    Claims,
    ClaimSlots,
    Owed,
}

#[near(
    contract_state,
    contract_metadata(standard(standard = "arsune-escrow", version = "1.0.0"))
)]
#[derive(PanicOnDefault)]
pub struct Escrow {
    intents: AccountId,
    token_id: String,
    bounties: LookupMap<String, Bounty>,
    claims: LookupMap<String, Claim>,
    /// (bounty_id, index) -> claim_id, for paging the claims of one bounty.
    claim_slots: LookupMap<(String, u32), String>,
    /// Payouts whose transfer failed. Anyone can call `retry_payout` for the account.
    owed: LookupMap<AccountId, u128>,
    liabilities: u128,
}

fn valid_id(id: &str) -> bool {
    (1..=MAX_ID_LEN).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'_')
}

fn parse_hash(text: &str) -> [u8; 32] {
    let bytes = text.as_bytes();
    require!(
        bytes.len() == 64
            && bytes
                .iter()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(b)),
        "evidence_hash must be 64 lowercase hex characters (sha256)"
    );
    let mut hash = [0u8; 32];
    for (i, pair) in bytes.chunks(2).enumerate() {
        let text = std::str::from_utf8(pair).unwrap();
        hash[i] = u8::from_str_radix(text, 16).unwrap();
    }
    hash
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn memo(kind: &str, id: &str) -> String {
    format!("arsune:{kind}:{id}")
}

pub fn claim_storage_cost() -> u128 {
    CLAIM_STORAGE_BYTES * env::storage_byte_cost().as_yoctonear()
}

#[near]
impl Escrow {
    #[init]
    pub fn new(intents_contract: AccountId, token_id: String) -> Self {
        require!(
            !token_id.is_empty() && token_id.len() <= 128,
            "token_id must be 1 to 128 characters"
        );
        Self {
            intents: intents_contract,
            token_id,
            bounties: LookupMap::new(Key::Bounties),
            claims: LookupMap::new(Key::Claims),
            claim_slots: LookupMap::new(Key::ClaimSlots),
            owed: LookupMap::new(Key::Owed),
            liabilities: 0,
        }
    }

    /// Create a bounty with immutable terms. The caller must be the owner or the judge and
    /// attaches NEAR for storage: the bounty record plus at least one claim. NEAR above the
    /// bounty record pays for claims; what is left goes back to the caller on withdrawal.
    #[payable]
    #[allow(clippy::too_many_arguments)]
    pub fn create_bounty(
        &mut self,
        bounty_id: String,
        owner: AccountId,
        judge: AccountId,
        fee_account: AccountId,
        reward: U128,
        base_bond: U128,
        fee_bps: u16,
        scout_share_max_bps: u16,
        deadline_ns: U64,
        rule_timeout_ns: U64,
    ) -> BountyView {
        let payer = env::predecessor_account_id();
        require!(
            payer == owner || payer == judge,
            "only the owner or the judge can create a bounty"
        );
        require!(
            valid_id(&bounty_id),
            "bounty_id must be 1 to 64 characters of a-z, 0-9, - and _"
        );
        require!(
            !self.bounties.contains_key(&bounty_id),
            "bounty_id is already used"
        );
        let me = env::current_account_id();
        for party in [&owner, &judge, &fee_account] {
            require!(
                *party != me && *party != self.intents,
                "the escrow and the intents contract cannot be parties"
            );
        }
        require!(
            reward.0 > 0 && reward.0 <= MAX_AMOUNT,
            "reward must be positive"
        );
        require!(
            base_bond.0 > 0 && base_bond.0 <= MAX_AMOUNT,
            "base_bond must be positive"
        );
        require!(fee_bps <= MAX_FEE_BPS, "fee_bps must be at most 2000");
        require!(
            scout_share_max_bps <= MAX_SCOUT_SHARE_BPS,
            "scout_share_max_bps must be at most 5000"
        );
        let now = env::block_timestamp();
        require!(deadline_ns.0 > now, "deadline must be in the future");
        require!(
            (MIN_RULE_TIMEOUT_NS..=MAX_RULE_TIMEOUT_NS).contains(&rule_timeout_ns.0),
            "rule_timeout_ns must be 60 s to 30 days"
        );
        let bounty = Bounty {
            owner,
            judge,
            fee_account,
            reward: reward.0,
            base_bond: base_bond.0,
            fee_bps,
            scout_share_max_bps,
            deadline_ns: deadline_ns.0,
            rule_timeout_ns: rule_timeout_ns.0,
            created_ns: now,
            closed: None,
            balance: 0,
            bonds_held: 0,
            open_claims: 0,
            claim_count: 0,
            funded_total: 0,
            rewards_total: 0,
            fees_total: 0,
            forfeited_total: 0,
            withdrawn_total: 0,
            storage_payer: payer,
            storage_pool: 0,
        };
        let before = env::storage_usage();
        self.bounties.insert(bounty_id.clone(), bounty);
        self.bounties.flush();
        let record =
            u128::from(env::storage_usage() - before) * env::storage_byte_cost().as_yoctonear();
        let attached = env::attached_deposit().as_yoctonear();
        let needed = record + claim_storage_cost();
        if attached < needed {
            env::panic_str(&format!(
                "attach at least {needed} yoctoNEAR: the bounty record and one claim"
            ));
        }
        let bounty = self.bounties.get_mut(&bounty_id).unwrap();
        bounty.storage_pool = attached - record;
        Event::Create {
            bounty_id: bounty_id.clone(),
            owner: bounty.owner.clone(),
            judge: bounty.judge.clone(),
            fee_account: bounty.fee_account.clone(),
            reward,
            base_bond,
            fee_bps,
            scout_share_max_bps,
            deadline_ns,
            rule_timeout_ns,
        }
        .emit();
        self.bounty_view(&bounty_id)
    }

    /// NEP-245 receiver. intents.near calls it after `mt_transfer_call`, or after a signed
    /// `transfer` intent that has a `msg`, has already moved the tokens to the escrow. The
    /// returned amounts are refunded to the sender, so every deposit that is not accepted
    /// goes back in full.
    pub fn mt_on_transfer(
        &mut self,
        sender_id: AccountId,
        previous_owner_ids: Vec<AccountId>,
        token_ids: Vec<String>,
        amounts: Vec<U128>,
        msg: String,
    ) -> PromiseOrValue<Vec<U128>> {
        require!(
            env::predecessor_account_id() == self.intents,
            "only the intents contract can notify deposits"
        );
        match self.accept_deposit(&sender_id, &previous_owner_ids, &token_ids, &amounts, &msg) {
            Ok(()) => PromiseOrValue::Value(vec![U128(0); amounts.len()]),
            Err(reason) => {
                Event::Refund {
                    sender_id,
                    token_ids,
                    amounts: amounts.clone(),
                    reason: reason.to_string(),
                }
                .emit();
                PromiseOrValue::Value(amounts)
            }
        }
    }

    /// The judge decides an open claim once. Every amount and receiver follows from the bounty
    /// terms and the bond; the judge chooses only the outcome and the evidence hash.
    pub fn rule(&mut self, claim_id: String, outcome: Outcome, evidence_hash: String) -> RuleView {
        let mut claim = self
            .claims
            .get(&claim_id)
            .cloned()
            .unwrap_or_else(|| env::panic_str("unknown claim"));
        require!(claim.outcome.is_none(), "the claim is already decided");
        let mut bounty = self.bounty(&claim.bounty_id).clone();
        require!(
            env::predecessor_account_id() == bounty.judge,
            "only the judge can rule"
        );
        require!(outcome != Outcome::Expired, "expired is not a ruling");
        let hash = parse_hash(&evidence_hash);
        let cost = bounty.claim_cost();
        let funded = bounty.balance >= cost;
        if outcome == Outcome::Unfunded {
            require!(!funded, "the bounty can pay this claim");
        }
        let result = if outcome == Outcome::Proven && !funded {
            Outcome::Unfunded
        } else {
            outcome
        };
        bounty.open_claims -= 1;
        bounty.bonds_held -= claim.bond;
        let mut view = RuleView {
            claim_id: claim_id.clone(),
            outcome: Some(result),
            ..Default::default()
        };
        let mut payouts: Vec<(AccountId, u128, String)> = Vec::new();
        let mut close = None;
        match result {
            Outcome::Proven => {
                let fee = bounty.fee();
                let scout = claim
                    .scout
                    .as_ref()
                    .map_or(0, |s| bounty.reward * u128::from(s.share_bps) / BPS);
                let hunter = bounty.reward - scout + claim.bond;
                bounty.balance -= cost;
                bounty.rewards_total += bounty.reward;
                bounty.fees_total += fee;
                payouts.push((claim.hunter.clone(), hunter, memo("reward", &claim_id)));
                if let Some(s) = &claim.scout {
                    payouts.push((s.account_id.clone(), scout, memo("scout", &claim_id)));
                }
                payouts.push((bounty.fee_account.clone(), fee, memo("fee", &claim_id)));
                view.hunter_amount = U128(hunter);
                view.scout_amount = U128(scout);
                view.fee_amount = U128(fee);
                if bounty.balance < cost && bounty.closed.is_none() {
                    close = Some(CloseReason::BudgetSpent);
                }
            }
            Outcome::Rejected => {
                bounty.balance += claim.bond;
                bounty.forfeited_total += claim.bond;
                view.forfeited = U128(claim.bond);
            }
            _ => {
                payouts.push((claim.hunter.clone(), claim.bond, memo("refund", &claim_id)));
                view.hunter_amount = U128(claim.bond);
                if result == Outcome::Unfunded && bounty.closed.is_none() {
                    close = Some(CloseReason::Unfunded);
                }
            }
        }
        if close.is_some() {
            bounty.closed = close;
        }
        claim.outcome = Some(result);
        claim.evidence_hash = Some(hash);
        claim.decided_ns = env::block_timestamp();
        Event::Rule {
            bounty_id: claim.bounty_id.clone(),
            claim_id: claim_id.clone(),
            judge: bounty.judge.clone(),
            ruling: outcome,
            outcome: result,
            evidence_hash,
            hunter_amount: view.hunter_amount,
            scout_amount: view.scout_amount,
            fee_amount: view.fee_amount,
            forfeited: view.forfeited,
        }
        .emit();
        if let Some(reason) = close {
            Event::Close {
                bounty_id: claim.bounty_id.clone(),
                reason,
            }
            .emit();
        }
        self.bounties.insert(claim.bounty_id.clone(), bounty);
        self.claims.insert(claim_id, claim);
        for (receiver, amount, memo) in payouts {
            self.pay(receiver, amount, memo);
        }
        view
    }

    /// After the judge's timeout, anyone can return an undecided bond to its hunter.
    pub fn reclaim_bond(&mut self, claim_id: String) -> U128 {
        let mut claim = self
            .claims
            .get(&claim_id)
            .cloned()
            .unwrap_or_else(|| env::panic_str("unknown claim"));
        require!(claim.outcome.is_none(), "the claim is already decided");
        let mut bounty = self.bounty(&claim.bounty_id).clone();
        let now = env::block_timestamp();
        require!(
            now >= claim.bonded_ns.saturating_add(bounty.rule_timeout_ns),
            "the judge can still rule this claim"
        );
        bounty.open_claims -= 1;
        bounty.bonds_held -= claim.bond;
        claim.outcome = Some(Outcome::Expired);
        claim.decided_ns = now;
        Event::Expire {
            bounty_id: claim.bounty_id.clone(),
            claim_id: claim_id.clone(),
            hunter: claim.hunter.clone(),
            bond: U128(claim.bond),
        }
        .emit();
        let (hunter, bond) = (claim.hunter.clone(), claim.bond);
        self.bounties.insert(claim.bounty_id.clone(), bounty);
        self.claims.insert(claim_id.clone(), claim);
        self.pay(hunter, bond, memo("expire", &claim_id));
        U128(bond)
    }

    /// The owner stops new funding and new claims. Open claims are still ruled.
    #[payable]
    pub fn close_bounty(&mut self, bounty_id: String) {
        near_sdk::assert_one_yocto();
        let bounty = self
            .bounties
            .get_mut(&bounty_id)
            .unwrap_or_else(|| env::panic_str("unknown bounty"));
        require!(
            env::predecessor_account_id() == bounty.owner,
            "only the owner can close the bounty"
        );
        require!(bounty.closed.is_none(), "the bounty is already closed");
        bounty.closed = Some(CloseReason::Owner);
        Event::Close {
            bounty_id,
            reason: CloseReason::Owner,
        }
        .emit();
    }

    /// Send the remaining balance to the owner and unused storage NEAR to its payer, once the
    /// bounty is closed or past its deadline and no claim is open. Anyone can call it: the
    /// receivers are fixed.
    pub fn withdraw_remaining(&mut self, bounty_id: String) -> U128 {
        let now = env::block_timestamp();
        let bounty = self
            .bounties
            .get_mut(&bounty_id)
            .unwrap_or_else(|| env::panic_str("unknown bounty"));
        require!(
            bounty.closed.is_some() || now >= bounty.deadline_ns,
            "the bounty is open until the owner closes it or the deadline passes"
        );
        require!(bounty.open_claims == 0, "claims are still open");
        let (amount, storage) = (bounty.balance, bounty.storage_pool);
        require!(amount > 0 || storage > 0, "nothing to withdraw");
        let closing = bounty.closed.is_none();
        if closing {
            bounty.closed = Some(CloseReason::Deadline);
        }
        bounty.balance = 0;
        bounty.storage_pool = 0;
        bounty.withdrawn_total += amount;
        let (owner, payer) = (bounty.owner.clone(), bounty.storage_payer.clone());
        if closing {
            Event::Close {
                bounty_id: bounty_id.clone(),
                reason: CloseReason::Deadline,
            }
            .emit();
        }
        Event::Withdraw {
            bounty_id: bounty_id.clone(),
            owner: owner.clone(),
            amount: U128(amount),
            storage_refund: U128(storage),
        }
        .emit();
        self.pay(owner, amount, memo("withdraw", &bounty_id));
        if storage > 0 {
            Promise::new(payer)
                .transfer(NearToken::from_yoctonear(storage))
                .detach();
        }
        U128(amount)
    }

    /// Send again what a failed payout left owed to `account_id`. Anyone can call it.
    pub fn retry_payout(&mut self, account_id: AccountId) -> U128 {
        let amount = self
            .owed
            .remove(&account_id)
            .unwrap_or_else(|| env::panic_str("nothing is owed to this account"));
        self.pay(
            account_id.clone(),
            amount,
            memo("retry", account_id.as_str()),
        );
        U128(amount)
    }

    #[private]
    pub fn on_payout(&mut self, receiver_id: AccountId, amount: U128, memo: String) -> bool {
        // mt_transfer returns nothing; only a failed promise means the tokens did not move.
        // No panic is possible below, so the rollback into `owed` is always saved.
        let sent = !matches!(env::promise_result_checked(0, 0), Err(PromiseError::Failed));
        if sent {
            Event::Payout {
                receiver_id,
                amount,
                memo,
            }
            .emit();
        } else {
            let owed = self
                .owed
                .get(&receiver_id)
                .copied()
                .unwrap_or(0)
                .saturating_add(amount.0);
            self.owed.insert(receiver_id.clone(), owed);
            self.liabilities = self.liabilities.saturating_add(amount.0);
            Event::PayoutFailed {
                receiver_id,
                amount,
                memo,
                owed: U128(owed),
            }
            .emit();
        }
        sent
    }

    pub fn get_config(&self) -> ConfigView {
        ConfigView {
            intents_contract: self.intents.clone(),
            token_id: self.token_id.clone(),
            max_fee_bps: MAX_FEE_BPS,
            max_scout_share_bps: MAX_SCOUT_SHARE_BPS,
            max_bond_multiplier: U128(MAX_BOND_MULTIPLIER),
            min_rule_timeout_ns: U64(MIN_RULE_TIMEOUT_NS),
            max_rule_timeout_ns: U64(MAX_RULE_TIMEOUT_NS),
            claim_storage_cost: U128(claim_storage_cost()),
            liabilities: U128(self.liabilities),
        }
    }

    pub fn get_bounty(&self, bounty_id: String) -> Option<BountyView> {
        self.bounties
            .contains_key(&bounty_id)
            .then(|| self.bounty_view(&bounty_id))
    }

    pub fn get_claim(&self, claim_id: String) -> Option<ClaimView> {
        self.claims
            .get(&claim_id)
            .map(|claim| self.claim_view(claim_id.clone(), claim))
    }

    /// Claims of one bounty in bond order, at most 100 per page.
    pub fn list_claims(
        &self,
        bounty_id: String,
        from_index: Option<u32>,
        limit: Option<u32>,
    ) -> Vec<ClaimView> {
        let count = self.bounty(&bounty_id).claim_count;
        let start = from_index.unwrap_or(0).min(count);
        let end = start
            .saturating_add(limit.unwrap_or(MAX_PAGE).min(MAX_PAGE))
            .min(count);
        (start..end)
            .filter_map(|index| self.claim_slots.get(&(bounty_id.clone(), index)))
            .filter_map(|claim_id| {
                let claim = self.claims.get(claim_id)?;
                Some(self.claim_view(claim_id.clone(), claim))
            })
            .collect()
    }

    pub fn get_owed(&self, account_id: AccountId) -> U128 {
        U128(self.owed.get(&account_id).copied().unwrap_or(0))
    }
}

impl Escrow {
    fn bounty(&self, bounty_id: &str) -> &Bounty {
        self.bounties
            .get(bounty_id)
            .unwrap_or_else(|| env::panic_str("unknown bounty"))
    }

    fn accept_deposit(
        &mut self,
        sender_id: &AccountId,
        previous_owner_ids: &[AccountId],
        token_ids: &[String],
        amounts: &[U128],
        msg: &str,
    ) -> Result<(), &'static str> {
        if token_ids != [self.token_id.as_str()]
            || amounts.len() != 1
            || previous_owner_ids != [sender_id.clone()]
        {
            return Err("wrong_token");
        }
        let amount = amounts[0].0;
        if amount == 0 {
            return Err("zero_amount");
        }
        let parsed: DepositMsg = near_sdk::serde_json::from_str(msg).map_err(|_| "bad_msg")?;
        let liabilities = self.liabilities.checked_add(amount).ok_or("overflow")?;
        match parsed {
            DepositMsg::Fund(bounty_id) => self.accept_fund(sender_id, amount, bounty_id)?,
            DepositMsg::Bond(bond) => self.accept_bond(sender_id, amount, bond)?,
        }
        self.liabilities = liabilities;
        Ok(())
    }

    fn open_bounty(&self, bounty_id: &str) -> Result<&Bounty, &'static str> {
        let bounty = self.bounties.get(bounty_id).ok_or("unknown_bounty")?;
        if bounty.closed.is_some() {
            return Err("bounty_closed");
        }
        if env::block_timestamp() >= bounty.deadline_ns {
            return Err("deadline_passed");
        }
        Ok(bounty)
    }

    fn accept_fund(
        &mut self,
        sender_id: &AccountId,
        amount: u128,
        bounty_id: String,
    ) -> Result<(), &'static str> {
        let balance = self
            .open_bounty(&bounty_id)?
            .balance
            .checked_add(amount)
            .ok_or("overflow")?;
        let bounty = self.bounties.get_mut(&bounty_id).unwrap();
        bounty.balance = balance;
        bounty.funded_total += amount;
        Event::Fund {
            bounty_id,
            sender_id: sender_id.clone(),
            amount: U128(amount),
            balance: U128(balance),
        }
        .emit();
        Ok(())
    }

    fn accept_bond(
        &mut self,
        hunter: &AccountId,
        amount: u128,
        bond: BondMsg,
    ) -> Result<(), &'static str> {
        if !valid_id(&bond.claim_id) {
            return Err("bad_claim_id");
        }
        if self.claims.contains_key(&bond.claim_id) {
            return Err("claim_exists");
        }
        let bounty = self.open_bounty(&bond.bounty_id)?;
        if bounty.balance < bounty.claim_cost() {
            return Err("budget_too_low");
        }
        if amount < bounty.min_bond() || amount > bounty.max_bond() {
            return Err("bond_out_of_range");
        }
        let me = env::current_account_id();
        if *hunter == bounty.judge || *hunter == me {
            return Err("bad_hunter");
        }
        if let Some(scout) = &bond.scout {
            let bad_account = scout.account_id == bounty.judge || scout.account_id == me;
            if scout.share_bps == 0 || scout.share_bps > bounty.scout_share_max_bps || bad_account {
                return Err("bad_scout");
            }
        }
        let storage = claim_storage_cost();
        if bounty.storage_pool < storage {
            return Err("no_storage");
        }
        let bounty = self.bounties.get_mut(&bond.bounty_id).unwrap();
        let index = bounty.claim_count;
        bounty.claim_count += 1;
        bounty.open_claims += 1;
        bounty.bonds_held += amount;
        bounty.storage_pool -= storage;
        self.claim_slots
            .insert((bond.bounty_id.clone(), index), bond.claim_id.clone());
        Event::Bond {
            bounty_id: bond.bounty_id.clone(),
            claim_id: bond.claim_id.clone(),
            hunter: hunter.clone(),
            amount: U128(amount),
            scout: bond.scout.clone(),
        }
        .emit();
        self.claims.insert(
            bond.claim_id,
            Claim {
                bounty_id: bond.bounty_id,
                hunter: hunter.clone(),
                bond: amount,
                scout: bond.scout,
                bonded_ns: env::block_timestamp(),
                outcome: None,
                evidence_hash: None,
                decided_ns: 0,
            },
        );
        Ok(())
    }

    /// Move `amount` from the escrow's intents balance. A failed transfer is not lost: the
    /// callback adds it to `owed` for a retry.
    fn pay(&mut self, receiver_id: AccountId, amount: u128, memo: String) {
        if amount == 0 {
            return;
        }
        self.liabilities -= amount;
        ext_intents::ext(self.intents.clone())
            .with_attached_deposit(ONE_YOCTO)
            .with_static_gas(GAS_MT_TRANSFER)
            .mt_transfer(
                receiver_id.clone(),
                self.token_id.clone(),
                U128(amount),
                None,
                Some(memo.clone()),
            )
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(GAS_ON_PAYOUT)
                    .with_unused_gas_weight(0)
                    .on_payout(receiver_id, U128(amount), memo),
            )
            .detach();
    }

    fn bounty_view(&self, bounty_id: &str) -> BountyView {
        let b = self.bounty(bounty_id);
        BountyView {
            bounty_id: bounty_id.to_string(),
            owner: b.owner.clone(),
            judge: b.judge.clone(),
            fee_account: b.fee_account.clone(),
            reward: U128(b.reward),
            fee: U128(b.fee()),
            fee_bps: b.fee_bps,
            base_bond: U128(b.base_bond),
            min_bond: U128(b.min_bond()),
            max_bond: U128(b.max_bond()),
            scout_share_max_bps: b.scout_share_max_bps,
            deadline_ns: U64(b.deadline_ns),
            rule_timeout_ns: U64(b.rule_timeout_ns),
            created_ns: U64(b.created_ns),
            status: if b.closed.is_some() { "closed" } else { "open" }.to_string(),
            close_reason: b.closed,
            balance: U128(b.balance),
            bonds_held: U128(b.bonds_held),
            open_claims: b.open_claims,
            claim_count: b.claim_count,
            claims_left: U64((b.storage_pool / claim_storage_cost()) as u64),
            funded_total: U128(b.funded_total),
            rewards_total: U128(b.rewards_total),
            fees_total: U128(b.fees_total),
            forfeited_total: U128(b.forfeited_total),
            withdrawn_total: U128(b.withdrawn_total),
            storage_payer: b.storage_payer.clone(),
            storage_pool: U128(b.storage_pool),
        }
    }

    fn claim_view(&self, claim_id: String, claim: &Claim) -> ClaimView {
        let timeout = self.bounty(&claim.bounty_id).rule_timeout_ns;
        ClaimView {
            claim_id,
            bounty_id: claim.bounty_id.clone(),
            hunter: claim.hunter.clone(),
            bond: U128(claim.bond),
            scout: claim.scout.clone(),
            bonded_ns: U64(claim.bonded_ns),
            reclaim_after_ns: U64(claim.bonded_ns.saturating_add(timeout)),
            outcome: claim.outcome,
            evidence_hash: claim.evidence_hash.map(|h| hex(&h)),
            decided_ns: claim.outcome.map(|_| U64(claim.decided_ns)),
        }
    }
}

#[cfg(test)]
mod tests;
