// NEAR wallet access for funding: NEP-413 signMessage and one ft_transfer_call.
// near-connect loads wallets (HOT, Meteor, MyNearWallet, Intear, ...) in sandboxed iframes.
import type { NearConnector, NearWalletBase } from "@hot-labs/near-connect";

const RPC_URLS = [
  "https://free.rpc.fastnear.com",
  "https://rpc.mainnet.near.org",
];
const TGAS = BigInt(10) ** BigInt(12);

let connector: Promise<NearConnector> | null = null;

function getConnector(): Promise<NearConnector> {
  connector ??= import("@hot-labs/near-connect").then(
    // Only wallets that can sign NEP-413 messages can fund a hunt.
    (m) =>
      new m.NearConnector({
        network: "mainnet",
        features: { signMessage: true, signAndSendTransaction: true },
      }),
  );
  return connector;
}

export type WalletAccount = { accountId: string; wallet: NearWalletBase };

async function firstAccount(wallet: NearWalletBase): Promise<WalletAccount> {
  const accounts = await wallet.getAccounts({ network: "mainnet" });
  if (!accounts.length) throw new Error("The wallet has no account.");
  return { accountId: accounts[0].accountId, wallet };
}

/** The account of a wallet connected earlier in this browser, or null. */
export async function connectedAccount(): Promise<WalletAccount | null> {
  try {
    const { wallet } = await (await getConnector()).getConnectedWallet();
    return await firstAccount(wallet);
  } catch {
    return null;
  }
}

export async function connectWallet(): Promise<WalletAccount> {
  return firstAccount(await (await getConnector()).connect());
}

export async function disconnectWallet(): Promise<void> {
  await (await getConnector()).disconnect();
}

async function view<T>(contract: string, method: string, args: object) {
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: "arsune",
    method: "query",
    params: {
      request_type: "call_function",
      finality: "final",
      account_id: contract,
      method_name: method,
      args_base64: btoa(JSON.stringify(args)),
    },
  });
  let last: unknown = null;
  for (const url of RPC_URLS) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
      });
      const json = await r.json();
      if (json.error || json.result?.error)
        throw new Error(JSON.stringify(json.error ?? json.result.error));
      const text = new TextDecoder().decode(new Uint8Array(json.result.result));
      return JSON.parse(text) as T;
    } catch (e) {
      last = e;
    }
  }
  throw last ?? new Error("The NEAR RPC is unavailable.");
}

/** "nep141:usdc.near" -> "usdc.near": the fungible token contract. */
export function ftContract(tokenId: string): string {
  return tokenId.replace(/^nep141:/, "");
}

/** The account's balance of tokenId inside the NEAR Intents contract, in token units. */
export async function intentsBalance(
  intents: string,
  accountId: string,
  tokenId: string,
): Promise<bigint> {
  const raw = await view<string>(intents, "mt_balance_of", {
    account_id: accountId,
    token_id: tokenId,
  });
  return BigInt(raw);
}

/** The account's balance of the token in its wallet (outside NEAR Intents). */
export async function walletBalance(
  accountId: string,
  tokenId: string,
): Promise<bigint> {
  const raw = await view<string>(ftContract(tokenId), "ft_balance_of", {
    account_id: accountId,
  });
  return BigInt(raw);
}

/** Move `units` of the token from the wallet into the signer's own NEAR Intents balance. */
export async function depositToIntents(
  account: WalletAccount,
  intents: string,
  tokenId: string,
  units: bigint,
): Promise<void> {
  // intents.near is registered on the USDC contract, so no storage_deposit is needed.
  // An empty msg credits the deposit to the sender inside NEAR Intents.
  await account.wallet.signAndSendTransaction({
    network: "mainnet",
    signerId: account.accountId,
    receiverId: ftContract(tokenId),
    actions: [
      {
        type: "FunctionCall",
        params: {
          methodName: "ft_transfer_call",
          args: { receiver_id: intents, amount: units.toString(), msg: "" },
          gas: (BigInt(100) * TGAS).toString(),
          deposit: "1",
        },
      },
    ],
  });
}

export type PreparedTransfer = {
  message: string;
  nonce: string; // base64 of 32 bytes
  recipient: string;
  memo: string;
  deadline: string;
};

/** Ask the wallet to sign the prepared NEP-413 message. Return the signed_intent for /fund. */
export async function signTransfer(
  account: WalletAccount,
  prepared: PreparedTransfer,
) {
  const nonce = Uint8Array.from(atob(prepared.nonce), (c) => c.charCodeAt(0));
  const signed = await account.wallet.signMessage({
    message: prepared.message,
    recipient: prepared.recipient,
    nonce,
    network: "mainnet",
    signerId: account.accountId,
  });
  return {
    standard: "nep413",
    payload: {
      message: prepared.message,
      nonce: prepared.nonce,
      recipient: prepared.recipient,
    },
    public_key: ed25519Text(signed.publicKey, 32),
    signature: ed25519Text(signed.signature, 64),
  };
}

// Arsune wants "ed25519:<base58>". Wallets return the public key in that form and the
// NEP-413 signature as base64 (some as "ed25519:<base58>" already).
export function ed25519Text(value: string, size: number): string {
  if (value.startsWith("ed25519:")) return value;
  const bytes = Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
  if (bytes.length !== size)
    throw new Error(
      `The wallet returned a ${bytes.length}-byte key or signature.`,
    );
  return `ed25519:${base58(bytes)}`;
}

const ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

export function base58(bytes: Uint8Array): string {
  let n = BigInt(0);
  for (const b of bytes) n = n * BigInt(256) + BigInt(b);
  let out = "";
  while (n > BigInt(0)) {
    out = ALPHABET[Number(n % BigInt(58))] + out;
    n /= BigInt(58);
  }
  for (const b of bytes) {
    if (b !== 0) break;
    out = "1" + out;
  }
  return out;
}
