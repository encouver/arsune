# Simplify the UI: one clean product

Date: 12 Nov 2026. Status: owner goal ("the UI has several things that I don't understand what
they are for… simplify the UI/UX so we can just present a clean product"). All items below are
my calls from an audit of every page on the test stack; the owner can reverse any of them.

## Why

The product is one thing: **a bug race**. An owner starts a race on a site, agents claim bugs,
Arsune checks each claim, and only proven bugs get paid. The UI still carries parts that do not
serve that story, or that repeat it:

- A "More tools" menu with three older tools (Website check, Incident investigation, Local demo).
- The "Simulation — no real money" label up to five times on one page, plus "Simulated" tags.
- A greyed-out "Scripted + AI agents" option with a technical reason ("this race host has no
  model key"), on the start page and again in step 4.
- A long owner page: every false claim and repeat is open, done steps keep their full cards.
- "Who won" money that does not match "Paid" after a race ends (it reads the race host's
  counters, which stop when the runner stops; Arsune's records keep paying).
- Duplicate panels: Leaderboard and Biggest losers repeat what "Who won" shows. "Tip market"
  is jargon.

## Goal

A first-time visitor sees one product with three places: **Start a race**, **Your races**,
**Live race**. Every visible element has a clear job. No element shows a technical reason
for something the visitor cannot change.

## Changes

1. **Navigation.** Three links: "Start a race" (`/`), "Your races" (`/races`), "Live race"
   (`/swarm`). Remove the "More tools" menu. `/checks`, `/incidents` and `/demo` stay at their
   URLs and keep working, but the race product does not link to them.
2. **One simulation label per page.** The text is "Simulation · no real money", in the page
   header (on the start page, in the form card head). Remove every other copy of the pill and
   the per-row "Simulated" tags. In dry mode a payment row shows no link.
3. **Agent choice only when it is a real choice.** Remove the "Agents" choice from the start
   page form. In step 4, show the choice only when the race host has AI agents
   (`host.ai_available`). When it does not, show no choice and no reason. Copy when shown:
   "Standard agents (free)": "{n} hunters and {m} scouts with fixed rules." and
   "Standard + AI agents": "AI agents also search. They use model credits."
4. **Owner page is short.**
   - A done step (Prove, Loads) shows one line: its title and "Done". Its old body is behind a
     small "Show" disclosure.
   - Results: the proven bugs list stays open. "False claims (bond lost)" and "Repeats (already
     found)" are closed disclosures with their count in the summary. "Not checked" too.
   - Fund step in dry mode: no "Other ways to fund" (no real money moves in a simulation).
   - "Technical details" stays closed at the bottom.
5. **"Who won" agrees with "Paid".** An agent's `earned` and `bonds_lost` come from Arsune's
   records (`bounty.hunters`: `earned + scout_earned`, `bonds_lost`) when the API knows the
   account; the race host's counters are used only for agents the API does not list. State
   (working now, broke) still comes from the race host.
6. **Live race page, fewer panels.** "Claim stream" becomes "Claims". In "More details" keep
   Payments, "Scout tips" (was "Tip market") and Activity. Remove Leaderboard and Biggest
   losers: "Who won" shows the same.
7. **Start page copy.** "No site at hand? Use the local demo shop" becomes "No site at hand?
   Try the demo shop", with "A small shop with known bugs. Arsune sets the proof for you."

8. **Storytelling** (owner: "improve the storytelling too"). Each page tells the same short
   story in the same words, and the result pages tell the outcome as a sentence, not only as
   numbers. Words used everywhere: *race*, *agents* (hunters and scouts), *claim*, *bond* (a small
   deposit), *proven*, *paid*. Define "bond" once on the start page.
   - **Start page hero.** Problem first, then the promise:
     eyebrow "Proof-paid bug race"; headline "AI builds sites fast. Bugs ship with them."; second
     line "Let agents race to find them. Pay only for proof."; body "Agents search your site and
     claim bugs. Each claim locks a bond, a small deposit. Arsune checks every claim again in a
     fresh browser. You pay only for bugs it proves."
     Three steps, in order: "1 · Fund a bounty pool" / "You set the reward per bug. Nothing is
     paid until a bug is proven."; "2 · Agents race" / "Hunters claim bugs. Scouts sell tips to
     hunters."; "3 · Arsune checks each claim" / "Proven: the agent is paid. False: the agent loses
     its bond, and the bond goes into your pool. Repeat: the bond comes back."
     One line under the steps: "You get a list of proven bugs, each with evidence that anyone can
     check again."
   - **Live race page: one story line under the header**, data-driven, `data-testid="race-story"`:
     - running: "{n} agents search {site}. Each claim locks a bond. Arsune checks it in a fresh
       browser."
     - finished: "Race finished. {paid} bugs proven and paid ({money} USDC).
       {lost} false claims lost their bond. False claims cost the owner nothing." Leave out a
       sentence whose count is 0, except the first one.
     - no race: keep the current notices.
   - **Owner page: a result headline** above the results list when the bounty has decided
     claims, `data-testid="result-story"`: "Your race found {proven} bugs." then, when
     non-zero: "{paid} are paid ({money} USDC)." "{unfunded} wait for more budget." "{false}
     false claims cost you nothing: their bonds went into your pool." "{repeats} repeats got
     their bond back."
   - Money in these sentences uses the same sources as the summary line (completed `reward_out`
     plus `scout_share` rows). Counts use `claimGroup`; paid excludes `unfunded`.

## Not changed

Backend, API, race host, money rules, the summary line and "Who won" design (except item 5),
the `/races` list, and the `/checks`, `/incidents`, `/demo` pages themselves.
