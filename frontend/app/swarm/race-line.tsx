"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
  type ReactNode,
} from "react";
import { usdc } from "./format";
import {
  DOT_CAP,
  latestSentence,
  lineCounts,
  pickNewDots,
  rareText,
  signedUsdc,
  stageOf,
  type LineClaim,
  type LineLedgerRow,
  type NameOf,
  type Pool,
  type Stage,
} from "./race-model";

export type RaceLineProps = {
  claims: LineClaim[];
  ledger: LineLedgerRow[];
  // null: the race state is not known, so Find shows no count.
  agentCount: number | null;
  nameOf: NameOf;
  titleOf: (c: LineClaim) => string;
  // true: no dots move.
  finished: boolean;
  seeAllHref: string;
  // The bounty (or race) on show; when it changes, its claims count as old again.
  resetKey?: string;
};

// A dot is one claim. It walks Find → Claim → Check → fork → its result row, one place
// per step, waits where its claim waits, then fades.
type Place = "find" | "claim" | "check" | "fork" | "end" | "gone";
type Dot = { id: string; place: Place; stage?: Stage };

const STEP_MS = 650;
const RESULTS: Stage[] = ["paid", "lost", "repeat", "other"];

function nextPlace(place: Place, stage: Stage): Place | null {
  if (place === "find") return "claim";
  if (place === "claim") return stage === "claim" ? "claim" : "check";
  if (place === "check") return RESULTS.includes(stage) ? "fork" : "check";
  if (place === "fork") return "end";
  if (place === "end") return "gone";
  return null;
}

// The stage is fixed when the dot reaches its row: a later change (a payment) does not move it.
function stepDots(dots: Dot[], stages: Map<string, Stage>): Dot[] {
  let changed = false;
  const next: Dot[] = [];
  for (const d of dots) {
    const stage = d.stage ?? stages.get(d.id) ?? "other";
    const place = nextPlace(d.place, stage);
    if (place !== d.place) changed = true;
    if (place === d.place) next.push(d);
    else if (place === "end") next.push({ id: d.id, place, stage });
    else if (place) next.push({ ...d, place });
  }
  return changed ? next : dots;
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

function useDots(claims: LineClaim[], still: boolean, resetKey?: string) {
  const [dots, setDots] = useState<Dot[]>([]);
  const seen = useRef<Set<string> | null>(null);
  const key = useRef(resetKey);
  const live = useRef(0);
  const stages = useMemo(
    () => new Map(claims.map((c) => [c.id, stageOf(c)])),
    [claims],
  );
  const stagesRef = useRef(stages);

  useEffect(() => {
    live.current = dots.length;
  }, [dots]);

  useEffect(() => {
    stagesRef.current = stages;
    if (key.current !== resetKey) {
      key.current = resetKey;
      seen.current = null;
      live.current = 0;
      setDots([]);
    }
    const free = still ? 0 : DOT_CAP - live.current;
    const picked = pickNewDots(seen.current, claims, free);
    seen.current = picked.seen;
    if (picked.fresh.length === 0) return;
    live.current += picked.fresh.length;
    const fresh = picked.fresh.map((id): Dot => ({ id, place: "find" }));
    setDots((d) => [...d, ...fresh]);
  }, [claims, stages, still, resetKey]);

  useEffect(() => {
    if (still) setDots([]);
  }, [still]);

  const moving = dots.length > 0;
  useEffect(() => {
    if (!moving) return;
    const timer = setInterval(
      () => setDots((d) => stepDots(d, stagesRef.current)),
      STEP_MS,
    );
    return () => clearInterval(timer);
  }, [moving]);

  return { dots: still ? [] : dots, stages };
}

export function PoolBar({ pool }: { pool: Pool }): JSX.Element | null {
  if (pool.fraction === null) return null;
  return (
    <div className="rv-pool" data-testid="pool-bar">
      <span className="rv-pool-name">Budget</span>
      <span className="rv-pool-bar" aria-hidden="true">
        <i style={{ width: `${pool.fraction * 100}%` }} />
      </span>
      <span className="rv-pool-text">
        <b>{usdc(pool.left)}</b> of {usdc(pool.into)} USDC left
      </span>
    </div>
  );
}

function Step({
  n,
  name,
  children,
}: {
  n: number;
  name: string;
  children: ReactNode;
}) {
  return (
    <div className={`rv-step rv-step-${n}`} data-step={name.toLowerCase()}>
      <span className="rv-node">{n}</span>
      <span className="rv-label">
        <b>{name}</b>
        {children}
      </span>
    </div>
  );
}

function ResultRow({
  kind,
  name,
  count,
  note,
  testId,
  noteTestId,
}: {
  kind: string;
  name: string;
  count: number;
  note: string;
  testId: string;
  noteTestId?: string;
}) {
  return (
    <li className="rv-result" data-result={kind}>
      <i className="rv-swatch" />
      <span className="rv-result-name">{name}</span>
      <b className="rv-count" data-testid={testId}>
        {count}
      </b>
      <span className="rv-note" data-testid={noteTestId}>
        {note}
      </span>
    </li>
  );
}

// The part after the arrow is the result; it gets the result's weight and color.
function Latest({
  claims,
  text,
  claimId,
}: {
  claims: LineClaim[];
  text: string;
  claimId: string;
}) {
  const cut = text.indexOf(" → ");
  if (cut < 0) return <>{text}</>;
  const c = claims.find((x) => x.id === claimId);
  return (
    <>
      {text.slice(0, cut + 3)}
      <b className="rv-latest-result" data-stage={c ? stageOf(c) : "other"}>
        {text.slice(cut + 3)}
      </b>
    </>
  );
}

export default function RaceLine({
  claims,
  ledger,
  agentCount,
  nameOf,
  titleOf,
  finished,
  seeAllHref,
  resetKey,
}: RaceLineProps): JSX.Element {
  const reduced = useReducedMotion();
  const { dots, stages } = useDots(claims, finished || reduced, resetKey);
  const counts = lineCounts(claims, ledger);
  const rare = rareText(counts);
  const latest = latestSentence(claims, ledger, nameOf, titleOf);
  const paidMoney = `${signedUsdc(counts.paidAmount)} USDC`;
  const finding =
    agentCount === null
      ? "Agents search the site"
      : `${agentCount} agents ${finished ? "searched" : "search"} the site`;
  return (
    <section
      className="rv-line"
      data-testid="race-line"
      aria-label="Hunt summary"
    >
      <div className="rv-stage" aria-hidden="true">
        <div className="rv-track">
          <span className="rv-wire" />
          <svg
            className="rv-fork"
            viewBox="0 0 100 174"
            preserveAspectRatio="none"
          >
            <path d="M0 87 L100 29 M0 87 L100 87 M0 87 L100 145" />
          </svg>
          <Step n={1} name="Find">
            <span>
              {agentCount === null ? (
                finding
              ) : (
                <>
                  <span data-testid="line-agents">{agentCount}</span> agents{" "}
                  {finished ? "searched" : "search"} the site
                </>
              )}
            </span>
          </Step>
          <Step n={2} name="Report">
            <span>
              <span data-testid="line-claims">{counts.claims}</span> reports ·
              deposit paid
            </span>
          </Step>
          <Step n={3} name="Check">
            <span>New? Real? Clean browser</span>
            {counts.checking > 0 && (
              <span className="rv-checking" data-testid="line-checking">
                {counts.checking} being checked
              </span>
            )}
          </Step>
        </div>
        <ul className="rv-results">
          <ResultRow
            kind="paid"
            name="Paid"
            count={counts.paid}
            note={paidMoney}
            testId="line-paid"
            noteTestId="line-paid-amount"
          />
          <ResultRow
            kind="lost"
            name="Deposit lost"
            count={counts.lost}
            note="fake"
            testId="line-lost"
          />
          <ResultRow
            kind="repeat"
            name="Repeat"
            count={counts.repeat}
            note="deposit back"
            testId="line-repeat"
          />
        </ul>
        {dots.length > 0 && (
          <div className="rv-dots">
            {dots.map((d) => (
              <span
                key={d.id}
                className="rv-dot"
                data-testid="race-dot"
                data-claim-id={d.id}
                data-stage={d.stage ?? stages.get(d.id) ?? "other"}
                data-place={d.place}
              >
                <i />
              </span>
            ))}
          </div>
        )}
        {rare && (
          <p className="rv-rare" data-testid="line-rare">
            <i className="rv-swatch" />
            {rare}
          </p>
        )}
      </div>
      <ul className="rv-sr">
        <li>Step 1, Find: {finding}.</li>
        <li>Step 2, Report: {counts.claims} reports, deposit paid.</li>
        <li>
          Step 3, Check: new, real, in a clean browser. {counts.checking} being
          checked.
        </li>
        <li>
          Paid: {counts.paid}, {usdc(counts.paidAmount)} USDC paid out.
        </li>
        <li>Deposit lost: {counts.lost}, fake.</li>
        <li>Repeat: {counts.repeat}, deposit back.</li>
        {rare && <li>{rare}.</li>}
      </ul>
      <div className="rv-latest">
        <span className="rv-latest-key">Latest</span>
        <p data-testid="line-latest" aria-live="polite">
          {latest ? (
            <Latest
              claims={claims}
              text={latest.text}
              claimId={latest.claimId}
            />
          ) : (
            "No result yet."
          )}
        </p>
        <a className="rv-see-all" href={seeAllHref} data-testid="line-see-all">
          See all {counts.claims} reports{" "}
          {seeAllHref.startsWith("#") ? "↓" : "→"}
        </a>
      </div>
    </section>
  );
}
