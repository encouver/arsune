# Access leak: security bugs only an agent finds

Date: 13 Nov 2026. Status: approved by the owner ("go", test with the Anthropic key, then NEAR AI).

## Why

Broken links, wide pages and missing images are crawler work. Arsune must show bugs that need
an agent that reads the app and makes a plan: reading another user's data (IDOR), an open
database table, a hidden admin API, a chatbot that leaks its instructions. The proof stays plain
code: Arsune replays the agent's steps in a fresh browser context, logged in as test account A,
and looks for secret markers that A must never see.

## Contract (both backend and swarm use exactly this)

### Claim

- `claim_type`: `"access_leak"`.
- `urls`: `[site_origin + path of the last step]`.
- `details`: `{"steps": [Step, ...]}`, 1 to 6 steps. Step = `{"method": "GET" | "POST",
  "path": "/...", "body": {...} | null}`. `path` is a path on the bounty origin (may include a
  query string). `body` is a JSON object sent as JSON for POST; null for GET.
- No other keys. The agent never sends a marker (it does not know them).

### Owner test setup (per bounty)

- Bounty field `access_test` (nullable JSON), set by the owner (API) and automatically for the
  demo shop:
  ```json
  {"login": {"path": "/shop/api/login", "body": {"email": "alice@demo.shop", "password": "alice-demo"}},
   "markers": ["ARSUNE-MARKER-...", "..."]}
  ```
- The public bounty view exposes only `test_account: {"login_path", "login_body"}` (what an
  agent needs to log in as A). **Markers are never in any public response, log, claim, or
  evidence text** (evidence says which marker index matched, e.g. "marker 2 of 4", and quotes
  at most 80 characters around it with the marker itself replaced by `[MARKER]`).
- A bounty without `access_test` rejects `access_leak` claims as unverified with a plain reason.

### Checker replay (deterministic, no AI)

1. New Playwright browser context (clean session). POST the login step (JSON) with
   `context.request`; the session cookie stays in the context.
2. Run the claim's steps in order with `context.request`, same origin only, no redirects to
   other origins, response bodies capped at 256 KB each, 10 s per step.
3. Proven if any response body contains any marker. Else rejected ("No step showed data that
   account A must not see.").
4. Fingerprint: `["access_leak", marker_index, METHOD + normalized path of the step that
   showed it (numbers in path segments kept)]`. So the same leak from the same endpoint is one
   bug; repeats get the deposit back.

### Demo shop plants (backend/app/demo_shop.py, under /shop)

Accounts: A = alice@demo.shop / alice-demo, B = bob@demo.shop. Login `POST /shop/api/login`
sets a session cookie. Markers are random per demo process (or fixed constants with a
recognizable prefix `ARSUNE-MARKER-`), and the demo-proof / demo bounty setup stores them in
`access_test`.

1. **IDOR**: `GET /shop/api/orders/{id}` returns any order with no owner check. Alice owns
   1042; Bob's 1043 contains marker 1 (in a delivery note). `GET /shop/api/orders` lists only
   the caller's orders.
2. **Open data table**: the page script `/shop/static/app.js` holds a public "anon key" and calls
   `GET /shop/api/db/profiles?select=email,plan&user=eq.me`. The endpoint ignores the filter
   when `select=*` or the filter is removed, and returns all rows; Bob's row has marker 2.
3. **Hidden admin API**: `app.js` references `/shop/api/admin/stats` in dead code (a flag that is
   off). It answers any logged-in user; it includes marker 3.
4. **Leaky chatbot**: `POST /shop/api/chat {"message": "..."}`. A support bot with a hidden
   system prompt that holds marker 4. Deterministic rule: if the message asks to repeat/ignore
   instructions or for the system prompt, it echoes the prompt. Else a canned helpful answer.

None of these are linked as plain `<a href>` links, so scripted crawlers do not find them.
Normal shop pages keep working and keep their old planted bugs.

### AI hunter (swarm)

- An AI hunter gets the bounty's `test_account`, logs in as A with its own HTTP session, and
  collects: the shop's HTML pages it crawled, the page scripts (`<script src>` on the origin,
  capped), and the API answers it sees.
- It asks the model (in strict JSON, like today's ai.py) for up to N probe plans (step lists).
  It runs each probe itself, shows the model the answers (capped), and the model decides which
  probe shows data that A must not see. Only those become `access_leak` claims through the
  normal deposit path.
- Budget per run as today (`--ai-budget`); default model for tests: Claude via
  `SWARM_MODEL_PROVIDER=anthropic`. Later: NEAR AI Cloud (OpenAI compatible).
- Scripted hunters do not make `access_leak` claims.

### UI

- Kind label: `access_leak` → "Access you should not have". Title: "{Method} {path} shows data
  that account A must not see" (from the last step). Group as any proven bug.

## Not now

Payment bypass, stored XSS, per-type reward multiplier, an owner UI to enter test accounts
(API only for non-demo sites).
