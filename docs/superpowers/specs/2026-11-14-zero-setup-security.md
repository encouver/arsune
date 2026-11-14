# Zero-setup security reports

Date: 14 Nov 2026. Owner decision: no owner setup at all (no test user, no markers). The owner
gives only the site URL. Agents find; plain code proves. Replaces the required security-test step
of 2026-11-14-access-leak.md / browser-session.md: `access_leak` stays in the code but is no longer
required or shown in the owner flow (verify must NOT require the security test any more; remove the
"Set up the security test" step from the owner page; demo bounties keep working).

## Scope
The proved site's registrable domain and its subdomains (`scope_base`/`in_scope` in
backend/app/browser/session.py), plus known backend-as-a-service hosts that the site's own
scripts name: `*.supabase.co`, `*.firebaseio.com`, `firestore.googleapis.com`. Private IPs blocked
except the demo origin (existing policy). No login. Requests are GET, or POST only when the site's
own scripts make that call. No writes, no deletes.

## New claim types (details schema is exact)

### 1. `data_exposure` — personal data open to anyone
- details: `{"steps": [{"method": "GET"|"POST", "url": "<absolute>", "headers": {name: value} | null,
  "body": obj | null}]}`, 1–4 steps. `headers` may only carry `apikey`, `Authorization`,
  `Accept`, `Content-Type`, `Prefer`, `Range` (agents need the public anon key the site ships).
- urls: `[last step url without query]`.
- Check: new browser context, NOT logged in. Run steps with `context.request` (caps: 10 s, 512 KB
  each, no out-of-scope redirects). Collect personal data from all response bodies: emails
  (regex), phone numbers (E.164-ish, ≥ 9 digits), and street-address-like fields only when JSON
  keys say so (`address`, `street`, `phone`, `email`, `dob`, `birth`). Remove values that appear on
  the site's public HTML pages (fetch the bounty origin home page and the step URL's origin home
  page; also drop the site's own domain emails like `info@<base>`). Proven when ≥ 3 distinct people
  remain (distinct emails, else distinct phones). Evidence: counts + up to 3 masked samples
  (`b***@gmail.com`, `+34*****12`). Never store full personal data in evidence or logs.
- Fingerprint: `["data_exposure", host, normalized path without query]`.

### 2. `secret_key` — powerful secret in public code
- details: `{"url": "<absolute script or page URL>", "kind": "supabase_service_role" |
  "stripe_secret" | "aws_secret" | "private_key" | "openai_key" | "anthropic_key"}`.
- Check: fetch the URL anonymously (in scope, ≤ 2 MB). Proven when the body has a value of that
  kind, verified offline only (never call the provider):
  - supabase_service_role: a JWT whose payload decodes to `"role":"service_role"`.
  - stripe_secret: `sk_live_[0-9A-Za-z]{20,}`; aws_secret: `AKIA[0-9A-Z]{16}` near a 40-char
    secret; private_key: `-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----`; openai_key:
    `sk-(proj-)?[A-Za-z0-9_-]{20,}`; anthropic_key: `sk-ant-[A-Za-z0-9_-]{20,}`.
- Evidence: kind, URL, masked value (first 6 + last 4 chars). Never the full value.
- Fingerprint: `["secret_key", kind, sha256(value)[:16]]`.

### 3. `private_file` — private file open to anyone
- details: `{"url": "<absolute>", "kind": "env" | "git" | "backup" | "source_map"}`.
- Check: anonymous GET (in scope, ≤ 1 MB). Proven by format: env = ≥ 3 lines `^[A-Z][A-Z0-9_]*=`
  and not HTML; git = `[core]` section (for /.git/config) or `ref: refs/` (HEAD);
  backup = SQL dump (`CREATE TABLE`/`INSERT INTO`) or zip/gzip magic bytes with a backup-like name;
  source_map = JSON with `"version":3` and `"sourcesContent"` that is non-empty.
- Evidence: kind, URL, size, first matching line names only (no values).
- Fingerprint: `["private_file", kind, normalized path]`.

## Demo shop plants (backend/app/demo_shop.py)
- An anonymous "Supabase-like" REST endpoint: `/shop/rest/v1/customers` with header `apikey`
  = the anon key shipped in `/shop/static/app.js`. The app's own call is
  `GET /shop/rest/v1/customers?select=first_name&id=eq.<visitor>` (returns nothing sensitive);
  `select=*` without the id filter returns 25 fake customers with emails/phones/addresses
  (clearly fake: example.com-like domains are NOT ok because of the public-page filter? use
  realistic-looking fake domains like `mailbox.test`? — use plausible fake addresses such as
  `maria.lopez@fastmail.example`; the check must count them).
- A JS chunk `/shop/static/chunk-admin.js` that is referenced from app.js and holds a
  `service_role` JWT for the fake project (signed with a random secret; only the payload matters).
- `/shop/.env.backup` that returns a fake env file.
Normal pages keep working; existing planted bugs stay.

## AI hunter (swarm)
Anonymous mode (no login). Crawl pages, collect same-scope scripts (and the BaaS host + anon key
they name), record the app's own API calls (page.on("request")). The model plans probes: queries
the app never makes (remove filters, `select=*`, neighbour ids, list endpoints), candidate private
files (`/.env`, `/.env.backup`, `/.git/config`, `*.map` of the scripts it saw, `/backup.sql`),
and secrets it sees in scripts. The hunter runs probes itself (same caps), and claims only what
its own run shows (≥ 3 people for data_exposure; the matching pattern for the others). One
`--ai-budget` as today. The swarm and the checker share the check functions so an agent never
claims something the checker would reject for format reasons.

## UI
Kinds: `data_exposure` "Personal data open to anyone"; `secret_key` "Secret key in public
code"; `private_file` "Private file open to anyone". All three plus `access_leak` count as
Security in "Bugs found". Titles: "{N} people's personal data at {host}{path}",
"{Kind label} in {path}", "{file kind} file at {path}".
