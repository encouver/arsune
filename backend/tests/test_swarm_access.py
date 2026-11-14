"""The AI hunter's access-leak mode: log in as account A, probe, claim per the contract."""

import json
import random
from types import SimpleNamespace

import httpx
import pytest
from app.browser.session import LoginFailed, Session
from app.settlement.dry import DRY_ACCOUNT, DRY_SALT
from swarm import access
from swarm.access import AccessHunt, login_of, to_step
from swarm.agents import Copycat, Swarm
from swarm.ai import AiHunter
from swarm.arsune import BountyFeed
from swarm.identity import Identity
from swarm.llm import AiBudget, ModelClient, ModelSettings
from swarm.market import TipBoard
from swarm.site import Page

ORIGIN = "https://shop.test.com"
SITE = ORIGIN + "/shop/"
API = "https://api.test.com"
ACCOUNT = {"login_url": ORIGIN + "/shop/login", "email": "a@x", "password": "pw"}
SETTLEMENT = {
    "mode": "dry",
    "arsune_account": DRY_ACCOUNT,
    "token": "nep141:usdc.test",
    "intents_contract": "intents.near",
    "salt": DRY_SALT,
}
MODEL = ModelSettings("http://model.test/v1", "sk-test-secret-key", "fake-model", 1.0, 4.0)
APP_JS = "fetch(API + '/orders'); const chat = '/shop/api/chat'; // admin: /shop/api/admin"
HOME = '<html><body><main>Shop</main><script src="/shop/static/app.js"></script></body></html>'
SESSION = Session({"cookies": [{"name": "sid", "value": "alice"}]}, {}, ORIGIN + "/shop/account")
LEAK = {"id": 1043, "owner": "bob", "note": "secret"}


class FakeShop:
    """Fakes for the shared login and replay, and a tiny Chromium that browses the shop."""

    def __init__(self, password="pw"):
        self.password = password
        self.log: list = []  # what the hunter did, in order

    async def login_session(self, browser, login_url, email, password, base):
        self.log.append(("login", login_url, base))
        if password != self.password:
            raise LoginFailed("still on the login page after submit")
        return SESSION

    async def replay_steps(self, browser, session, steps, base, origin):
        assert session is SESSION and base == "test.com" and origin == ORIGIN
        self.log.append(("replay", [(s["method"], s["url"]) for s in steps]))
        return [
            {"method": s["method"], "url": s["url"], "status": 200, "body": json.dumps(LEAK)}
            if s["url"] == API + "/orders/1043"
            else {
                "method": s["method"],
                "url": s["url"],
                "status": None,
                "body": "",
                "error": "The step did not finish.",
            }
            for s in steps
        ]

    # --- the fake browser
    async def new_context(self, storage_state=None):
        assert storage_state == SESSION.storage_state
        self.log.append(("context",))
        return FakeContext(self)


class FakeContext:
    def __init__(self, shop):
        self.shop = shop
        self.request = self

    async def new_page(self):
        return FakePage(self.shop)

    async def get(self, url, timeout=None):
        self.shop.log.append(("script", url))
        return SimpleNamespace(body=_async(APP_JS.encode()))

    async def close(self):
        pass


def _async(value):
    async def get():
        return value

    return get


def request(method, url, kind, post_data=None):
    return SimpleNamespace(method=method, url=url, resource_type=kind, post_data=post_data)


class FakePage:
    def __init__(self, shop):
        self.shop, self.handlers, self.url = shop, [], ""

    def on(self, event, handler):
        assert event == "request"
        self.handlers.append(handler)

    async def goto(self, url, **_):
        self.shop.log.append(("goto", url))
        self.url = url
        for item in [
            request("GET", url, "document"),
            request("GET", ORIGIN + "/shop/static/app.js", "script"),
            request("GET", ORIGIN + "/shop/static/logo.png", "image"),
            request("GET", "https://cdn.other.com/lib.js", "script"),
            request("GET", API + "/orders?mine=1", "fetch"),
            request("POST", API + "/cart", "xhr", '{"sku":"x"}'),
            request("GET", "https://tracker.other.com/t", "fetch"),
        ]:
            for handler in self.handlers:
                handler(item)

    async def wait_for_load_state(self, *_, **__):
        pass

    async def content(self):
        return HOME

    async def close(self):
        pass


class FakeModel:
    """Plans probes first, then names P1 as the leak."""

    def __init__(self):
        self.users: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        _system, user = (m["content"] for m in json.loads(request.content)["messages"])
        self.users.append(user)
        if "Answers to your probes" not in user:
            answer = {
                "leaks": [],
                "probes": [
                    {
                        "steps": [{"method": "GET", "url": API + "/orders/1043", "body": None}],
                        "why": "orders are read by id",
                    },
                    {  # dropped: DELETE is not allowed
                        "steps": [{"method": "DELETE", "url": API + "/orders/1", "body": None}]
                    },
                    {  # dropped: a POST to a URL no call, page or script names
                        "steps": [{"method": "POST", "url": API + "/secret", "body": {}}]
                    },
                    {  # dropped: another domain
                        "steps": [{"method": "GET", "url": "https://evil.com/x", "body": None}]
                    },
                    {  # dropped: logout
                        "steps": [{"method": "GET", "url": ORIGIN + "/shop/logout", "body": None}]
                    },
                ],
                "note": "",
            }
        else:
            answer = {"leaks": [{"probe": "P1", "why": "order 1043 is Bob's"}], "probes": []}
        usage = {"prompt_tokens": 1000, "completion_tokens": 200}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(answer)}}], "usage": usage},
        )


class Api:
    def __init__(self):
        self.bodies = []

    async def bond_quote(self, bounty_id, hunter):
        return {"tier": "new", "required_bond": 100_000}

    async def submit_claim(self, bounty_id, body):
        self.bodies.append(body)
        return {"id": body["claim_id"], "ledger": []}


def hunter(bounty_extra, monkeypatch, model=None, limit=0.5, cls=AiHunter, password="pw"):
    api, log, shop = Api(), [], FakeShop(password)
    monkeypatch.setattr(access, "login_session", shop.login_session)
    monkeypatch.setattr(access, "replay_steps", shop.replay_steps)
    model = model or FakeModel()
    budget = AiBudget(limit, MODEL.input_usd_per_million, MODEL.output_usd_per_million)
    swarm = Swarm(
        api,
        BountyFeed(api, "b1"),
        None,
        None,
        TipBoard(lambda agent, text: log.append(text)),
        lambda agent, text: log.append(text),
        SETTLEMENT,
        {"id": "b1", "reward_amount": 1_000_000, "bond_amount": 100_000, "url": SITE}
        | bounty_extra,
        pace=0,
        ai=ModelClient(MODEL, budget, httpx.MockTransport(model.handler)),
    )
    strategy = "ai" if cls is AiHunter else "copycat"
    agent = cls(
        swarm, "hunter-ai-01", Identity.generate("hunter"), strategy, random.Random(1), 300_000
    )
    agent.access_browser = shop
    agent.crawl.pages, agent.crawl.frontier = {SITE: Page(SITE, 200, HOME)}, []
    swarm.agents.append(agent)
    return agent, api, shop, model, log


async def test_access_hunt_logs_in_maps_the_api_probes_and_claims_per_contract(monkeypatch):
    agent, api, shop, model, log = hunter({"test_account": ACCOUNT}, monkeypatch)
    assert agent.access_ready()
    await agent.hunt_access()
    # One login, then the logged-in pages, one in-scope script, then the one allowed probe.
    assert shop.log == [
        ("login", ACCOUNT["login_url"], "test.com"),
        ("context",),
        ("goto", ORIGIN + "/shop/account"),
        ("goto", SITE),
        ("script", ORIGIN + "/shop/static/app.js"),
        ("replay", [("GET", API + "/orders/1043")]),
    ]
    first = model.users[0]
    assert APP_JS in first and "bob" not in first
    # The API map: the app's own in-scope calls, no static assets, no other domains.
    assert f"GET {API}/orders?mine=1" in first and f'POST {API}/cart body: {{"sku":"x"}}' in first
    assert "logo.png" not in first and "tracker.other.com" not in first
    assert "cdn.other.com" not in first
    assert '"owner": "bob"' in model.users[1]
    [(_, tip)] = agent.queue
    assert tip is None
    await agent.claim(*agent.queue.pop(0))
    [body] = api.bodies
    assert body["claim_type"] == "access_leak"
    assert body["urls"] == [API + "/orders/1043"]
    assert body["details"] == {
        "steps": [{"method": "GET", "url": API + "/orders/1043", "body": None}]
    }
    assert "bond_intent" in body
    assert "hunter-ai-01: order 1043 is Bob's; claiming access_leak" in log
    assert "hunter-ai-01 probes as account A: orders are read by id" in log
    assert agent.access_done and not agent.access_ready()
    await agent.swarm.ai.close()


def test_no_test_account_means_no_access_hunt(monkeypatch):
    agent, _, shop, model, _ = hunter({}, monkeypatch)
    assert login_of(agent.swarm.bounty) is None
    assert agent.access_done and not agent.access_ready()
    assert not shop.log and not model.users


def test_the_old_test_account_shape_is_not_usable():
    old = {"login_path": "/shop/api/login", "login_body": {"email": "a@x", "password": "pw"}}
    assert login_of({"test_account": old}) is None
    assert login_of({"test_account": {**ACCOUNT, "login_url": "/shop/login"}}) is None
    assert login_of({"test_account": ACCOUNT}) == ACCOUNT


async def test_a_spent_budget_stops_the_access_hunt(monkeypatch):
    agent, _, shop, model, log = hunter({"test_account": ACCOUNT}, monkeypatch, limit=0.000001)
    await agent.hunt_access()
    assert agent.scripted and not agent.queue and not model.users
    assert not any(entry[0] == "replay" for entry in shop.log)
    assert any("AI budget is spent" in line for line in log)
    await agent.swarm.ai.close()


async def test_a_failed_login_skips_the_hunt(monkeypatch):
    agent, _, shop, model, log = hunter({"test_account": ACCOUNT}, monkeypatch, password="other")
    await agent.hunt_access()
    assert not model.users and not agent.queue
    assert shop.log == [("login", ACCOUNT["login_url"], "test.com")]
    assert log[-1] == (
        "hunter-ai-01 could not log in as test account A "
        "(still on the login page after submit); it skips access probes."
    )
    await agent.swarm.ai.close()


@pytest.mark.parametrize(
    "step, allowed",
    [
        ({"method": "GET", "url": API + "/orders/1", "body": None}, True),
        ({"method": "GET", "path": "/shop/api/orders/1", "body": None}, True),
        ({"method": "GET", "url": "https://evil.com/x", "body": None}, False),
        ({"method": "GET", "url": "https://test.com.evil.com/x", "body": None}, False),
        ({"method": "GET", "url": API + "/account/delete", "body": None}, False),
        ({"method": "GET", "url": ACCOUNT["login_url"] + "?next=/", "body": None}, False),
        ({"method": "POST", "url": API + "/cart", "body": {}}, True),  # the app calls it
        ({"method": "POST", "url": ORIGIN + "/shop/api/chat", "body": {}}, True),  # in a script
        ({"method": "POST", "url": API + "/secret", "body": {}}, False),
        ({"method": "POST", "url": API + "/cart/reset", "body": {}}, False),
    ],
)
def test_probe_safety(step, allowed):
    hunt = AccessHunt(SITE, ACCOUNT, None)
    hunt.calls = {("POST", API + "/cart"): ""}
    hunt.known_text = APP_JS
    parsed = to_step(step, hunt.origin)
    assert parsed is not None and hunt.allowed(parsed) is allowed


def test_a_bare_path_resolves_against_the_bounty_origin():
    step = to_step({"method": "get", "path": "/shop/api/orders/1", "body": {"x": 1}}, ORIGIN)
    assert step == {"method": "GET", "url": ORIGIN + "/shop/api/orders/1", "body": None}
    assert to_step({"method": "GET", "path": "//evil.com/x"}, ORIGIN) is None
    assert to_step({"method": "PUT", "url": API + "/x"}, ORIGIN) is None


def test_a_copycat_never_copies_an_access_leak(monkeypatch):
    agent, *_ = hunter({}, monkeypatch, cls=Copycat)
    leak = {
        "claim_type": "access_leak",
        "urls": [API + "/orders/1043"],
        "details": {"steps": [{"method": "GET", "url": API + "/orders/1043", "body": None}]},
        "hunter_account": "someone-else",
    }
    agent.swarm.feed.proven = {"k1": leak}
    assert agent.next_finding() is None
