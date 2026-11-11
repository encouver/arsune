"""The race host (`python -m swarm serve`): the UI starts and stops simulated races over HTTP.

Arsune is the in-memory copy of its public API from test_swarm.py."""

import asyncio
import json
from collections import Counter

import httpx
import pytest
from swarm.__main__ import parse, parse_serve
from swarm.host import AI_OFF, BUSY, IDLE, RaceHost, available_model, serve
from swarm.runner import DEFAULT_MIX, DEFAULT_SCOUT_MIX, STOPPED, Config
from swarm.state import serve_http
from test_swarm import (
    AI_MODEL,
    ORIGIN,
    PAUSED_REASON,
    REAL,
    SETTLEMENT,
    STATE_KEYS,
    FakeArsune,
    site_handler,
)

BOUNTY = "5b2c1e9e-6a3f-4c1d-9b7a-2f1e0d3c4b5a"
JSON = {"content-type": "application/json"}
# A small, fast race on the in-memory site instead of the UI profile.
FAST = {
    "hunters": 9,
    "scouts": 2,
    "mix": {"links": 4, "careless": 3, "copycat": 2},
    "scout_mix": {"links": 1, "hasty": 1},
    "hunter_wallet": 200_000,
    "pace": 0,
    "poll": 0.02,
    "duration": 30,
    "settle_timeout": 5,
    "seed": 7,
}
HOST_KEYS = {"busy", "bounty_id", "run_id", "ai_available", "ai_model", "ai_max_usd", "agents"}


@pytest.fixture(autouse=True)
def no_model_settings(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ARSUNE_SWARM_MODEL_PROVIDER", "ARSUNE_SWARM_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)


def open_arsune(**bounty) -> FakeArsune:
    """Arsune with an open, funded bounty. The fake names it b1; the host asks for BOUNTY."""
    arsune = FakeArsune(budget=20_000_000)
    arsune.open_bounty()
    arsune.bounty |= {"budget_amount": 20_000_000, "fee_amount": 100_000, **bounty}
    arsune.reads = []
    return arsune


def transport(arsune: FakeArsune, settlement=SETTLEMENT) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        arsune.reads.append(request.url.path)
        if request.url.path == "/api/settlement":
            return httpx.Response(200, json=settlement)
        if BOUNTY in request.url.path and not arsune.bounty.get("known", True):
            return httpx.Response(404, json={"detail": "Bounty not found"})
        url = request.url.copy_with(path=request.url.path.replace(BOUNTY, "b1"))
        return arsune.handler(
            httpx.Request(request.method, url, headers=request.headers, content=request.content)
        )

    return httpx.MockTransport(handler)


def make_host(arsune: FakeArsune, settlement=SETTLEMENT, **kwargs) -> RaceHost:
    return RaceHost(
        "http://arsune.test",
        race=kwargs.pop("race", FAST),
        quiet=True,
        api_transport=transport(arsune, settlement),
        site_transport=httpx.MockTransport(site_handler),
        **kwargs,
    )


async def post(host: RaceHost, path: str, body=None, headers=JSON):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return await host.respond("POST", path, headers, data)


async def get(host: RaceHost, path: str):
    return await host.respond("GET", path, {}, b"")


def held_run(monkeypatch, ignore_stop=False):
    """Replace the race with one that waits for its stop event (or forever). Records configs."""
    from swarm import host as host_module

    configs = []

    async def waiting(config, *transports, state, stop):
        configs.append(config)
        await (asyncio.Event() if ignore_stop else stop).wait()

    monkeypatch.setattr(host_module, "run", waiting)
    return configs


def log_texts(host: RaceHost) -> list[str]:
    return [event["text"] for event in host.state.view()["log"]]


# ---------------------------------------------------------------- GET /host and the profile


async def test_host_view_and_the_ui_race_profile():
    host = RaceHost("http://arsune.test")
    status, view = await get(host, "/host")
    assert status == 200 and set(view) == HOST_KEYS
    assert view == {
        "busy": False,
        "bounty_id": None,
        "run_id": None,
        "ai_available": False,
        "ai_model": None,
        "ai_max_usd": 0.5,
        "agents": {"hunters": 12, "scouts": 3, "ai_hunters": 3, "ai_scouts": 1},
    }
    status, state = await get(host, "/state")
    assert status == 200 and set(state) == STATE_KEYS
    assert state["run_id"] is None and state["finished"] is True and state["agents"] == []

    scripted, with_ai = host.race_config(BOUNTY, False), host.race_config(BOUNTY, True)
    assert (scripted.hunters, scripted.scouts, scripted.ai_hunters, scripted.ai_scouts) == (
        12,
        3,
        0,
        0,
    )
    assert (with_ai.ai_hunters, with_ai.ai_scouts) == (3, 1)
    assert (scripted.pace, scripted.duration, scripted.pages, scripted.browser_pages) == (
        0.5,
        240,
        30,
        6,
    )
    # The default mixes: careless and copycat hunters are part of the demo.
    assert scripted.mix == DEFAULT_MIX and scripted.scout_mix == DEFAULT_SCOUT_MIX
    assert not scripted.live and not scripted.setup and scripted.bounty_id == BOUNTY
    await host.close()


@pytest.mark.parametrize("cap, expected", [(2.0, 0.5), (0.2, 0.2)])
async def test_with_model_settings_ai_is_available_and_the_key_is_never_shown(cap, expected):
    host = RaceHost("http://arsune.test", model=AI_MODEL, ai_cap_usd=cap)
    _, view = await get(host, "/host")
    assert view["ai_available"] is True and view["ai_model"] == "fake-model"
    assert view["ai_max_usd"] == expected == host.race_config(BOUNTY, True).ai_budget
    assert AI_MODEL.api_key not in json.dumps(view)
    await host.close()


def test_ai_is_available_only_when_the_model_settings_validate(monkeypatch, capsys):
    assert available_model() is None
    monkeypatch.setenv("ARSUNE_SWARM_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("ARSUNE_SWARM_MODEL_NAME", "claude-haiku-5-5")
    monkeypatch.setenv("ARSUNE_SWARM_MODEL_INPUT_USD_PER_MILLION", "0.10")
    monkeypatch.setenv("ARSUNE_SWARM_MODEL_OUTPUT_USD_PER_MILLION", "0.50")
    assert available_model() is None  # Claude needs ANTHROPIC_API_KEY
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-host-test")
    assert available_model().name == "claude-haiku-5-5"
    monkeypatch.setenv("ARSUNE_SWARM_MODEL_PROVIDER", "not-a-provider")
    assert available_model() is None
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out and "sk-ant-host-test" not in out


# ---------------------------------------------------------------- POST /races


async def test_a_race_starts_with_202_and_runs_to_its_summary():
    arsune = open_arsune()
    host = make_host(arsune)
    status, body = await post(host, "/races", {"bounty_id": BOUNTY.upper(), "ai_agents": False})
    assert status == 202 and body == {"run_id": host.state.run_id, "bounty_id": BOUNTY}
    _, view = await get(host, "/host")
    assert view["busy"] is True and (view["bounty_id"], view["run_id"]) == (BOUNTY, body["run_id"])
    _, state = await get(host, "/state")
    assert state["bounty_id"] == BOUNTY and state["finished"] is False  # set before any read
    await asyncio.wait_for(host.task, 60)

    _, state = await get(host, "/state")
    assert state["finished"] is True and state["run_id"] == body["run_id"]
    assert len(state["agents"]) == 11 and state["site_check"]["state"] == "passed"
    outcomes = Counter(c["outcome"] for c in arsune.claims)
    assert outcomes["proven"] == len(REAL)
    assert any(text.startswith("Summary:") for text in log_texts(host))
    assert (await get(host, "/host"))[1]["busy"] is False
    await host.close()


@pytest.mark.parametrize(
    "body, headers",
    [
        (b"not json", JSON),
        (b"[]", JSON),
        (b"{}", JSON),
        ({"bounty_id": "b1"}, JSON),
        ({"bounty_id": 7}, JSON),
        ({"bounty_id": BOUNTY, "ai_agents": "yes"}, JSON),
        ({"bounty_id": BOUNTY, "mode": "live"}, JSON),
        ({"bounty_id": BOUNTY}, {"content-type": "text/plain"}),
        ({"bounty_id": BOUNTY}, {}),
    ],
)
async def test_a_bad_body_is_refused_with_400(body, headers):
    arsune = open_arsune()
    host = make_host(arsune)
    status, answer = await post(host, "/races", body, headers)
    assert status == 400 and set(answer) == {"detail"} and answer["detail"].endswith(".")
    assert not host.busy and arsune.reads == []
    await host.close()


async def test_ai_agents_are_refused_when_the_server_has_no_model():
    arsune = open_arsune()
    host = make_host(arsune)
    status, answer = await post(host, "/races", {"bounty_id": BOUNTY, "ai_agents": True})
    assert (status, answer) == (400, {"detail": AI_OFF})
    assert AI_OFF == "AI agents are not configured on this server."
    assert not host.busy and arsune.reads == []
    await host.close()


async def test_a_second_race_is_refused_while_one_runs(monkeypatch):
    held_run(monkeypatch)
    host = make_host(open_arsune())
    first, second = await asyncio.gather(
        post(host, "/races", {"bounty_id": BOUNTY}), post(host, "/races", {"bounty_id": BOUNTY})
    )
    assert sorted([first[0], second[0]]) == [202, 409]
    assert {"detail": BUSY} in (first[1], second[1])
    assert BUSY == "A race is already running."
    await host.close()


@pytest.mark.parametrize(
    "bounty, status, detail",
    [
        (
            {"status": "paused", "paused_reason": PAUSED_REASON},
            409,
            f"The bounty is paused, not open: {PAUSED_REASON}",
        ),
        ({"status": "closed"}, 409, "The bounty is closed, not open."),
        ({"status": "pending_proof"}, 409, "The bounty is pending_proof, not open."),
        ({"budget_amount": 1_099_999}, 409, "The bounty has no funds left"),
        ({"known": False}, 404, f"Arsune has no bounty {BOUNTY}."),
    ],
)
async def test_a_bounty_that_arsune_does_not_show_open_and_funded_is_refused(
    bounty, status, detail
):
    arsune = open_arsune(**bounty)
    host = make_host(arsune)
    answer = await post(host, "/races", {"bounty_id": BOUNTY})
    assert answer[0] == status and answer[1]["detail"].startswith(detail)
    assert not host.busy and arsune.reads == [f"/api/bounties/{BOUNTY}"]
    await host.close()


async def test_arsune_down_is_a_502():
    def down(request):
        raise httpx.ConnectError("refused")

    host = RaceHost("http://arsune.test", api_transport=httpx.MockTransport(down))
    answer = await post(host, "/races", {"bounty_id": BOUNTY})
    assert answer == (502, {"detail": "Arsune's API did not answer."}) and not host.busy
    await host.close()


async def test_unknown_paths_and_methods():
    host = RaceHost("http://arsune.test")
    assert (await get(host, "/nope"))[0] == 404
    assert (await get(host, "/races"))[0] == 405
    assert (await post(host, "/state", {}))[0] == 405
    await host.close()


# ---------------------------------------------------------------- POST /races/stop


async def test_stop_without_a_race_is_409():
    host = RaceHost("http://arsune.test")
    assert await post(host, "/races/stop", b"", {}) == (409, {"detail": IDLE})
    await host.close()


async def test_stop_ends_a_running_race_cleanly_and_a_new_race_can_start():
    arsune = open_arsune()
    # Real think time keeps the agents busy until the stop.
    host = make_host(arsune, race={**FAST, "pace": 1.0, "duration": 60, "settle_timeout": 30})
    status, started = await post(host, "/races", {"bounty_id": BOUNTY})
    assert status == 202
    while not host.state.agents:
        await asyncio.sleep(0.02)
    status, stopped = await post(host, "/races/stop", b"", {})
    assert status == 202 and stopped == started
    await asyncio.wait_for(host.task, 30)
    texts = log_texts(host)
    assert f"The swarm stops: {STOPPED}." in texts and STOPPED == "the race was stopped on request"
    assert host.state.finished and not host.busy
    assert all(agent["state"] in {"done", "broke"} for agent in host.state.view()["agents"])
    assert (await post(host, "/races", {"bounty_id": BOUNTY}))[0] == 202
    host.stop()  # before the race's first step
    await asyncio.gather(host.task, return_exceptions=True)
    assert log_texts(host)[-1] == "The race was stopped before any agent started."
    assert host.state.finished and not host.busy
    await host.close()


async def test_stop_before_any_agent_starts_cancels_the_setup(monkeypatch):
    held_run(monkeypatch, ignore_stop=True)  # for example, a site check that takes long
    host = make_host(open_arsune())
    assert (await post(host, "/races", {"bounty_id": BOUNTY}))[0] == 202
    await asyncio.sleep(0.05)
    assert (await post(host, "/races/stop", b"", {}))[0] == 202
    await asyncio.wait_for(asyncio.gather(host.task, return_exceptions=True), 5)
    assert not host.busy and host.state.finished
    assert log_texts(host)[-1] == "The race was stopped before any agent started."
    await host.close()


# ---------------------------------------------------------------- safety: simulation only


async def test_live_mode_can_never_be_started(monkeypatch):
    arsune = open_arsune()
    host = make_host(arsune)
    for field in ({"mode": "live"}, {"live": True}, {"i_approve_mainnet": True}):
        status, _ = await post(host, "/races", {"bounty_id": BOUNTY, **field})
        assert status == 400
    configs = held_run(monkeypatch)
    host.launch(Config(live=True, approve_mainnet=True, setup=True, hold=True, state_port=8590))
    await asyncio.sleep(0)
    assert configs[0].live is False and configs[0].approve_mainnet is False
    assert configs[0].state_port is None and configs[0].hold is False
    assert host.state.mode == "simulation"
    host.stop()
    await host.close()


async def test_a_host_race_refuses_an_arsune_that_moves_real_money():
    arsune = open_arsune()
    host = make_host(arsune, settlement={**SETTLEMENT, "mode": "near_intents"})
    assert (await post(host, "/races", {"bounty_id": BOUNTY}))[0] == 202
    await asyncio.wait_for(host.task, 10)
    assert host.state.finished and host.state.agents == [] and arsune.claims == []
    assert log_texts(host)[-1] == (
        "The race did not start: Simulation needs Arsune in dry settlement mode; "
        "Arsune reports 'near_intents'."
    )
    await host.close()


async def test_a_failed_site_check_ends_the_race_with_the_refusal_on_the_board():
    arsune = open_arsune()
    arsune.checkable = False
    host = make_host(arsune)
    assert (await post(host, "/races", {"bounty_id": BOUNTY}))[0] == 202
    await asyncio.wait_for(host.task, 10)
    state = host.state.view()
    assert state["finished"] and state["agents"] == [] and arsune.claims == []
    assert state["site_check"]["state"] == "failed"
    assert log_texts(host)[-1].startswith("No agent starts: Arsune cannot check this site")
    await host.close()


# ---------------------------------------------------------------- the server and the CLI


async def test_the_server_reads_json_and_chunked_bodies(monkeypatch):
    held_run(monkeypatch)
    host = make_host(open_arsune())
    server = await serve_http(host.respond, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"

    async def chunks():
        yield b'{"bounty_id": '
        yield json.dumps(BOUNTY).encode() + b"}"

    try:
        async with httpx.AsyncClient(base_url=base) as client:
            state = await client.get("/state")
            started = await client.post("/races", json={"bounty_id": BOUNTY})
            busy = await client.post("/races", content=chunks(), headers=JSON)
            stopped = await client.post("/races/stop")
            missing = await client.get("/missing")
    finally:
        server.close()
    assert state.status_code == 200 and state.headers["access-control-allow-origin"] == "*"
    assert started.status_code == 202 and started.json()["bounty_id"] == BOUNTY
    assert "access-control-allow-origin" not in started.headers
    assert busy.status_code == 409 and busy.json() == {"detail": BUSY}
    assert stopped.status_code == 202 and missing.status_code == 404
    await host.close()


async def test_demo_on_start_runs_the_setup_race_and_shows_its_bounty_at_once():
    arsune = FakeArsune(budget=20_000_000)
    arsune.reads = []
    host = make_host(arsune)
    demo = Config(api_url="http://arsune.test", site_url=ORIGIN + "/site/", setup=True, **FAST)
    serving = asyncio.create_task(serve(host, "127.0.0.1", 0, demo))
    while host.task is None:
        await asyncio.sleep(0.01)
    while host.state.bounty_id is None:
        await asyncio.sleep(0.01)
    assert host.state.bounty_id == "b1" and host.busy
    await asyncio.wait_for(host.task, 60)
    assert arsune.funded and Counter(c["outcome"] for c in arsune.claims)["proven"] == len(REAL)
    assert host.state.finished
    serving.cancel()
    await asyncio.gather(serving, return_exceptions=True)


def test_serve_takes_the_compose_flags_and_simulate_parses_as_before():
    args = parse_serve(
        [
            "--demo-on-start",
            "--api=http://api:8540",
            "--site=http://demo:8541/shop/",
            "--hunters=100",
            "--scouts=10",
            "--budget=80000000",
            "--state-host=0.0.0.0",
        ]
    )
    assert args.demo_on_start and (args.hunters, args.scouts, args.budget) == (100, 10, 80_000_000)
    assert (args.state_host, args.state_port, args.ai_budget) == ("0.0.0.0", 8590, 0.5)
    assert not hasattr(args, "approve_mainnet")
    config, _ = parse(["simulate", "--setup", "--hunters=5"])
    assert config.setup and config.hunters == 5 and not config.live
    with pytest.raises(SystemExit):
        parse(["serve"])  # serve is its own command, not a mode of simulate/live
