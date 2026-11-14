"""Anonymous swarm probes use local proof and do not send writes or private requests."""

import base64
import json
from types import SimpleNamespace

import pytest
from app.bounty.claims import fingerprint, secret_fingerprint
from app.browser.policy import TargetPolicyError
from swarm import security
from swarm.security import SecurityHunt
from swarm.site import Page

SITE = "https://shop.example.com/shop/"
API = "https://project.supabase.co"
ANON = ".".join(
    [
        "eyJhbGciOiJIUzI1NiJ9",
        base64.urlsafe_b64encode(b'{"role":"anon"}').decode().rstrip("="),
        "abcdefghijklmno",
    ]
)
SECRET = ".".join(
    [
        "eyJhbGciOiJIUzI1NiJ9",
        base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("="),
        "abcdefghijklmno",
    ]
)
PEOPLE = json.dumps([{"email": f"person{i}@mailbox.example"} for i in range(3)]).encode()


class FakeBrowser:
    def __init__(self):
        self.contexts = []
        self.sent = []
        self.replies = {
            SITE: (200, b'<script src="/shop/app.js"></script>'),
            SITE + "app.js": (
                200,
                f'const host="{API}";const key="{ANON}";import("./chunk.js");'.encode(),
            ),
            SITE + "chunk.js": (200, f'const secret="{SECRET}";'.encode()),
            API + "/rest/v1/customers?select=*": (200, PEOPLE),
            "https://shop.example.com/": (200, b"Public home"),
            API + "/": (404, b"Missing"),
            SITE + ".env.backup": (
                200,
                b"DB_PASSWORD=hidden\nAPI_KEY=secret\nPRIVATE_HOST=internal\n",
            ),
        }

    async def new_context(self, **options):
        assert "storage_state" not in options
        context = FakeContext(self)
        self.contexts.append(context)
        return context


class FakeContext:
    def __init__(self, browser):
        self.browser = browser
        self.request = self
        self.closed = False

    async def route(self, pattern, handler):
        self.guard = handler

    async def route_web_socket(self, *args):
        pass

    async def fetch(self, url, **options):
        self.browser.sent.append((url, options))
        status, body = self.browser.replies.get(url, (404, b"Missing"))
        headers = {}
        if isinstance(body, dict):
            headers, body = body, b""

        async def read():
            return body

        async def dispose():
            pass

        return SimpleNamespace(status=status, headers=headers, body=read, dispose=dispose)

    async def new_page(self):
        return FakePage(self.browser)

    async def close(self):
        self.closed = True


class FakePage:
    def __init__(self, browser):
        self.browser = browser
        self.url = ""
        self.handler = None

    def on(self, event, handler):
        assert event == "request"
        self.handler = handler

    async def goto(self, url, **options):
        self.url = url
        self.handler(
            SimpleNamespace(
                url=SITE + "api/customers?mine=1",
                method="GET",
                resource_type="fetch",
                post_data=None,
            )
        )

    async def wait_for_timeout(self, milliseconds):
        pass

    async def content(self):
        return self.browser.replies[SITE][1].decode()

    async def close(self):
        pass


@pytest.fixture
def browser(monkeypatch):
    async def validate(url):
        if "127.0.0.1" in url or "private.example.com" in url:
            raise TargetPolicyError("private")
        return url

    monkeypatch.setattr(security, "validate_target", validate)
    return FakeBrowser()


def plan(kind, details):
    return {"type": kind, "details": details}


@pytest.mark.asyncio
async def test_gather_scripts_chunks_anon_key_and_api_calls(browser):
    hunt = SecurityHunt(SITE, browser)
    await hunt.gather([Page(SITE, 200)])
    assert SITE + "chunk.js" in hunt.scripts
    assert API in hunt.baas
    assert ANON in hunt.public_keys
    assert SECRET not in hunt.context
    assert "supabase_service_role" in hunt.context
    assert "api/customers?mine=1" in hunt.context
    assert all(context.closed for context in browser.contexts)


@pytest.mark.asyncio
async def test_all_three_kinds_require_local_proof_and_mask_evidence(browser):
    hunt = SecurityHunt(SITE, browser)
    await hunt.gather([])
    steps = [
        {
            "method": "GET",
            "url": API + "/rest/v1/customers?select=*",
            "headers": {"apikey": ANON},
            "body": None,
        }
    ]
    plans = hunt.plans(
        {
            "probes": [
                plan("data_exposure", {"steps": steps}),
                plan("secret_key", {"url": SITE + "chunk.js", "kind": "supabase_service_role"}),
                plan("private_file", {"url": SITE + ".env.backup", "kind": "env"}),
            ]
        }
    )
    findings = [await hunt.run(p) for p in plans]
    assert [f.claim_type for f in findings] == ["data_exposure", "secret_key", "private_file"]
    exposure, secret, private = findings
    assert exposure.urls == [API + "/rest/v1/customers"]
    assert exposure.key == fingerprint(exposure.claim_type, exposure.urls, exposure.details)
    import hashlib

    assert secret.key == secret_fingerprint(
        "supabase_service_role", hashlib.sha256(SECRET.encode()).hexdigest()
    )
    assert private.key == fingerprint(private.claim_type, private.urls, private.details)
    evidence = " ".join(f.evidence + f.explanation for f in findings)
    assert SECRET not in evidence and "person0@" not in evidence and "hidden" not in evidence
    assert all(context.closed for context in browser.contexts)
    assert all(
        options["max_redirects"] == 0 and options["timeout"] == 10_000
        for _, options in browser.sent
    )


@pytest.mark.asyncio
async def test_reject_public_data_bad_formats_missing_and_large_files(browser):
    hunt = SecurityHunt(SITE, browser)
    await hunt.gather([])
    browser.replies["https://shop.example.com/"] = (200, PEOPLE)
    exposure = plan(
        "data_exposure", {"steps": [{"method": "GET", "url": API + "/rest/v1/customers?select=*"}]}
    )
    assert await hunt.run(exposure) is None
    for body in [b"<html>A=B\nC=D\nE=F</html>", b"A=B", b"A=B\nC=D\nE=F\n" + b"x" * (1024 * 1024)]:
        browser.replies[SITE + ".env.backup"] = (200, body)
        assert (
            await hunt.run(plan("private_file", {"url": SITE + ".env.backup", "kind": "env"}))
            is None
        )
    assert (
        await hunt.run(plan("secret_key", {"url": SITE + "missing.js", "kind": "stripe_secret"}))
        is None
    )


@pytest.mark.asyncio
async def test_redirect_scope_private_hosts_and_caps(browser):
    hunt = SecurityHunt(SITE, browser)
    browser.replies[SITE + "redirect"] = (302, {"location": "https://other.supabase.co/leak"})
    browser.replies[SITE + "loop"] = (302, {"location": SITE + "loop"})
    for url in [SITE + "redirect", SITE + "loop", "https://private.example.com/leak"]:
        assert await hunt.run(plan("private_file", {"url": url, "kind": "env"})) is None
    assert not any(
        "other.supabase.co" in url or "private.example.com" in url for url, _ in browser.sent
    )
    assert sum(url.endswith("loop") for url, _ in browser.sent) == 4


def test_only_observed_read_only_posts_and_public_auth(browser):
    hunt = SecurityHunt(SITE, browser)
    query = {
        "method": "POST",
        "url": SITE + "graphql",
        "body": {"query": "query { customers { id } }"},
    }
    assert not hunt.allowed(query)
    hunt.calls[("POST", SITE + "graphql")] = {}
    assert hunt.allowed(query)
    assert not hunt.allowed({**query, "body": {"query": "mutation { deleteCustomer(id: 1) }"}})
    hunt.calls[("POST", SITE + "checkout")] = {}
    assert not hunt.allowed({**query, "url": SITE + "checkout"})
    assert not hunt.allowed({"method": "GET", "url": SITE, "headers": {"Cookie": "sid=alice"}})
    assert not hunt.allowed(
        {"method": "GET", "url": SITE, "headers": {"Authorization": "Bearer secret"}}
    )
    assert not hunt.scoped("https://unrelated.supabase.co/")


@pytest.mark.asyncio
async def test_ai_act_without_account_plans_runs_and_queues_all_proofs(browser, monkeypatch):
    from test_swarm_access import hunter

    agent, _, _, _, log = hunter({}, monkeypatch)
    agent.swarm.bounty["url"] = SITE
    agent.crawl.pages = {SITE: Page(SITE, 200)}
    agent.access_browser = browser
    prompts = []

    async def ask(system, user, tokens):
        prompts.append(user)
        return {
            "probes": [
                plan(
                    "data_exposure",
                    {
                        "steps": [
                            {
                                "method": "GET",
                                "url": API + "/rest/v1/customers?select=*",
                                "headers": {"apikey": ANON},
                            }
                        ]
                    },
                ),
                plan("secret_key", {"url": SITE + "chunk.js", "kind": "supabase_service_role"}),
                plan("private_file", {"url": SITE + ".env.backup", "kind": "env"}),
            ]
            if len(prompts) == 1
            else [],
            "note": "",
        }

    agent.swarm.ai.ask = ask
    assert agent.access_done and not agent.security_done
    assert await agent.act()
    assert agent.security_done
    assert {f.claim_type for f, _ in agent.queue} == {"data_exposure", "secret_key", "private_file"}
    assert SECRET not in " ".join(prompts + log)
    assert PEOPLE.decode() not in " ".join(prompts + log)
    assert len(prompts) == 2
    await agent.swarm.ai.close()


@pytest.mark.asyncio
async def test_shared_budget_exhaustion_stops_anonymous_model_calls(browser, monkeypatch):
    from test_swarm_access import hunter

    agent, _, _, model, _ = hunter({}, monkeypatch, limit=1e-9)
    agent.swarm.bounty["url"] = SITE
    agent.access_browser = browser
    await agent.hunt_security()
    assert agent.scripted and agent.security_done and not agent.queue
    assert not model.users and agent.swarm.ai.budget.exhausted
    await agent.swarm.ai.close()


def test_malformed_plans_and_literal_public_keys(browser):
    hunt = SecurityHunt(SITE, browser)
    hunt.read_script(SITE + "app.js", 'const anon_key="public-demo-key-123";')
    assert "public-demo-key-123" in hunt.public_keys
    hunt.read_script(SITE + "bad.js", f'const apikey="{SECRET}";')
    assert SECRET not in hunt.public_keys
    assert not hunt.scoped(None)
    assert not hunt.scoped("https://shop.example.com:bad/")
    assert not hunt.plans(
        {
            "probes": [
                None,
                {},
                {"type": [], "details": {}},
                plan("secret_key", {"url": [], "kind": "stripe_secret"}),
                plan("secret_key", {"url": SITE, "kind": []}),
                plan(
                    "data_exposure",
                    {"steps": [{"method": "GET", "url": SITE, "headers": {"apikey": SECRET}}]},
                ),
            ]
        }
    )


def test_aws_secret_is_removed_from_model_text():
    access = "AKIA" + "A" * 16
    secret = "aB1/" * 10
    text = security.safe_text(f'const key="{access}";const secret="{secret}";')
    assert access not in text and secret not in text


@pytest.mark.asyncio
async def test_baas_redirect_only_allowed_for_data_exposure(browser):
    hunt = SecurityHunt(SITE, browser)
    hunt.baas.add(API)
    browser.replies[SITE + "secret.js"] = (302, {"location": API + "/secret.js"})
    browser.replies[API + "/secret.js"] = (200, SECRET.encode())
    assert (
        await hunt.run(
            plan("secret_key", {"url": SITE + "secret.js", "kind": "supabase_service_role"})
        )
        is None
    )
    assert not any(url == API + "/secret.js" for url, _ in browser.sent)


@pytest.mark.asyncio
async def test_gather_closes_context_on_error(browser, monkeypatch):
    async def fail(self, pages):
        raise RuntimeError("page failed")

    monkeypatch.setattr(SecurityHunt, "_gather", fail)
    hunt = SecurityHunt(SITE, browser)
    with pytest.raises(RuntimeError):
        await hunt.gather([])
    assert browser.contexts[-1].closed and hunt.session is None


@pytest.mark.asyncio
async def test_route_preserves_script_mime_and_public_api_headers(browser):
    hunt = SecurityHunt(SITE, browser)
    await hunt.gather([])
    context = browser.contexts[0]
    results = []

    async def fulfill(**options):
        results.append(options)

    async def abort():
        results.append("blocked")

    hunt.session = context
    for url, kind, headers in [
        (SITE + "app.js", "script", {}),
        (API + "/rest/v1/customers?select=*", "fetch", {"apikey": ANON}),
    ]:
        request = SimpleNamespace(url=url, method="GET", resource_type=kind, headers=headers)
        await context.guard(SimpleNamespace(request=request, fulfill=fulfill, abort=abort))
    assert results[0]["content_type"] == "text/javascript"
    assert results[1]["status"] == 200
    assert browser.sent[-1][1]["headers"] == {"apikey": ANON}


@pytest.mark.asyncio
async def test_every_local_proof_is_confirmed_by_shared_checker(browser, monkeypatch):
    from app.browser import checker

    monkeypatch.setattr(checker, "validate_target", security.validate_target)
    hunt = SecurityHunt(SITE, browser)
    await hunt.gather([])
    plans = hunt.plans(
        {
            "probes": [
                plan(
                    "data_exposure",
                    {
                        "steps": [
                            {
                                "method": "GET",
                                "url": API + "/rest/v1/customers?select=*",
                                "headers": {"apikey": ANON},
                            }
                        ]
                    },
                ),
                plan("secret_key", {"url": SITE + "chunk.js", "kind": "supabase_service_role"}),
                plan("private_file", {"url": SITE + ".env.backup", "kind": "env"}),
            ]
        }
    )
    for probe in plans:
        finding = await hunt.run(probe)
        check = getattr(checker, "check_" + finding.claim_type)
        context = await browser.new_context()
        status, _, _ = await check(
            finding.body(), context, hunt.base, "https://shop.example.com", SITE
        )
        assert status == "confirmed"
        await context.close()


def test_script_discovery_ignores_malformed_urls(browser):
    hunt = SecurityHunt(SITE, browser)
    hunt.read_script(
        SITE + "app.js",
        'const bad="https://[";const port="https://x.supabase.co:wrong/";import("https://[/file.js")',
    )
    assert not hunt.baas and not hunt.known_scripts


@pytest.mark.asyncio
async def test_inline_host_and_key_known_before_browser_execution(browser):
    hunt = SecurityHunt(SITE, browser)
    await hunt.gather([])
    context = browser.contexts[0]
    hunt.baas.clear()
    hunt.public_keys.clear()
    browser.replies[SITE] = (
        200,
        f'<script>const host="{API}";const key="{ANON}";fetch(host);</script>'.encode(),
    )
    executed = []

    async def fulfill(**options):
        # Fulfilling the document lets the browser execute its scripts.
        executed.append(True)
        assert API in hunt.baas and ANON in hunt.public_keys

    async def abort():
        pytest.fail("document was blocked")

    hunt.session = context
    request = SimpleNamespace(url=SITE, method="GET", resource_type="document", headers={})
    await context.guard(SimpleNamespace(request=request, fulfill=fulfill, abort=abort))
    assert executed
    # Model sees a page URL that it can use to prove an inline secret.
    browser.replies[SITE] = (200, f'<script>const key="{SECRET}";</script>'.encode())
    hunt.session = None
    await hunt.gather([])
    assert "#inline" not in hunt.context
    assert "Script " + SITE in hunt.context


@pytest.mark.asyncio
async def test_browser_failure_keeps_other_hunter_work_available(browser, monkeypatch):
    from playwright.async_api import Error as BrowserError
    from test_swarm_access import hunter

    agent, _, _, _, log = hunter({}, monkeypatch)
    agent.access_browser = browser

    async def fail(**options):
        raise BrowserError("browser unavailable")

    monkeypatch.setattr(browser, "new_context", fail)
    await agent.hunt_security()
    assert agent.security_done and not agent.scripted
    assert "browser did not finish" in log[-1]
    await agent.swarm.ai.close()
