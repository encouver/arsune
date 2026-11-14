# Deploy Arsune to arsune.augevi.com

Public site: https://arsune.augevi.com (real USDC on NEAR mainnet, `near_intents` settlement).

## What runs where

One shared server: `augevi@api.staging.personaizit.com` (159.195.12.68). Traefik owns ports 80 and 443,
gets the TLS certificate, and sends `https://arsune.augevi.com` to the `ui` container (port 3540).
Arsune lives in `~/arsune` on the server and runs as the Compose project `arsune`:
`ui`, `api`, `worker`, `coordinator`, `demo`, `swarm`, `postgres`, `redis`. No service publishes a host port.
Only `ui` joins the `traefik-public` network. Everything else talks over the private project network.

Settings come from `compose.yaml` plus `compose.prod.yaml`. The containers never read `.env`.
Docker Compose reads `~/arsune/.env` on the server for the `${VAR}` values in `compose.prod.yaml`.

## Real money (once)

1. In `~/arsune/.env` on the server (never in the repo), set the relayer account:
   `ARSUNE_NEAR_ACCOUNT_ID` and `ARSUNE_NEAR_PRIVATE_KEY`. Optional: `ARSUNE_NEAR_RPC_URL`,
   `ARSUNE_MAX_PAYMENT_AMOUNT`, `ARSUNE_ONECLICK_JWT`. `ARSUNE_SETTLEMENT_MODE` defaults to
   `near_intents` here; set it to `dry` to go back to simulation. Only `api`, `worker` and
   `coordinator` get these values.
2. Set `ARSUNE_OWNER_SESSION_SECRET` in `~/arsune/.env` to a long random value
   (`openssl rand -hex 32`). It signs the 7-day sessions of "Sign in with NEAR". Without it,
   the API makes a random secret at start, so every restart signs all owners out.
3. Copy the swarm accounts file (written by `scripts/near_swarm_accounts.py`) to
   `~/arsune/.data/swarm-accounts.json`, mode 600. The `swarm` container mounts it read-only.
   Without the file, Compose refuses to start `swarm`.

## DNS (once)

Create an A record `arsune` -> `159.195.12.68` in Cloudflare for `augevi.com`. Set it to "DNS only" (not proxied), so Traefik sees client IPs and gets the Let's Encrypt certificate.
Wait until it resolves. Traefik gets the certificate on the first HTTPS request.

## Deploy

From the repo root on your machine:

    scripts/deploy.sh --dry-run   # shows how many files would be sent; no server contact
    scripts/deploy.sh             # rsync, build and start on the server, then check the site

The script never copies `.env`. It ends with the HTTP status of `https://arsune.augevi.com/` (expect 200).

## Logs, stop, restart

    ssh augevi@api.staging.personaizit.com
    cd ~/arsune
    docker compose -p arsune -f compose.yaml -f compose.prod.yaml logs -f --tail 100 swarm api worker
    docker compose -p arsune -f compose.yaml -f compose.prod.yaml ps
    docker compose -p arsune -f compose.yaml -f compose.prod.yaml stop      # stop, keep data
    docker compose -p arsune -f compose.yaml -f compose.prod.yaml down      # remove containers, keep data
    # never add -v unless you want to delete the database and the evidence

## Back up the database

    docker compose -p arsune -f compose.yaml -f compose.prod.yaml exec -T postgres \
      pg_dump -U arsune arsune | gzip > arsune-$(date +%F).sql.gz

Evidence files are in the `evidence` volume (`arsune_evidence`).

## Limits

- Real money: settlement is `near_intents`. Owners fund hunts with USDC (signed deposit, or 1Click
  from another chain). One signed funding transfer is at most 1 USDC (`ARSUNE_MAX_PAYMENT_AMOUNT`).
- A live race has one agent per hunter and scout account in the accounts file (owner,
  hunter-1..4, scout-1: 4 hunters and 1 scout; with a model, hunter-4 is the AI hunter). The host
  refuses a bounty whose reward plus bond is above 1 USDC.
- With `ARSUNE_SETTLEMENT_MODE=dry`, a race has 15 agents (12 hunters + 3 scouts).
- One race at a time. No race starts by itself; a visitor starts it from the UI.
- Claim checks run one at a time (worker concurrency 1).
- Rate limits per client IP at Traefik: 20 requests/second (burst 60) for the whole site;
  1 request/second (burst 5) for `POST /api/bounties` and `POST /swarm-api/races`.
- Memory limits (about 2.7 GB in total, about 1 GB in use during a race): postgres 256m, redis 64m,
  api 384m, worker 1g, coordinator 192m, demo 128m, ui 256m, swarm 384m, migrate 256m (short job).
- The API accepts browser requests only from `https://arsune.augevi.com` (`ARSUNE_UI_ORIGIN`).
