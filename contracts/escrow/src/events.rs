use near_sdk::json_types::{U128, U64};
use near_sdk::{near, AccountId};

use crate::types::{CloseReason, Outcome, Scout};

/// NEP-297 events, logged as `EVENT_JSON:{"standard":"arsune-escrow","version":"1.0.0",...}`.
#[near(event_json(standard = "arsune-escrow"))]
pub enum Event {
    #[event_version("1.0.0")]
    Create {
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
    },
    #[event_version("1.0.0")]
    Fund {
        bounty_id: String,
        sender_id: AccountId,
        amount: U128,
        balance: U128,
    },
    #[event_version("1.0.0")]
    Bond {
        bounty_id: String,
        claim_id: String,
        hunter: AccountId,
        amount: U128,
        scout: Option<Scout>,
    },
    #[event_version("1.0.0")]
    Rule {
        bounty_id: String,
        claim_id: String,
        judge: AccountId,
        ruling: Outcome,
        outcome: Outcome,
        evidence_hash: String,
        hunter_amount: U128,
        scout_amount: U128,
        fee_amount: U128,
        forfeited: U128,
    },
    #[event_version("1.0.0")]
    Expire {
        bounty_id: String,
        claim_id: String,
        hunter: AccountId,
        bond: U128,
    },
    #[event_version("1.0.0")]
    Payout {
        receiver_id: AccountId,
        amount: U128,
        memo: String,
    },
    #[event_version("1.0.0")]
    PayoutFailed {
        receiver_id: AccountId,
        amount: U128,
        memo: String,
        owed: U128,
    },
    #[event_version("1.0.0")]
    Refund {
        sender_id: AccountId,
        token_ids: Vec<String>,
        amounts: Vec<U128>,
        reason: String,
    },
    #[event_version("1.0.0")]
    Close {
        bounty_id: String,
        reason: CloseReason,
    },
    #[event_version("1.0.0")]
    Withdraw {
        bounty_id: String,
        owner: AccountId,
        amount: U128,
        storage_refund: U128,
    },
}
