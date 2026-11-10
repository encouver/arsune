#!/usr/bin/env bash
# Unit tests, wasm build, then sandbox tests against the real intents.near wasm. All Rust
# runs in Docker; nothing is installed on the host. The wasm is built by build.sh in the
# pinned cargo-near image. Tests run in a local image made from it that adds OpenSSL headers
# (near-workspaces needs them), clippy and rustfmt.
#
#   contracts/escrow/test.sh             # everything
#   contracts/escrow/test.sh unit        # unit tests only
#
# Fixtures (checked by hash, kept in target/fixtures):
#   - intents.near code, read from mainnet with the read-only RPC `view_code` and checked
#     against the code hash of defuse 0.4.5 (rev 16d04f6e, reproducible build). If mainnet
#     upgrades intents.near the hash check fails: build defuse at that rev instead.
#   - fungible-token.wasm from near/intents at the same rev (the test USDC).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE="sourcescan/cargo-near:0.22.0-rust-1.97.1@sha256:7467038bdddc86484b73b416eeadce926ff59013e128e53dec5a19e1cb4b2234"
CACHE=${ESCROW_CACHE_DIR:-${TMPDIR:-/tmp}/arsune-escrow-cache}
RPC=${NEAR_MAINNET_RPC:-https://free.rpc.fastnear.com}
DEFUSE_CODE_HASH=BtA1BEFNS619KkvXFsgcpQ21Tb5yMm32aaUkn7xNothk
DEFUSE_SHA256=a1afab98916d9f213746544dcf8e4e6bdb22c0be6bc5dfbd8ea8814c9922b547
FT_URL=https://raw.githubusercontent.com/near/intents/16d04f6ec4401263f8914638710260da50bb46b0/releases/fungible-token.wasm
FT_SHA256=12c5f9bd9136b4396823478281a61368b4c74cdaf35ab5743b302c3aac55a354
FIXTURES="$HERE/target/fixtures"
TEST_IMAGE=arsune-escrow-test:local
mkdir -p "$CACHE/registry" "$CACHE/git" "$CACHE/unit" "$CACHE/sandbox" "$CACHE/near" "$FIXTURES"

in_docker() {
  local crate=$1 target=$2
  shift 2
  docker run --rm \
    -v "$HERE:/home/near/code" \
    -v "$CACHE/registry:/home/near/.cargo/registry" \
    -v "$CACHE/git:/home/near/.cargo/git" \
    -v "$CACHE/$target:/home/near/target" \
    -v "$CACHE/near:/home/near/.near" \
    -e CARGO_TARGET_DIR=/home/near/target \
    -e ESCROW_WASM=/home/near/code/target/near/arsune_escrow.wasm \
    -e DEFUSE_WASM=/home/near/code/target/fixtures/defuse-0.4.5.wasm \
    -e FT_WASM=/home/near/code/target/fixtures/fungible-token.wasm \
    -w "/home/near/code/$crate" \
    "$TEST_IMAGE" "$@"
}

docker build -q -t "$TEST_IMAGE" - >/dev/null <<EOF
FROM $IMAGE
USER root
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
USER near
RUN rustup component add clippy rustfmt
EOF

check_sha() {
  echo "$2  $1" | sha256sum --check --quiet
}

fetch_fixtures() {
  local defuse="$FIXTURES/defuse-0.4.5.wasm" ft="$FIXTURES/fungible-token.wasm"
  if ! [ -f "$defuse" ] || ! check_sha "$defuse" "$DEFUSE_SHA256"; then
    python3 - "$RPC" "$DEFUSE_CODE_HASH" "$defuse" <<'EOF'
import base64, json, sys, urllib.request
rpc, code_hash, path = sys.argv[1:]
body = json.dumps({"jsonrpc": "2.0", "id": "arsune", "method": "query", "params": {
    "request_type": "view_code", "finality": "final", "account_id": "intents.near"}}).encode()
request = urllib.request.Request(rpc, body, {"Content-Type": "application/json"})
result = json.loads(urllib.request.urlopen(request, timeout=60).read())["result"]
if result["hash"] != code_hash:
    sys.exit(f"intents.near now runs code {result['hash']}, not {code_hash}: build defuse 0.4.5")
open(path, "wb").write(base64.b64decode(result["code_base64"]))
EOF
    check_sha "$defuse" "$DEFUSE_SHA256"
  fi
  if ! [ -f "$ft" ] || ! check_sha "$ft" "$FT_SHA256"; then
    curl -sSfL -o "$ft" "$FT_URL"
    check_sha "$ft" "$FT_SHA256"
  fi
}

in_docker . unit bash -c "cargo fmt --check && cargo clippy --locked --all-targets -- -D warnings && cargo test --locked --lib"
[ "${1:-all}" = unit ] && exit 0
ESCROW_CACHE_DIR="$CACHE" "$HERE/build.sh"
fetch_fixtures
in_docker integration-tests sandbox bash -c "cargo fmt --check && cargo clippy --locked --all-targets -- -D warnings && cargo test --locked -- --nocapture --test-threads 4"
