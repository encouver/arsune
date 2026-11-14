# Browser session for access tests (any site)

Date: 14 Nov 2026. Replaces the "JSON login + raw HTTP on one origin" part of
2026-11-14-access-leak.md. Markers, claim type `access_leak`, fingerprints, evidence redaction stay.

## Problem

Real sites use Clerk/NextAuth/Supabase/Firebase/custom forms, several hosts (www, app., api.),
and tokens in headers or local storage. One JSON login POST on one origin covers almost none.

## Design

### Scope
Allowed hosts = the proved bounty host's registrable domain and all its subdomains
(e.g. proved `staging.personaizit.com` → `*.personaizit.com` and `personaizit.com`). Use a simple
rule: host == base or host endswith "." + base, where base = last 2 labels of the proved host
(last 3 if the 2nd-level label is a short public suffix part like co.uk/com.br; a small list is
fine). Private IPs stay blocked except the demo origin (existing policy).

### Owner test account (replaces access_test.login)
`access_test = {"account": {"login_url": "https://app.x.com/sign-in", "email": "...",
"password": "..."} | null, "markers": [...]}`. PUT /api/bounties/{id}/access-test takes
`{login_url, email, password}` (owner key). Public `test_account` = `{"login_url", "email",
"password"}` (agents need it to log in as A; it is a TEST account). Keep backward compatibility
for the demo shop by giving it login_url `<demo>/shop/login` (add a tiny HTML login form page to the
demo shop that posts to the existing /shop/api/login and redirects to /shop/account).

### Session capture: `backend/app/browser/session.py` (shared by checker and swarm)
`async def login_session(browser, login_url, email, password, allowed) -> Session`
1. New context; goto login_url; wait for network idle (cap 15 s).
2. Fill the first visible email/username input (type=email, name/id/autocomplete contains
   email|user|login), press Continue/Next if no visible password input, then fill the visible
   password input; submit (Enter, or the button with type=submit / text Sign in|Log in|Continue|
   Iniciar sesión|Entrar). Handle Clerk-like two-step forms.
3. Wait until the URL leaves the login page or 15 s; then visit the post-login page and wait for
   network idle. While doing so, record request headers to allowed hosts: keep the latest
   `Authorization` header per host.
4. Session = `{"storage_state": context.storage_state(), "auth_headers": {host: value},
   "home_url": page.url}`. Raise `LoginFailed(reason)` with a plain reason when no password field
   is found, or still on the login page after submit.

### Steps (claim details) — unchanged shape, wider scope
`{"steps": [{"method": "GET"|"POST", "url": "https://any-allowed-host/path?q", "body": obj|null}]}`
Note: `url` (absolute) replaces `path`. Accept `path` too (resolve against the bounty origin) so
the demo and old claims keep working. Every url must be in scope.

### Replay (checker)
New context with `storage_state`; open `home_url` (so the app's JS refreshes tokens); for each
step use `context.request` with the cookies from the context plus the captured `Authorization`
header for that host if any (re-capture by listening to the page's own requests during the
home_url load; prefer the fresh one). Same caps as before (256 KB, 10 s per step, no cross-scope
redirects). Proven when a marker appears. Login failure → unverified with the reason.

### Hunter (swarm)
Same flow: `login_session` once per AI hunter, then crawl pages in that logged-in context
(collect HTML, same-scope scripts, and the URLs + methods of the API calls the app itself makes —
this is the best map of the API), give the model that map, run probes with the replay function,
claim per contract. The swarm and the checker import the same session + replay code.

## Out of scope
OAuth-only, MFA, CAPTCHA (an upload of a Playwright storage_state file can come later).
