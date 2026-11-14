"""uv run python -m swarm {simulate,live,serve} [options]. See docs/SWARM.md."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from swarm.agents import SCOUT_FINDERS, STRATEGIES
from swarm.host import AI_RACES_PER_DAY, UI_AI_BUDGET_USD, RaceHost, available_model, serve
from swarm.runner import DEFAULT_MIX, DEFAULT_SCOUT_MIX, Config, SwarmError, run

# Live runs default to small amounts inside the approved 1 USDC budget.
SIMULATION_AMOUNTS = {"reward": 1_000_000, "bond": 100_000, "budget": 20_000_000}
LIVE_AMOUNTS = {"reward": 100_000, "bond": 10_000, "budget": 500_000}


def weights(text: str, names) -> dict[str, int]:
    """Parse 'links=20,mobile=10' into {'links': 20, 'mobile': 10}."""
    result = {}
    for part in filter(None, text.split(",")):
        name, _, value = part.partition("=")
        if name.strip() not in names or not value.strip().isdigit():
            raise argparse.ArgumentTypeError(f"Use name=weight with names from {sorted(names)}.")
        result[name.strip()] = int(value)
    return result


def parse(argv: list[str]) -> tuple[Config, Path | None]:
    parser = argparse.ArgumentParser(prog="python -m swarm", description=__doc__)
    parser.add_argument("mode", choices=["simulate", "live"])
    parser.add_argument("--api", default="http://127.0.0.1:8540", help="Arsune API base URL")
    parser.add_argument("--site", default="http://127.0.0.1:8541/shop/", help="bounty site URL")
    parser.add_argument("--bounty-id", help="join an existing open bounty")
    parser.add_argument("--setup", action="store_true", help="create, prove and fund a bounty")
    parser.add_argument("--hunters", type=int, default=100, help="scripted hunters")
    parser.add_argument("--scouts", type=int, default=10, help="scripted scouts")
    parser.add_argument(
        "--ai-hunters", type=int, default=0, help="hunters that decide with a language model"
    )
    parser.add_argument(
        "--ai-scouts", type=int, default=0, help="scouts that decide with a language model"
    )
    parser.add_argument(
        "--ai-budget", type=float, default=0.5, help="USD for all model calls of the run"
    )
    parser.add_argument(
        "--mix",
        type=lambda t: weights(t, STRATEGIES),
        default=DEFAULT_MIX,
        help="hunter strategy weights, e.g. links=17,mobile=13,prices=12,images=8,errors=5,"
        "generalist=25,careless=10,copycat=10",
    )
    parser.add_argument(
        "--scout-mix",
        type=lambda t: weights(t, SCOUT_FINDERS),
        default=DEFAULT_SCOUT_MIX,
        help="scout weights, e.g. links=3,mobile=2,prices=2,images=1,errors=1,hasty=1",
    )
    parser.add_argument("--reward", type=int, help="reward per proven bug, token units")
    parser.add_argument("--bond", type=int, help="bond per claim, token units")
    parser.add_argument("--budget", type=int, help="bounty funding, token units")
    parser.add_argument("--fee-bps", type=int, default=1000)
    parser.add_argument(
        "--hunter-wallet", type=int, default=300_000, help="simulated starting bond money"
    )
    parser.add_argument("--duration", type=float, default=300, help="time limit in seconds")
    parser.add_argument("--pace", type=float, default=1.0, help="think-time multiplier, 0 = fast")
    parser.add_argument("--pages", type=int, default=60, help="page budget per agent")
    parser.add_argument("--browser-pages", type=int, default=6, help="shared Chromium page pool")
    parser.add_argument("--copy-loss-limit", type=int, default=3)
    parser.add_argument("--settle-timeout", type=float, default=120)
    parser.add_argument("--state-host", default="127.0.0.1")
    parser.add_argument("--state-port", type=int, default=8590)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--accounts", type=Path, help="live accounts file (default ARSUNE_SWARM_ACCOUNTS_FILE)"
    )
    parser.add_argument("--i-approve-mainnet", dest="approve_mainnet", action="store_true")
    parser.add_argument("--hold", action="store_true", help="keep serving /state after the run")
    parser.add_argument("--quiet", action="store_true", help="do not print events")
    parser.add_argument("--summary-json", type=Path, help="also write the summary here")
    args = parser.parse_args(argv)
    amounts = LIVE_AMOUNTS if args.mode == "live" else SIMULATION_AMOUNTS
    config = Config(
        live=args.mode == "live",
        api_url=args.api,
        site_url=args.site,
        bounty_id=args.bounty_id,
        setup=args.setup,
        hunters=args.hunters,
        scouts=args.scouts,
        ai_hunters=args.ai_hunters,
        ai_scouts=args.ai_scouts,
        ai_budget=args.ai_budget,
        mix=args.mix,
        scout_mix=args.scout_mix,
        reward=args.reward or amounts["reward"],
        bond=args.bond or amounts["bond"],
        budget=args.budget or amounts["budget"],
        fee_bps=args.fee_bps,
        hunter_wallet=args.hunter_wallet,
        duration=args.duration,
        pace=args.pace,
        pages=args.pages,
        browser_pages=args.browser_pages,
        copy_loss_limit=args.copy_loss_limit,
        settle_timeout=args.settle_timeout,
        state_host=args.state_host,
        state_port=args.state_port,
        seed=args.seed,
        accounts=args.accounts,
        approve_mainnet=args.approve_mainnet,
        hold=args.hold,
        quiet=args.quiet,
    )
    return config, args.summary_json


def parse_serve(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m swarm serve",
        description="Host races for the web UI, one at a time, in simulation only.",
    )
    parser.add_argument("--api", default="http://127.0.0.1:8540", help="Arsune API base URL")
    parser.add_argument("--state-host", default="127.0.0.1")
    parser.add_argument("--state-port", type=int, default=8590)
    parser.add_argument(
        "--demo-on-start", action="store_true", help="start one demo race (simulate --setup)"
    )
    parser.add_argument(
        "--site", default="http://127.0.0.1:8541/shop/", help="demo race: bounty site URL"
    )
    parser.add_argument("--hunters", type=int, default=100, help="demo race: scripted hunters")
    parser.add_argument("--scouts", type=int, default=10, help="demo race: scripted scouts")
    parser.add_argument(
        "--budget",
        type=int,
        default=SIMULATION_AMOUNTS["budget"],
        help="demo race: bounty funding, token units",
    )
    parser.add_argument("--browser-pages", type=int, default=6, help="shared Chromium page pool")
    parser.add_argument(
        "--ai-budget",
        type=float,
        default=UI_AI_BUDGET_USD,
        help=f"USD for the model calls of one AI race (at most {UI_AI_BUDGET_USD})",
    )
    parser.add_argument(
        "--ai-races-per-day",
        type=int,
        default=AI_RACES_PER_DAY,
        help="UI races per UTC day that get an AI hunter",
    )
    parser.add_argument("--quiet", action="store_true", help="do not print events")
    return parser.parse_args(argv)


def main_serve(argv: list[str]) -> int:
    args = parse_serve(argv)
    demo = None
    if args.demo_on_start:
        demo = Config(
            api_url=args.api,
            site_url=args.site,
            setup=True,
            hunters=args.hunters,
            scouts=args.scouts,
            budget=args.budget,
            browser_pages=args.browser_pages,
        )

    async def main_loop():
        host = RaceHost(
            args.api,
            model=available_model(),
            ai_cap_usd=args.ai_budget,
            ai_races_per_day=args.ai_races_per_day,
            browser_pages=args.browser_pages,
            quiet=args.quiet,
        )
        await serve(host, args.state_host, args.state_port, demo)

    try:
        asyncio.run(main_loop())
    except OSError as exc:
        print(f"swarm serve: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["serve"]:
        return main_serve(argv[1:])
    config, summary_path = parse(argv)
    try:
        summary = asyncio.run(run(config))
    except SwarmError as exc:
        print(f"swarm: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    if summary_path:
        summary_path.write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
