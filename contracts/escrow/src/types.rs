use near_sdk::json_types::{U128, U64};
use near_sdk::{near, AccountId};

pub const BPS: u128 = 10_000;

#[near(serializers = [borsh, json])]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    /// The judge reproduced the fault and the bounty paid the reward.
    Proven,
    /// The judge saw evidence that contradicts the claim. The bond goes to the bounty.
    Rejected,
    /// Another claim already proved the same fault. The bond is refunded.
    Duplicate,
    /// Access, resources or scope prevented a conclusion. The bond is refunded.
    Unverified,
    /// Proven, but the bounty could not pay one reward and its fee. The bond is refunded.
    Unfunded,
    /// Not a ruling: the judge did not rule in time and the bond went back to the hunter.
    Expired,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CloseReason {
    /// The owner closed the bounty.
    Owner,
    /// After a paid claim the balance could not pay one more reward and its fee.
    BudgetSpent,
    /// A proven claim found the balance unable to pay.
    Unfunded,
    /// The deadline passed and the remaining balance was withdrawn.
    Deadline,
}

impl CloseReason {
    /// Only a spent budget lets a top-up open the bounty again (RULES.md "Closing and
    /// top-ups"). An owner close and the deadline are final.
    pub fn takes_top_ups(self) -> bool {
        matches!(self, CloseReason::BudgetSpent | CloseReason::Unfunded)
    }
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Scout {
    pub account_id: AccountId,
    pub share_bps: u16,
}

#[near(serializers = [borsh])]
#[derive(Clone, Debug)]
pub struct Bounty {
    pub owner: AccountId,
    pub judge: AccountId,
    pub fee_account: AccountId,
    pub reward: u128,
    pub base_bond: u128,
    pub fee_bps: u16,
    pub scout_share_max_bps: u16,
    pub deadline_ns: u64,
    pub rule_timeout_ns: u64,
    pub created_ns: u64,
    pub closed: Option<CloseReason>,
    /// Money that can pay rewards and fees, or go back to the owner.
    pub balance: u128,
    /// Bonds of claims that are not decided yet. Not part of `balance`.
    pub bonds_held: u128,
    pub open_claims: u32,
    pub claim_count: u32,
    pub funded_total: u128,
    pub rewards_total: u128,
    pub fees_total: u128,
    pub forfeited_total: u128,
    pub withdrawn_total: u128,
    /// Account that paid the NEAR for storage; unused storage NEAR goes back to it.
    pub storage_payer: AccountId,
    /// yoctoNEAR left to pay the storage of new claims.
    pub storage_pool: u128,
}

impl Bounty {
    /// The fee for one proven claim, rounded down (RULES.md "Fees and shares").
    pub fn fee(&self) -> u128 {
        self.reward * u128::from(self.fee_bps) / BPS
    }

    /// One reward and its fee: what a proven claim takes from the balance.
    pub fn claim_cost(&self) -> u128 {
        self.reward + self.fee()
    }

    /// The trusted tier pays half the base bond, rounded up (RULES.md "Bonds and reputation").
    pub fn min_bond(&self) -> u128 {
        self.base_bond.div_ceil(2)
    }

    /// The very_poor tier pays three times the base bond.
    pub fn max_bond(&self) -> u128 {
        self.base_bond * crate::MAX_BOND_MULTIPLIER
    }
}

#[near(serializers = [borsh])]
#[derive(Clone, Debug)]
pub struct Claim {
    pub bounty_id: String,
    pub hunter: AccountId,
    pub bond: u128,
    /// Named by the hunter in the signed bond message, never by the judge.
    pub scout: Option<Scout>,
    pub bonded_ns: u64,
    pub outcome: Option<Outcome>,
    /// sha256 of the judge's evidence bundle.
    pub evidence_hash: Option<[u8; 32]>,
    pub decided_ns: u64,
}

/// The `msg` of an mt_transfer_call or of a `transfer` intent to the escrow.
#[near(serializers = [json])]
#[derive(Debug)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub enum DepositMsg {
    Fund(String),
    Bond(BondMsg),
}

#[near(serializers = [json])]
#[derive(Debug)]
#[serde(deny_unknown_fields)]
pub struct BondMsg {
    pub bounty_id: String,
    pub claim_id: String,
    #[serde(default)]
    pub scout: Option<Scout>,
}

#[near(serializers = [json])]
#[derive(Debug)]
pub struct BountyView {
    pub bounty_id: String,
    pub owner: AccountId,
    pub judge: AccountId,
    pub fee_account: AccountId,
    pub reward: U128,
    pub fee: U128,
    pub fee_bps: u16,
    pub base_bond: U128,
    pub min_bond: U128,
    pub max_bond: U128,
    pub scout_share_max_bps: u16,
    pub deadline_ns: U64,
    pub rule_timeout_ns: U64,
    pub created_ns: U64,
    pub status: String,
    pub close_reason: Option<CloseReason>,
    pub balance: U128,
    pub bonds_held: U128,
    pub open_claims: u32,
    pub claim_count: u32,
    pub claims_left: U64,
    pub funded_total: U128,
    pub rewards_total: U128,
    pub fees_total: U128,
    pub forfeited_total: U128,
    pub withdrawn_total: U128,
    pub storage_payer: AccountId,
    pub storage_pool: U128,
}

#[near(serializers = [json])]
#[derive(Debug)]
pub struct ClaimView {
    pub claim_id: String,
    pub bounty_id: String,
    pub hunter: AccountId,
    pub bond: U128,
    pub scout: Option<Scout>,
    pub bonded_ns: U64,
    pub reclaim_after_ns: U64,
    pub outcome: Option<Outcome>,
    pub evidence_hash: Option<String>,
    pub decided_ns: Option<U64>,
}

#[near(serializers = [json])]
#[derive(Debug, Default)]
pub struct RuleView {
    pub claim_id: String,
    pub outcome: Option<Outcome>,
    pub hunter_amount: U128,
    pub scout_amount: U128,
    pub fee_amount: U128,
    pub forfeited: U128,
}

#[near(serializers = [json])]
#[derive(Debug)]
pub struct ConfigView {
    pub intents_contract: AccountId,
    pub token_id: String,
    pub max_fee_bps: u16,
    pub max_scout_share_bps: u16,
    pub max_bond_multiplier: U128,
    pub min_rule_timeout_ns: U64,
    pub max_rule_timeout_ns: U64,
    pub claim_storage_cost: U128,
    /// Sum of all bounty balances, held bonds and owed payouts. The escrow's
    /// mt_balance_of on the intents contract is never below this.
    pub liabilities: U128,
}
