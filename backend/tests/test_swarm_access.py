"""The AI hunter's access-leak mode: log in as account A, probe, claim per the contract."""

import json
import random

import httpx
from app.settlement.dry import DRY_ACCOUNT, DRY_SALT
from swarm.access import AccessHunt, login_of
from swarm.agents import Copycat, Swarm
from swarm.ai import AiHunter
from swarm.arsune import BountyFeed
from swarm.identity import Identity
from swarm.llm import AiBudget, ModelClient, ModelSettings
from swarm.market import TipBoard
from swarm.site import Page

ORIGIN = "http://shop.test"
SITE = ORIGIN + "/shop/"
LOGIN = {"login_path": "/shop/api/login", "login_body": {"email": "a@x", "password": "pw"}}
SETTLEMENT = {
    "mode": "dry",
    "arsune_account": DRY_ACCOUNT,
    "token": "nep141:usdc.test",
    "intents_contract": "intents.near",
    "salt": DRY_SALT,
}
MODEL = ModelSettings("http://model.test/v1", "sk-test-secret-key", "fake-model", 1.0, 4.0)
APP_JS = "fetch('/shop/api/orders'); const chat = '/shop/api/chat'; // admin: /shop/api/admin"
HOME = '<html><body><main>Shop</main><script src="/shop/static/app.js"></script></body></html>'


class FakeShop:
    """A tiny site: a cookie login and an order API with no owner check."""

    def __init__(self):
        self.requests: list[tuple[str, str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie")
        self.requests.append((request.method, request.url.raw_path.decode(), cookie))
        route = (request.method, request.url.path)
        if route == ("POST", "/shop/api/login"):
            if json.loads(request.content) != LOGIN["login_body"]:
                return httpx.Response(401, json={"detail": "bad login"})
            return httpx.Response(
                200, json={"ok": True}, headers={"set-cookie": "sid=alice; Path=/"}
            )
        if route == ("GET", "/shop/static/app.js"):
            return httpx.Response(200, text=APP_JS)
        if route == ("GET", "/shop/api/orders/1043"):
            if cookie != "sid=alice":
                return httpx.Response(401)
            return httpx.Response(200, json={"id": 1043, "owner": "bob", "note": "secret"})
        return httpx.Response(404)


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
                        "steps": [{"method": "GET", "path": "/shop/api/orders/1043", "body": None}],
                        "why": "orders are read by id",
                    },
                    {  # dropped: DELETE is not allowed
                        "steps": [{"method": "DELETE", "path": "/shop/api/orders/1", "body": None}]
                    },
                    {  # dropped: a POST to a path no page or script names
                        "steps": [{"method": "POST", "path": "/shop/api/secret", "body": {}}]
                    },
                    {"steps": [{"method": "GET", "path": "//evil.test/x", "body": None}]},
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


def hunter(bounty_extra, model=None, limit=0.5, cls=AiHunter):
    api, log, shop = Api(), [], FakeShop()
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
    agent.access_transport = httpx.MockTransport(shop.handler)
    agent.crawl.pages, agent.crawl.frontier = {SITE: Page(SITE, 200, HOME)}, []
    swarm.agents.append(agent)
    return agent, api, shop, model, log


async def test_access_hunt_logs_in_probes_and_claims_exactly_per_contract():
    agent, api, shop, model, log = hunter({"test_account": LOGIN})
    assert agent.access_ready()
    await agent.hunt_access()
    # Login first, then the script, then only the one allowed probe, with A's cookie.
    assert shop.requests == [
        ("POST", "/shop/api/login", None),
        ("GET", "/shop/static/app.js", "sid=alice"),
        ("GET", "/shop/api/orders/1043", "sid=alice"),
    ]
    assert APP_JS in model.users[0] and "Bob" not in model.users[0]
    assert '"owner":"bob"' in model.users[1]
    [(_, tip)] = agent.queue
    assert tip is None
    await agent.claim(*agent.queue.pop(0))
    [body] = api.bodies
    assert body["claim_type"] == "access_leak"
    assert body["urls"] == [ORIGIN + "/shop/api/orders/1043"]
    assert body["details"] == {
        "steps": [{"method": "GET", "path": "/shop/api/orders/1043", "body": None}]
    }
    assert "bond_intent" in body
    assert "hunter-ai-01: order 1043 is Bob's; claiming access_leak" in log
    assert "hunter-ai-01 probes as account A: orders are read by id" in log
    assert agent.access_done and not agent.access_ready()
    await agent.swarm.ai.close()


def test_no_test_account_means_no_access_hunt():
    agent, _, shop, model, _ = hunter({})
    assert login_of(agent.swarm.bounty) is None
    assert agent.access_done and not agent.access_ready()
    assert not shop.requests and not model.users


async def test_a_spent_budget_stops_the_access_hunt():
    agent, _, shop, model, log = hunter({"test_account": LOGIN}, limit=0.000001)
    await agent.hunt_access()
    assert agent.scripted and not agent.queue and not model.users
    assert [r[1] for r in shop.requests] == ["/shop/api/login", "/shop/static/app.js"]
    assert any("AI budget is spent" in line for line in log)
    await agent.swarm.ai.close()


async def test_a_failed_login_skips_the_hunt():
    agent, _, _, model, log = hunter(
        {"test_account": {**LOGIN, "login_body": {"email": "a@x", "password": "wrong"}}}
    )
    await agent.hunt_access()
    assert not model.users and not agent.queue
    assert "could not log in as test account A" in log[-1]
    await agent.swarm.ai.close()


async def test_probes_never_leave_the_origin():
    hunt = AccessHunt(SITE, {"path": "/shop/api/login", "body": {}}, httpx.MockTransport(None))
    status, text = await hunt.send({"method": "GET", "path": "//evil.test/x", "body": None})
    assert status is None and "another origin" in text
    await hunt.close()


def test_a_copycat_never_copies_an_access_leak():
    agent, *_ = hunter({}, cls=Copycat)
    leak = {
        "claim_type": "access_leak",
        "urls": [ORIGIN + "/shop/api/orders/1043"],
        "details": {"steps": [{"method": "GET", "path": "/shop/api/orders/1043", "body": None}]},
        "hunter_account": "someone-else",
    }
    agent.swarm.feed.proven = {"k1": leak}
    assert agent.next_finding() is None
