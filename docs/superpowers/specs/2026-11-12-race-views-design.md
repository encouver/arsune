# Race views: the summary line and the "Who won" agent view

Date: 12 Nov 2026. Status: approved direction, design for review.
Mockups (throwaway, for look and feel only): [summary-line.png](2026-11-12-race-views/summary-line.png)
(use the **Light** version), [agent-views.png](2026-11-12-race-views/agent-views.png) (use
**A · Who won** only). The HTML files next to them show the motion.

## Why

The owner looked at the live race board (`/swarm`) and said it does not feel like a great
product. The board shows the end state only: eight stat tiles and a dark grid of 110 boxes
with glyphs, numbers and seven state colors. You cannot see the product do its work.

The owner asked for an "assembly line": each claim passes from step A to B to C, and you
watch it happen. In review, the owner chose a **clean, light** line with few words, and a
second view that shows **all agents at once, in a clean way**.

What success looks like: a person who opens the race page for the first time understands
in ten seconds what the agents do, what Arsune checks, and where the money goes.

## Goals

1. A **summary line** that shows the race as a flow: Find → Claim → Check → results.
2. An **agent view** ("Who won") that shows every agent on one screen, grouped by result.
3. Fewer words, fewer colors, a light theme that matches the rest of the site.
4. The animation is honest: each moving dot is a real claim, and it ends in that claim's
   real result.

## Non-goals

- No change to the backend, the race host, or the API. Use the data the page already loads
  (`SwarmState` from the race host, `Bounty` with `claims`, `ledger`, `hunters`).
- No change to money rules, and no UI for the planned monthly price.
- No change to `/checks`, `/incidents`, `/demo`, `/races` or the start page.

## Anti-goals (a failure even if it works)

- A dense dashboard again: many stat tiles, a legend of glyphs, seven colors.
- Animation that is decoration: dots that do not match real claims, or that loop forever
  after the race ends.
- Meaning by color alone. Every color sits next to a word or a shape.
- A dark "control room" theme. The owner chose light.

## Where the views go

### `/swarm` (the race page), top to bottom

1. **Header**: site name (serif, italic, as now), race state ("Race running · 110 agents",
   "Race finished", or the current idle/offline texts), and the "Simulation · no real money"
   pill in dry mode (the live-mode badge stays as now). Keep all current banners (other
   race, errors, site check failed, paused, no race yet).
2. **Pool bar**: "Bounty pool", a thin bar, and "27.19 of 80.00 USDC left". The bar is the
   part of the funded pool that is still left.
3. **Summary line** (below).
4. **Agent view: Who won** (below).
5. **Claims**: the current claim stream with its filters (All, Proven, False, Repeats), as now.
6. **More details**: Payments, Tip market, Leaderboard and Activity, closed by default
   (`<details>` or a similar disclosure). Their content stays as now.

The eight stat tiles and the dark agent grid are removed. Their numbers move into the line
and the agent view (see "Data").

### `/bounties/{id}` (the owner's race step)

The live race box (`LiveRace` in `app/bounties/race-step.tsx`) shows the summary line in
place of its five-number stat list. The short list of the latest claims under it stays.
The link to the full race page stays.

## The summary line

Look: see `summary-line.png`, Light version.

- Three numbered steps on one horizontal line:
  1. **Find**: "Agents search the site".
  2. **Claim**: "{claims} claims · bond locked".
  3. **Check**: "New? True? Fresh browser".
- After Check, the line splits into three result rows at the right:
  - **Paid**: count, and "+{USDC} USDC" paid out (hunters plus scouts).
  - **Bond lost**: count, and "not true".
  - **Repeat**: count, and "bond back".
- Under the three rows, one small muted line with the parts that are not zero, for example
  "3 could not be checked · 2 proven, but the pool was empty · bond back". The first part
  counts `unverified` claims; the second counts proven claims with status `unfunded`.
  *(My call: these are rare and get no branch of their own.)*
- While claims wait for a result, the Check label shows "{n} being checked" under its
  text. *(My call.)*
- A **"Latest"** row under the line: one plain sentence for the newest decided claim, for
  example "hunter-045 proved "Too wide on mobile" on /shop/c/support → paid +2.14 USDC".
  It changes when a new claim is decided. At the right: "See all {n} claims ↓", which
  scrolls to the claim list.

### Motion

- Each new claim since the last poll becomes a dot. It enters at Find and moves into Claim
  (the dot turns gold: the bond is locked). It waits in Check while Arsune checks it. When
  its outcome arrives, it moves out of Check to its result row, takes the result color,
  and fades into that row's mark.
- The step circles sit above the dots, so a dot goes "into" a step and comes out.
- A repeat moves through Check at once (no browser check), in grey.
- At most about 12 dots move at the same time. When more claims arrive, the counts still
  update; the extra claims get no dot. *(My call.)*
- When the race is finished, no dots move. The line shows the final counts.
- With `prefers-reduced-motion`, no dot moves. The counts and the "Latest" row still update.
- The pool bar changes width smoothly when money goes out.

## The agent view: "Who won"

Look: see `agent-views.png`, option A only.

- Title "Who won" and a key: ● hunter, ■ scout, a gold ring = working now, and
  "Point at a dot to see the agent".
- Four rows. Each row: the name and a short note at the left, one mark for each agent in the
  middle (the marks wrap to a second line), and the count with a money total at the right.

  | Row | Note | Mark | Total at the right |
  | --- | --- | --- | --- |
  | Earned | proved bugs, got paid | filled green | "+{USDC} USDC" |
  | Lost money | false claims cost more than it earned | filled coral | "−{USDC} USDC" |
  | Went broke | lost all bond money, stopped | coral ring, empty inside | "−{USDC} USDC" |
  | Nothing yet | no proven bug so far | light grey | "bonds back" |

- The result of an agent is the current rule `resultOf` in `app/swarm/page.tsx`: broke if
  its state is `broke`; else lost money if `bonds_lost > earned`; else earned if
  `earned > 0`; else nothing yet.
- Inside a row, the agents are in order of money, the most first.
- Hunters are circles, scouts are small rounded squares.
- An agent that works now (state searching, negotiating, claiming or waiting, while the race
  runs) has a soft gold ring that pulses. No pulse with `prefers-reduced-motion`; the ring
  stays.
- Pointing at a mark, or focusing it with the keyboard, shows a tooltip:
  "hunter-045 · mobile · trusted · +2.14 USDC". Use the same agent names as the page shows
  now (names, not hashes). Scouts have no tier.
- Clicking a mark filters the claim list to that agent's claims, and scrolls to it. A
  second click, or a "Show all" control on the list, clears the filter. *(My call.)*
- When an agent's result changes during the race, its mark moves to the new row.

## Data

All values come from data the page loads now.

| Shown | Source |
| --- | --- |
| Money into the pool | the sum of completed `fund` rows **plus** completed `bond_forfeit` rows (a lost bond goes into the pool, so `budget_amount` can grow past the funded amount) |
| Left in the pool | `bounty.budget_amount`; the bar width is left ÷ money into the pool, clamped to 0–100%. No bar when money into the pool is 0 |
| Claims at step 2 | the claims of this race (as the page filters them now) |
| Being checked | claims in `claimGroup` "pending" (`received`, `checking`, `needs_review`) |
| Paid count | proven claims that are **not** `unfunded` |
| Bond lost, Repeat counts | outcomes `rejected` and `duplicate` |
| Paid money | completed `reward_out` plus `scout_share` ledger rows, as "Paid to hunters" and "Paid to scouts" compute now |
| Small muted line | `unverified` claims, and proven claims with status `unfunded` (each counted once, never also in Paid) |
| Agents and their results | `swarm.agents` (`state`, `earned`, `bonds_lost`, `role`, `strategy`, `tier`) |
| "Latest" sentence | the newest claim with an outcome, with the claim title from `claim-text.ts` |

The Arsune fees, bonds forfeited and "cost of false claims to the owner" tiles go away from
the top. Keep these numbers in the Payments part of "More details". *(My call: the owner's
cost of false claims is always 0, and the line already says "not true" for bond lost.)*

## Look

Use the site's tokens in `app/globals.css` (`--paper`, `--ink`, `--muted`, `--line`,
`--green`, `--serif`) where they fit. New colors for the views:

| Use | Color | Note |
| --- | --- | --- |
| Paid, Earned | `#2f7a3a` | |
| Bond lost, Lost money, Went broke | `#ec7a5c` | Checked with the dataviz validator against `#2f7a3a`: safe for color-blind readers (ΔE 9.5). Contrast with the background is under 3:1, so it always sits next to a text label. |
| Repeat, Nothing yet, Not checked | grey (`#8a938d` mark, `#c9cfca` for "nothing yet") | |
| Bond locked, working now | gold `#c99a2e` | |

Big numbers use the serif. Labels are short and plain.

## Accessibility

- Every mark in the agent view is a button with an `aria-label` that holds the tooltip text.
- Identity is never by color alone: each row and each result has a word, and hunters and
  scouts differ by shape.
- The line has a text version for screen readers (the step counts and result counts as a
  list), and the "Latest" row is `aria-live="polite"`.
- `prefers-reduced-motion` stops all motion (see above).

## Mobile (390 px wide)

- The summary line turns vertical: the three steps go down the page, and the three result
  rows follow under Check. Dots move down, not across. *(My call.)*
- The agent view rows put the name and the count above the marks, and the marks wrap.
- No horizontal scroll on the page.

## Testing

- Playwright: `tests/swarm.spec.ts`, `tests/race-live.spec.ts` and `tests/bounties.spec.ts`
  test the current board and live box. Update them to the new layout, and keep the
  `data-testid` values that still make sense.
- New checks: the line's counts match the claim outcomes; the agent rows match `resultOf`
  for each agent; the tooltip and `aria-label` text; a click on a mark filters the claim
  list; reduced motion shows no moving dots; no horizontal overflow at 390 px.
- Screenshots at 1440 px and 390 px, on a running race and on a finished race.

## Details settled after the builder review

All of these are my calls; the owner can change them.

1. **Money in the "Latest" sentence.** The API ledger rows carry `claim_id` (the frontend
   `LedgerRow` type lacks the field; add it). For a paid claim, show the hunter's completed
   `reward_out` amount for that claim, and when a `scout_share` row exists for it, add
   "· {scout} +{amount}". Sentences for other results, with no amount:
   - rejected: "{hunter} claimed {title} on {page} → it was not true, bond lost"
   - duplicate: "{hunter} sent a bug that was already proven → bond back"
   - unverified: "{hunter}: Arsune could not check {title} → bond back"
   - unfunded: "{hunter} proved {title}, but the pool was empty → bond back"
   If a paid claim has no completed ledger row yet, show "proven, payment pending".
2. **Which claims get a dot.** Only claims that the page sees for the first time after the
   first load. Keep a set of seen claim ids. On first load (a running or finished race),
   the line starts at the current counts with no dots. When the page sees a claim whose
   result is already known, its dot runs the full path quickly. When one poll brings more
   than the dot cap, the extra claims get no dot.
3. **Dot stages.** Claim = status `received`; Check = `checking` or `needs_review`;
   result = an outcome. A dot waits in Check as long as the claim is pending, with no time
   limit. When the outcome of a claim changes after its dot has finished (for example a
   payment completes), the dot does not move again; only the counts change.
4. **No race state.** When the host is idle, down, or runs another race, the page falls back
   to agents from `bounty.hunters` (`agentsFromHunters`). "Who won" still renders from that
   fallback, with no "working now" rings, tier "not reported" and strategy left out of the
   tooltip. The summary line shows the counts from the bounty's claims with no dots. When
   there is no bounty at all, both views are hidden and the current "No race yet" notice
   stays. A failed site check keeps its banner, and the line shows zeros.
5. **Agent filter.** A hunter mark filters by `hunter_account`; a scout mark filters by
   `scout.account` (the claims that it sourced). The agent filter works together with the
   All/Proven/False/Repeats chips (both apply). A small bar above the list says
   "Claims by hunter-045 · Show all", and the filter stays across polls until it is cleared.
6. **The owner page** (`LiveRace`). The line component takes plain props (claims, ledger
   rows, agents or null, finished), so both pages feed it. The owner page shows the line and
   the "Latest" sentence; its "See all {n} claims" link goes to `/swarm?bounty={id}`. It keeps
   its `since` filter (claims from this race only). "Who won" is on `/swarm` only.
7. **Test hooks.** Renaming or removing `data-testid` values is allowed. Keep `live-race` and
   `live-stream`. Update every Playwright spec in `frontend/tests/` that uses a removed hook
   or the removed "Live swarm board" heading. No hidden tests depend on them; CI runs only
   the specs in the repo.
8. **Dots in the DOM.** Each moving dot is an element with `data-testid="race-dot"` and
   `data-claim-id`. With `prefers-reduced-motion`, no `race-dot` element is rendered.

## Left to the builder

- The component split (for example `RaceLine` and `WhoWon` in `app/swarm/`, used by both
  pages) and how the CSS is organized (the page uses `swarm.module.css` and global `sw-`
  classes now).
- The exact animation timing and how to queue dots between polls (the page polls every
  1.8 s).
- How "More details" opens (one disclosure or one per panel).
