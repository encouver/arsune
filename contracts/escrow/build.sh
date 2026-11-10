#!/usr/bin/env bash
# Build the escrow wasm in Docker with the pinned cargo-near image: the same image (by digest)
# and the same `cargo near build non-reproducible-wasm --locked` command that built the
# intents.near Verifier on mainnet. Source, Cargo.lock, image and in-container paths are fixed,
# so two builds give the same bytes. Prints the sha256 and the size.
#
#   contracts/escrow/build.sh            -> contracts/escrow/target/near/arsune_escrow.wasm
#
# ESCROW_CACHE_DIR keeps the cargo registry and build cache between runs (default
# ${TMPDIR:-/tmp}/arsune-escrow-cache). The image runs as its user `near` (uid 1000), so the
# cache and output directories must be writable by uid 1000.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE="sourcescan/cargo-near:0.22.0-rust-1.97.1@sha256:7467038bdddc86484b73b416eeadce926ff59013e128e53dec5a19e1cb4b2234"
CACHE=${ESCROW_CACHE_DIR:-${TMPDIR:-/tmp}/arsune-escrow-cache}
OUT="$HERE/target/near"
mkdir -p "$CACHE/registry" "$CACHE/git" "$CACHE/build" "$OUT"

docker run --rm \
  -v "$HERE:/home/near/code:ro" \
  -v "$CACHE/registry:/home/near/.cargo/registry" \
  -v "$CACHE/git:/home/near/.cargo/git" \
  -v "$CACHE/build:/home/near/target" \
  -v "$OUT:/home/near/out" \
  -e CARGO_TARGET_DIR=/home/near/target \
  -w /home/near/code \
  "$IMAGE" \
  cargo near build non-reproducible-wasm --locked --out-dir /home/near/out >&2

WASM="$OUT/arsune_escrow.wasm"
SHA=$(sha256sum "$WASM" | cut -d' ' -f1)
SIZE=$(stat -c %s "$WASM")
CODE_HASH=$(python3 -c '
import sys
alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
raw = bytes.fromhex(sys.argv[1])
n, out = int.from_bytes(raw, "big"), ""
while n:
    n, r = divmod(n, 58)
    out = alphabet[r] + out
print("1" * (len(raw) - len(raw.lstrip(b"\0"))) + out)' "$SHA" 2>/dev/null || echo "?")
echo "wasm:      $WASM"
echo "sha256:    $SHA"
echo "code_hash: $CODE_HASH (base58, as view_account shows it)"
echo "size:      $SIZE bytes ($((SIZE / 1024)) KiB)"
