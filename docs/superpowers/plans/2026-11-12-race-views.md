# Race Views (Summary Line + Who Won) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stat tiles and dark agent grid on `/swarm` with a light summary line
(Find → Claim → Check → Paid / Bond lost / Repeat) and a "Who won" agent view, and show the
same line in the owner page's live race box.

**Architecture:** One pure module (`race-model.ts`) computes every number, sentence, row and
dot stage from the data the pages already load. Two client components (`RaceLine`, `WhoWon`)
render it; `RaceLine` takes plain props so `/swarm` and `/bounties/{id}` both use it. No
backend, API or race-host change.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, plain CSS, Playwright 1.64
(also used as the unit-test runner for the pure module: a spec without `page`).

**Spec:** `docs/superpowers/specs/2026-11-12-race-views-design.md` (approved 12 Nov 2026).
Mockups: `docs/superpowers/specs/2026-11-12-race-views/` (`summary-line-mockup.html` Light
version, `agent-views-mockup.html` option A). Read the spec first; this plan does not repeat it.

## Global Constraints

- No change under `backend/`, to the race host, or to the API. No change to `/checks`,
  `/incidents`, `/demo`, `/races` or the start page.
- Light theme only. Use `app/globals.css` tokens (`--paper`, `--ink`, `--muted`, `--line`,
  `--green`, `--serif`, `--white`).
- New colors, exactly: paid/earned `#2f7a3a`; bond lost/lost money/went broke `#ec7a5c`;
  repeat/not checked mark `#8a938d`; nothing yet `#c9cfca`; bond locked/working now gold
  `#c99a2e`. Coral always sits next to a text label.
- Big numbers use `var(--serif)`. Labels short and plain (ASD-STE100 style, as the site is now).
- Agents show by name (`a.id`, e.g. `hunter-045`); a 64-hex account never shows as text
  (use `isHexAccount` / the page's `names` map; hex → "an unnamed agent").
- Money: `usdc()` from `app/swarm/format.ts`; signs `+` and `−` (U+2212); zero has no sign.
- At most 12 moving dots (`DOT_CAP = 12`). No dot moves when the race is finished or with
  `prefers-reduced-motion: reduce` (then no `race-dot` element is rendered).
- Keep `data-testid` values `live-race` and `live-stream`. Other test ids may change; update
  every spec that uses a removed one.
- New CSS classes use the prefix `rv-` in a new file `app/race-views.css`, imported in
  `app/layout.tsx` next to `race.css`.
- Do not start AI agents in any check (no `?agents=ai`). No paid model call.
- Commit after each task with a conventional message. No `[skip ci]`.

## Review Focus

1. **The page switches bounty** (`?bounty=` changes, or the race host moves to another
   bounty): the seen-claim set resets, so the new bounty's old claims make no dots.
   Test in Task 3.
2. **Hex accounts**: a 64-hex hunter or scout never shows in the "Latest" sentence, the
   tooltip, the `aria-label` or the agent filter bar. Tests in Task 1 and Task 4.
3. **Pool edge values**: money in = 0 → no bar; `budget_amount` larger than money in (or a
   bounty with only lost bonds) → bar clamps to 100%; `budget_amount` 0 → bar empty, text
   "0.00 of X USDC left". Tests in Task 1.
4. **Agent filter keyed by account**: the filter survives polls, an agent changing rows, and
   the fall back to `agentsFromHunters` (other ids). It uses `account`, not `id`. A filter
   with no matching claims shows "No claims match." and the "Show all" bar. Test in Task 4.
5. **110 agents at 390 px**: no horizontal scroll on the page, marks wrap, the line is
   vertical. Test in Task 6.

---

## File map

| File | Responsibility |
| --- | --- |
| `frontend/app/swarm/race-model.ts` (create) | Pure functions: counts, pool, stages, Latest sentence, agent rows, labels, filters, dot picking. No React, no DOM. |
| `frontend/app/swarm/race-line.tsx` (create) | `RaceLine` component: steps, result rows, rare line, Latest row, dots, screen-reader list. |
| `frontend/app/swarm/who-won.tsx` (create) | `WhoWon` component: four rows of agent marks, key, tooltip, click to pick. |
| `frontend/app/race-views.css` (create) | `rv-` styles for both components, desktop and ≤ 640 px. |
| `frontend/app/layout.tsx` | Import `./race-views.css`. |
| `frontend/app/swarm/types.ts` | Add `claim_id?: string \| null` to `LedgerRow`. |
| `frontend/app/swarm/page.tsx` | New order: header → pool bar → `RaceLine` → `WhoWon` → claims (+ agent filter) → "More details". Remove totals tiles, agent grid, `Legend`, `useFlashes`, agent detail. Move `resultOf` to `race-model.ts`. |
| `frontend/app/bounties/race-step.tsx` | `LiveRace` shows `RaceLine` in place of its `<dl>` stats. |
| `frontend/app/globals.css` | Remove now-unused `sw-totals`, `sw-stat*`, `sw-tile*`, `sw-mark*`, `sw-grid-panel`, `sw-results` rules (only if unused anywhere). |
| `frontend/tests/race-model.spec.ts` (create) | Unit tests of `race-model.ts` (no browser page). |
| `frontend/tests/swarm.spec.ts`, `tests/bounties.spec.ts`, `tests/race-live.spec.ts` | Update to the new layout. |
| `.github/workflows/ci.yml` | Add `tests/race-model.spec.ts` to the Playwright command. |

---

### Task 1: The pure race model

**Files:**
- Create: `frontend/app/swarm/race-model.ts`
- Create: `frontend/tests/race-model.spec.ts`
- Modify: `frontend/app/swarm/types.ts` (`LedgerRow.claim_id`)
- Modify: `.github/workflows/ci.yml:80` (add `tests/race-model.spec.ts`)

**Interfaces:**
- Consumes: `claimGroup`, `claimPage`, `isHexAccount`, `ClaimText` from `app/claim-text.ts`;
  `usdc` from `app/swarm/format.ts`; `Agent` from `app/swarm/types.ts`.
- Produces (exact):

```ts
export const DOT_CAP = 12;
export type LineClaim = ClaimText & {
  id: string;
  hunter_account: string;
  fingerprint?: string | null;
  scout?: { account: string } | null;
  created_at?: string;
  updated_at?: string;
};
export type LineLedgerRow = {
  kind: string; amount: number; account: string;
  status?: string; claim_id?: string | null;
};
export type LineCounts = {
  claims: number; checking: number; paid: number; lost: number; repeat: number;
  unchecked: number; unfunded: number; paidAmount: number;
};
export type Pool = { into: number; left: number; fraction: number | null };
export type Stage = "claim" | "check" | "paid" | "lost" | "repeat" | "other";
export type Result = "earned" | "lost" | "broke" | "none";
export type NameOf = (account: string) => string;

export function lineCounts(claims: LineClaim[], ledger: LineLedgerRow[]): LineCounts;
export function rareText(counts: LineCounts): string;            // "" when both are 0
export function poolOf(budgetAmount: number, ledger: LineLedgerRow[]): Pool;
export function stageOf(c: LineClaim): Stage;
export function latestSentence(
  claims: LineClaim[], ledger: LineLedgerRow[], nameOf: NameOf,
  titleOf: (c: LineClaim) => string,
): { claimId: string; text: string } | null;
export function resultOf(a: Agent): Result;                       // moved from page.tsx
export function net(a: Agent): number;                            // earned − bonds_lost
export function signedUsdc(units: number): string;               // "+2.14", "−0.30", "0.00"
export function agentRows(agents: Agent[]): Record<Result, Agent[]>;
export function agentLabel(a: Agent, fallback: boolean): string;
export function isWorking(a: Agent): boolean;                     // searching|negotiating|claiming|waiting
export function claimsOfAgent<T extends LineClaim>(claims: T[], account: string, role: "hunter" | "scout"): T[];
export function pickNewDots(
  seen: Set<string> | null, claims: LineClaim[], free: number,
): { seen: Set<string>; fresh: string[] };
```

Rules the tests pin (from the spec "Data" table and "Details settled" 1–3):
- `lineCounts`: `checking` = group `pending`; `paid` = group `proven` and status ≠ `unfunded`;
  `unfunded` = status `unfunded`; `lost` = group `false`; `repeat` = group `repeat`;
  `unchecked` = group `unchecked`; `paidAmount` = completed `reward_out` + completed
  `scout_share`. Each claim counts once.
- `rareText`: non-zero parts joined by " · ", then " · bond back":
  `"3 could not be checked · 2 proven, but the pool was empty · bond back"`;
  singular `"1 could not be checked"`; `""` when both are 0.
- `poolOf`: `into` = completed `fund` + completed `bond_forfeit`; `left` = `budgetAmount`;
  `fraction` = `null` if `into` ≤ 0, else `left / into` clamped to [0, 1].
- `stageOf`: `received` → `claim`; `checking`/`needs_review` → `check`; then by group:
  proven (not unfunded) → `paid`, false → `lost`, repeat → `repeat`, unchecked or
  `unfunded` → `other`.
- `latestSentence`: newest claim (by `updated_at ?? created_at`) whose group ≠ `pending`.
  Titles in straight double quotes, page from `claimPage`:
  - paid with a completed `reward_out` row for its `claim_id` (hunter's account):
    `hunter-045 proved "Too wide on mobile" on /shop/c/support → paid +2.14 USDC`; with a
    completed `scout_share` row for the claim, append ` · scout-02 +0.30`.
  - paid without that row: `… on /x → proven, payment pending`.
  - rejected: `hunter-007 claimed "Broken link to /a" on /b → it was not true, bond lost`
  - duplicate: `hunter-007 sent a bug that was already proven → bond back`
  - unverified: `hunter-007: Arsune could not check "Broken link to /a" → bond back`
  - unfunded: `hunter-007 proved "Broken link to /a", but the pool was empty → bond back`
- `agentRows`: `resultOf` rule unchanged (broke if state `broke`; else lost if
  `bonds_lost > earned`; else earned if `earned > 0`; else none). Each row sorted by
  `Math.abs(net(a))` descending, then `id` ascending.
- `agentLabel(a, false)`: `hunter-045 · mobile · trusted · +2.14 USDC`; tier `very_poor`
  shows `very poor`; AI strategies `ai`/`ai-scout` show `AI`; scouts have no tier part:
  `scout-02 · scout · +0.30 USDC`. `agentLabel(a, true)` (fallback agents): no strategy,
  tier part `tier not reported`: `hunter-01 · tier not reported · +2.14 USDC`.
  Net 0 shows `0.00 USDC`.
- `claimsOfAgent`: hunter → `hunter_account === account`; scout → `scout?.account === account`.
- `pickNewDots`: `seen === null` (first load) → `fresh = []`, `seen` = all ids. Else
  `fresh` = ids not in `seen`, oldest first by `created_at`, at most `free` (≤ 0 → none);
  returned `seen` = old ∪ all ids (claims past the cap are marked seen too).

- [ ] **Step 1: Write the failing tests** in `tests/race-model.spec.ts`, one `test()` per
  function above, with the exact strings above. Use plain objects; no `page` fixture.
  Include: Review Focus 2 (a 64-hex `hunter_account` with a `nameOf` that maps hex →
  "an unnamed agent" gives a sentence with no 64-hex run: `expect(text).not.toMatch(/[0-9a-f]{64}/)`),
  Review Focus 3 (`poolOf(0, [])` → `fraction null`; `poolOf(90e6, [fund 80e6])` → `1`;
  `poolOf(0, [fund 80e6])` → `0`; a `pending` fund row is not counted), and a `pickNewDots`
  case with 15 new claims and `free = 12` → 12 fresh, all 15 in `seen`.
- [ ] **Step 2: Run** `cd frontend && npx playwright test tests/race-model.spec.ts`
  Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `race-model.ts` with the signatures above; add `claim_id` to
  `LedgerRow` in `types.ts`; add `tests/race-model.spec.ts` to the CI Playwright command.
- [ ] **Step 4: Run** the same command and `npm run typecheck`. Expected: all PASS, no type errors.
- [ ] **Step 5: Commit** `feat(ui): add race model for the summary line and Who won`

### Task 2: `RaceLine` (static) and the new `/swarm` top

**Files:**
- Create: `frontend/app/swarm/race-line.tsx`, `frontend/app/race-views.css`
- Modify: `frontend/app/layout.tsx`, `frontend/app/swarm/page.tsx`, `frontend/tests/swarm.spec.ts`

**Interfaces:**
- Consumes: Task 1 (`lineCounts`, `rareText`, `latestSentence`, `poolOf`, types).
- Produces:

```ts
export type RaceLineProps = {
  claims: LineClaim[];
  ledger: LineLedgerRow[];
  agentCount: number | null;          // null → Find shows "Agents search the site" only
  nameOf: NameOf;
  titleOf: (c: LineClaim) => string;
  finished: boolean;                  // true → no dots
  seeAllHref: string;                 // "#claims" on /swarm; "/swarm?bounty={id}" on the owner page
};
export default function RaceLine(props: RaceLineProps): JSX.Element;
export function PoolBar({ pool }: { pool: Pool }): JSX.Element | null;  // null when pool.fraction === null
```

Test ids (exact): `race-line` (root), `line-agents` (agent count in Find, only when not null),
`line-claims`, `line-checking` (rendered only when > 0, text `{n} being checked`), `line-paid`,
`line-paid-amount` (`+{usdc} USDC`), `line-lost`, `line-repeat`, `line-rare` (only when
`rareText` ≠ ""), `line-latest` (`aria-live="polite"`), `line-see-all`, `pool-bar`
(`aria-label`/text `27.19 of 80.00 USDC left`). Count elements hold the bare number.
Copy (exact): steps `Find` / `Agents search the site` (with count: `{n} agents search the site`),
`Claim` / `{claims} claims · bond locked`, `Check` / `New? True? Fresh browser`; rows
`Paid`, `Bond lost` + `not true`, `Repeat` + `bond back`; link `See all {n} claims ↓`.
Screen-reader list: a visually hidden `<ul>` with the step and result counts in words.
At ≤ 640 px the line is vertical (steps down the page, the result rows under Check).
The pool bar width changes with a CSS transition (none with reduced motion).

`/swarm` page changes in this task: header text per spec ("Race running · {n} agents",
"Race finished", idle/offline texts unchanged; keep the dry pill and live badge; drop the
"Live swarm board" eyebrow and the `h1` sentence, keep the site name in serif italic);
`PoolBar` with "Bounty pool"; `RaceLine` with `seeAllHref="#claims"` and `id="claims"` on the
claim section; remove the `sw-totals` section. Keep the `ai-usage` block as one small line
under the pool bar (same test id and text). Keep every banner. Hide pool bar and line when
there is no bounty.

- [ ] **Step 1: Update `tests/swarm.spec.ts`** for the new top. Replace the `total-*`
  assertions in "dry mode labels money…" with: `line-claims`, `line-paid` = `1`,
  `line-paid-amount` contains the old hunters+scouts total, `line-lost` = `1`,
  `pool-bar` text `… of 50.00 USDC left` (match the fixture: money in = fund + bond_forfeit).
  Add a test "the line shows rare results in one small line" (fixture with one `unverified`
  and one `unfunded` claim → `line-rare` = `1 could not be checked · 1 proven, but the pool was empty · bond back`,
  and neither is in `line-paid`). Add a test "Latest names the newest decided claim"
  (exact sentence for the fixture's newest decided claim).
- [ ] **Step 2: Build and run** (see "How to run the UI specs" below) the changed tests.
  Expected: FAIL (test ids missing).
- [ ] **Step 3: Implement** `race-line.tsx` (no dots yet), `race-views.css`, the import in
  `layout.tsx`, and the page changes above.
- [ ] **Step 4: Run** `npm run typecheck`, `npm run build`, then all of `tests/swarm.spec.ts`.
  Expected: the new tests PASS; failing old tests are only the agent-grid ones Task 4 replaces
  (list them in the commit body).
- [ ] **Step 5: Commit** `feat(ui): summary line and pool bar on the race page`

### Task 3: Dots on the line

**Files:**
- Modify: `frontend/app/swarm/race-line.tsx`, `frontend/app/race-views.css`, `frontend/tests/swarm.spec.ts`

**Interfaces:**
- Consumes: `stageOf`, `pickNewDots`, `DOT_CAP` (Task 1); `RaceLineProps` (Task 2).
- Produces: a `resetKey?: string` prop on `RaceLineProps` (the bounty id). When it changes,
  the seen set goes back to `null` (first load). DOM: each dot is
  `<span data-testid="race-dot" data-claim-id={id} data-stage={stage}>`.

Behavior (spec "Motion" and "Details settled" 2, 3, 8): keep `seen` in a ref and live dots in
state; on each `claims` change call `pickNewDots(seen, claims, DOT_CAP - liveDots)`. A dot's
stage follows `stageOf` of its claim; it moves Find → Claim (gold) → Check (waits while
pending, no time limit) → its result row (result color), then fades and is removed. A claim
first seen already decided runs the whole path quickly. A repeat passes Check at once, grey.
`other` dots end at the rare line. After a dot is removed it never returns. Step circles sit
above dots (z-index). Use CSS transitions on `transform`/`opacity`; the builder picks timings
(the page polls every 1.8 s). Reduced motion (`matchMedia("(prefers-reduced-motion: reduce)")`)
or `finished` → render no dots at all.

Test helper: extend `mock()` in `tests/swarm.spec.ts` so `opts.bounty` may be a function
called on each request (`bounty?: Record<string, unknown> | (() => Record<string, unknown>)`).

- [ ] **Step 1: Write failing tests:**
  - "old claims make no dots on first load": running race, 3 decided claims →
    after 2 polls `race-dot` count is 0.
  - "a new claim makes one dot that ends in its result": a second poll adds one `received`
    claim → a `race-dot[data-claim-id=X]` appears; later polls turn it `proven`/`paid` →
    its `data-stage` becomes `paid`; then it is gone (`toHaveCount(0)`).
  - "at most 12 dots": one poll adds 20 claims → `race-dot` count ≤ 12; `line-claims` counts all.
  - "no dots with reduced motion": `page.emulateMedia({ reducedMotion: "reduce" })`, add
    claims → `race-dot` count stays 0 while `line-claims` updates.
  - "no dots when the race is finished": `finished: true` swarm, add claims → 0 dots.
  - Review Focus 1: "switching bounty makes no dots": change `?bounty=` (navigate with
    `page.goto`) is a reload, so instead make the mocked swarm-state switch `bounty_id`
    between polls with a bounty that has 5 claims → 0 dots.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the dots in `race-line.tsx` and CSS; pass `resetKey={bounty?.id}`
  from `page.tsx`.
- [ ] **Step 4: Run** typecheck, build, the new tests and the Task 2 tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ui): animate real claims along the summary line`

### Task 4: `WhoWon`, the agent filter and "More details"

**Files:**
- Create: `frontend/app/swarm/who-won.tsx`
- Modify: `frontend/app/swarm/page.tsx`, `frontend/app/race-views.css`, `frontend/app/globals.css`, `frontend/tests/swarm.spec.ts`

**Interfaces:**
- Consumes: `agentRows`, `agentLabel`, `isWorking`, `net`, `signedUsdc`, `resultOf`,
  `claimsOfAgent` (Task 1).
- Produces:

```ts
export type WhoWonProps = {
  agents: Agent[];
  fallback: boolean;        // agents come from agentsFromHunters: no rings, label fallback form
  running: boolean;         // rings only when running && !fallback
  picked: string | null;    // picked agent account
  onPick: (a: Agent) => void;
};
export default function WhoWon(props: WhoWonProps): JSX.Element | null; // null when agents is empty
```

Test ids: `who-won` (root), `who-row` with `data-result` = `earned|lost|broke|none`,
`who-count` inside each row, `who-total` inside each row (`+12.40 USDC`, `−3.10 USDC`,
`bonds back`), `agent-mark` buttons with `data-role`, `data-result`, `data-working="true"`
when ringed, `aria-label` = `agentLabel(...)`, `aria-pressed` when picked, and a tooltip on
hover and focus with the same text (a `title` alone is not enough: show a styled tooltip
element, `data-testid="agent-tip"`). Row copy (exact): `Earned` / `proved bugs, got paid`;
`Lost money` / `false claims cost more than it earned`; `Went broke` /
`lost all bond money, stopped`; `Nothing yet` / `no proven bug so far`. Key: `● hunter`,
`■ scout`, gold ring `working now`, `Point at a dot to see the agent`. Hunters are circles,
scouts rounded squares; Lost money filled coral; Went broke coral ring with empty inside.
Pulse only without reduced motion; the ring stays. At ≤ 640 px each row puts its name and
count above the marks, and the marks wrap.

Page changes: replace the agent grid section, `Legend`, `useFlashes`, `GLYPHS`,
`RESULT_*` maps and the agent detail `<dl>` with `WhoWon`. Agent filter state holds
`{ account, role, name }`; a click on a picked mark clears it. The claim list shows
`claimsOfAgent(...)` AND the chip filter; filter chip counts use the agent-filtered list. A
bar above the list, `data-testid="agent-filter"`: `Claims by {name} · Show all` (`Show all`
is a button that clears). Clicking a mark scrolls to `#claims`. Then "More details": one
`<details data-testid="more-details">` with `<summary>More details</summary>`, closed by
default, holding Payments (add three lines above the ledger list: `Arsune fees`,
`Bonds forfeited`, `Paid to scouts`, test ids `pay-fees`, `pay-forfeited`, `pay-scouts`),
Tip market, Leaderboard (with Biggest losers) and Activity, content as now. Remove CSS that
no element uses any more.

- [ ] **Step 1: Rewrite the agent-grid tests in `tests/swarm.spec.ts`:**
  - "Who won groups agents by result": `raceAgents(true)` fixture → `who-row[data-result=earned] agent-mark`
    count and order equal `agentRows` order; `who-count` and `who-total` per row.
  - "working agents have a gold ring while the race runs": running fixture → marks of
    `searching|negotiating|claiming|waiting` agents have `data-working="true"`; finished → none.
  - "tooltip and aria-label name the agent": hover a mark → `agent-tip` text equals
    `hunter-… · {strategy} · {tier} · {signed} USDC`; focus with keyboard shows it too; a scout's
    label has no tier; a hex account never shows (`not.toMatch(/[0-9a-f]{64}/)`).
  - "a click on a mark filters the claim list": click → every `claim-row` belongs to that
    hunter, `agent-filter` says `Claims by hunter-…`; with chip `filter-rejected` both apply;
    after one more poll it still applies; `Show all` clears it. Scout mark → claims with that
    `scout.account`.
  - Review Focus 4: "the agent filter survives the fallback": pick an agent, then make
    `/swarm-state` return 502 → the filter bar stays and rows still match by account; picking
    an agent with no claims shows `No claims match.`
  - "fallback agents have no rings and say tier not reported" (idle host fixture).
  - "More details is closed and keeps payments, tips, leaderboard, activity": `more-details`
    has no `open`; after a click, `ledger`, `tips`, `leaderboard`, `activity-log` are visible;
    `pay-fees`, `pay-forfeited`, `pay-scouts` show the old totals.
  - Update every other test that used `agent-tile`, `tile-mark`, `agent-detail`, `ai-badge`,
    `scout-badge`, `race-results`, `result-counts`, `agent-grid` or `total-*` to the new ids
    (AI agents: their label says `AI`; `ai-usage` stays). Tests that read `tips`,
    `ledger`, `leaderboard`, `activity-log`, `biggest-losers` must open `more-details` first.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** `who-won.tsx`, the page changes and the CSS.
- [ ] **Step 4: Run** typecheck, build and all of `tests/swarm.spec.ts`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(ui): Who won agent view, agent filter and More details`

### Task 5: The owner page live box

**Files:**
- Modify: `frontend/app/bounties/race-step.tsx`, `frontend/app/race.css`, `frontend/tests/bounties.spec.ts`, `frontend/tests/race-live.spec.ts`

**Interfaces:**
- Consumes: `RaceLine` and `RaceLineProps` (Tasks 2–3); `OwnerClaim`/`LedgerRow` from
  `bounties/api.ts` fit `LineClaim`/`LineLedgerRow` (fix the types in `race-model.ts` if not).

`LiveRace`: keep its head sentence and its `since` filter; replace the `<dl class="bo-live-stats">`
with `<RaceLine claims={claims} ledger={bounty.ledger} agentCount={swarm ? swarm.agents.length : null}
nameOf={…from swarm.agents ids} titleOf={titler(bounty.claims)} finished={false}
seeAllHref={`/swarm?bounty=${id}`} resetKey={bounty.id} />`. Keep the short `live-stream`
list under it. Ledger rows passed to the line are only those whose `claim_id` is one of this
race's claims (so `line-paid-amount` is this race's money). Remove unused `bo-live-stats` CSS.

- [ ] **Step 1: Update tests:** `tests/bounties.spec.ts:1095-1117` → `live-race` contains
  `race-line`; `line-agents` = `15`; `line-claims` = `0` then `2`; `line-paid` = `1`,
  `line-repeat` = `1`, `line-lost` = `0`; `live-stream` items as now; `line-see-all` href is
  `/swarm?bounty={id}`. `tests/race-live.spec.ts:47-55` → `line-agents` and `line-claims`.
- [ ] **Step 2: Run** `tests/bounties.spec.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the `LiveRace` change.
- [ ] **Step 4: Run** typecheck, build, `tests/bounties.spec.ts` and `tests/swarm.spec.ts`.
  Expected: all PASS.
- [ ] **Step 5: Commit** `feat(ui): summary line in the owner's live race box`

### Task 6: Mobile, real race, screenshots

**Files:**
- Modify: `frontend/tests/swarm.spec.ts` (mobile test), CSS as needed.

- [ ] **Step 1: Write the test** "no horizontal scroll at 390 px with 110 agents"
  (Review Focus 5): viewport 390×844, `swarm(110)` fixture, running → `document.documentElement.scrollWidth <= 390`;
  `race-line` computed `flex-direction`/grid shows the vertical layout (assert the Check step's
  box is below the Claim step's box: `checkBox.y > claimBox.y`); every `agent-mark` box is
  inside the viewport width.
- [ ] **Step 2: Run** it; fix CSS until it PASSES.
- [ ] **Step 3: Screenshots** from the mocked specs at 1440 and 390 px, running and finished
  (`/swarm`), and the owner live box at 1440: save under
  `<scratchpad>/race-views-shots/` and look at each one (label collisions, overflow, the
  line reads left to right, coral always next to a word).
- [ ] **Step 4: Real race** on the `arsune-judge` stack: rebuild only the UI container from
  this tree (`ARSUNE_UI_PORT=3640 ARSUNE_API_PORT=8640 ARSUNE_DEMO_PORT=8641 ARSUNE_SWARM_PORT=8690 ARSUNE_POSTGRES_PORT=55632 ARSUNE_REDIS_PORT=56479 docker compose -p arsune-judge -f compose.yaml -f <scratchpad>/model-mode.yaml up -d --build --no-deps ui`),
  then `cd frontend && ARSUNE_UI_URL=http://127.0.0.1:3640 ARSUNE_LIVE_RACE=1 npx playwright test tests/race-live.spec.ts`
  (scripted agents, dry money). While it runs, take 1440 and 390 screenshots of
  `/swarm?bounty={id}` that show moving dots. Expected: PASS, dots visible mid-race.
- [ ] **Step 5: Full check:** `npm run typecheck`, `npm run build`,
  `npx playwright test tests/race-model.spec.ts tests/swarm.spec.ts tests/bounties.spec.ts`
  against a fresh `next start` (all PASS). `npx prettier --check` on changed files.
- [ ] **Step 6: Commit** `test(ui): mobile layout and screenshots for the race views`

## How to run the UI specs

The mocked specs need a running UI; all data comes from `page.route` mocks:

```bash
cd frontend
npm run build
setsid npx next start --hostname 127.0.0.1 --port 3599 > <scratchpad>/next-3599.log 2>&1 &
ARSUNE_UI_URL=http://127.0.0.1:3599 PLAYWRIGHT_BROWSERS_PATH=/tmp/arsune-browsers \
  npx playwright test tests/swarm.spec.ts
```

Rebuild and restart the server after code changes (kill only the PID/process group you
started; never `pkill -f`). Ports 3540/8540/8541 belong to other services: do not touch them.
