"""Record the swarm board during one fresh race. scripts/record_demo.sh calls this.

Needs a running Compose stack and Playwright Chromium (`uv run playwright install chromium`).
It shows an intro card, restarts the Compose `swarm` service (a new bounty and a new race),
records /swarm at 1440x900 until the race finishes, and ends with a card of the summary.
Writes <out>/arsune-swarm.webm and a full-page screenshot <out>/arsune-swarm.png.
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SIZE = {"width": 1440, "height": 900}
INTRO_SECONDS = 6
OUTRO_SECONDS = 8
# While the race runs: the top of the board, then the payments and tip market row.
RACE_TOUR = [(0, 24), (930, 10)]
INTRO = (
    "Arsune",
    [
        "The judge for AI agents that hunt bugs.",
        "100 hunter agents and 10 scouts race on a demo shop with 50 seeded bugs and 18 decoys.",
        "Every claim carries a signed bond. Arsune checks it again in a fresh browser.",
        "Proven, unique bugs get paid. False claims lose their bond.",
        "Simulation: dry settlement, no real money.",
    ],
)


def default_ui() -> str:
    return os.getenv("ARSUNE_UI_URL") or f"http://127.0.0.1:{os.getenv('ARSUNE_UI_PORT', '3540')}"


def swarm_state(ui: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{ui}/swarm-state", timeout=5) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def card(title: str, lines: list[str]) -> str:
    items = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    return f"""<!doctype html><html><body style="margin:0;height:100vh;display:flex;
      flex-direction:column;justify-content:center;padding:0 140px;background:#172d29;
      color:#f6f5ef;font:26px/1.5 Arial,Helvetica,sans-serif">
      <h1 style="font:64px Georgia,serif;color:#d9ef8b;margin:0 0 28px">{html.escape(title)}</h1>
      {items}</body></html>"""


def summary(state: dict) -> list[str]:
    prefixes = ("Summary:", "Payouts:", "Tips:")
    lines = [e["text"] for e in state["log"] if e["text"].startswith(prefixes)]
    return lines + ["Dry mode: no real money moved. Run it: docker compose up --build --wait"]


def scroll(page, y: int):
    page.evaluate("y => window.scrollTo({top: y, behavior: 'smooth'})", y)


def record(ui: str, out: Path, timeout: float) -> dict:
    old = swarm_state(ui)
    old_run = old["run_id"] if old else None
    with tempfile.TemporaryDirectory() as raw, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport=SIZE, record_video_dir=raw, record_video_size=SIZE)
        page = context.new_page()
        page.set_content(card(*INTRO))
        shown = time.monotonic()
        subprocess.run(["docker", "compose", "restart", "swarm"], cwd=ROOT, check=True)
        deadline = time.monotonic() + 90
        while True:
            state = swarm_state(ui)
            if state and state["run_id"] != old_run and state.get("bounty_id"):
                break
            if time.monotonic() > deadline:
                raise SystemExit("The swarm did not start a new race within 90 s.")
            page.wait_for_timeout(500)
        page.wait_for_timeout(max(0.0, INTRO_SECONDS - (time.monotonic() - shown)) * 1000)
        page.goto(f"{ui}/swarm")
        page.wait_for_selector("[data-testid=agent-tile]")
        deadline, step = time.monotonic() + timeout, 0
        while not state["finished"]:
            if time.monotonic() > deadline:
                raise SystemExit(f"The race did not finish within {timeout:.0f} s.")
            y, seconds = RACE_TOUR[step % len(RACE_TOUR)]
            scroll(page, y)
            until = time.monotonic() + seconds
            while time.monotonic() < until and not state["finished"]:
                page.wait_for_timeout(2000)
                state = swarm_state(ui) or state
            step += 1
        scroll(page, 0)
        page.wait_for_timeout(5000)  # the board polls every 1.8 s
        page.screenshot(path=str(out / "arsune-swarm.png"), full_page=True)
        for y in (930, 1500):
            scroll(page, y)
            page.wait_for_timeout(5000)
        page.set_content(card("Result", summary(state)))
        page.wait_for_timeout(OUTRO_SECONDS * 1000)
        video = page.video
        context.close()
        shutil.move(video.path(), out / "arsune-swarm.webm")
        browser.close()
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui", default=default_ui(), help="Arsune UI base URL")
    parser.add_argument("--out", type=Path, default=ROOT / ".data" / "demo")
    parser.add_argument("--timeout", type=float, default=480, help="race time limit, seconds")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    state = record(args.ui.rstrip("/"), args.out, args.timeout)
    for line in summary(state)[:-1]:
        print(line)
    print(f"Wrote {args.out / 'arsune-swarm.webm'} and {args.out / 'arsune-swarm.png'}")


if __name__ == "__main__":
    main()
