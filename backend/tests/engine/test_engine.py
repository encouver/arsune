import copy
import json
import os
import subprocess
import sys
import time

import httpx
import pytest
from app.agents.candidates import scripted, validate_candidates
from app.agents.model import ModelConfigurationError, generate_candidates
from app.browser.policy import TargetPolicyError, validate_target
from app.core.scan import VIEWPORTS
from app.workers.workflow import run_scan
from ports import free_port

PORT = free_port()


@pytest.fixture(scope="module", autouse=True)
def demo_server():
    from app.core.config import settings

    old_demo = settings.demo_url
    settings.demo_url = f"http://127.0.0.1:{PORT}"
    environment = {**os.environ, "ARSUNE_DEMO_PORT": str(PORT)}
    with subprocess.Popen(
        [sys.executable, "-m", "app.demo"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    ) as process:
        for _ in range(50):
            try:
                if httpx.get(f"http://127.0.0.1:{PORT}/faulty/", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            process.terminate()
            raise RuntimeError("Demo did not start.")
        yield
        process.terminate()
        process.wait(timeout=5)
        settings.demo_url = old_demo


async def test_real_browser_faults_rejection_cap_and_corrected(demo_server, tmp_path):
    snapshots = []
    report = await run_scan(
        f"http://127.0.0.1:{PORT}/faulty/",
        tmp_path / "faulty",
        inject_demo_false_positive=True,
        save_checkpoint=lambda state: snapshots.append(copy.deepcopy(state)),
    )
    assert len(report["inspected_pages"]) == 5
    assert {f["claim_type"] for f in report["confirmed"]} == {
        "broken_link",
        "horizontal_overflow",
        "price_mismatch",
    }
    assert any(f["injected"] and f["status"] == "rejected" for f in report["findings"])
    assert {a["role"] for a in report["activity"]} >= {
        "navigation",
        "mobile",
        "consistency",
        "checker",
    }
    for finding in report["findings"]:
        assert finding["reproduction_steps"]
        for evidence in finding["evidence"]:
            assert (tmp_path / "faulty" / evidence["screenshot"]).is_file()
    assert all(o["url"] in report["inspected_pages"] for o in report["observations"])
    resumed = await run_scan(
        f"http://127.0.0.1:{PORT}/faulty/",
        tmp_path / "faulty",
        inject_demo_false_positive=True,
        checkpoint=snapshots[-1],
    )
    assert [f["id"] for f in resumed["findings"]] == [f["id"] for f in report["findings"]]
    assert resumed["coverage"]["requests"] == report["coverage"]["requests"]
    corrected = await run_scan(f"http://127.0.0.1:{PORT}/corrected/", tmp_path / "corrected")
    assert corrected["confirmed"] == []
    (tmp_path / "faulty-report.json").write_text(json.dumps(report, indent=2))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:secret@example.com",
        "http://127.0.0.1:8542/",
        f"http://localhost:{PORT}/",
        "http://169.254.169.254/",
        "http://[::1]/",
    ],
)
async def test_policy_rejects_private_and_unsafe(url):
    with pytest.raises(TargetPolicyError):
        await validate_target(url)


async def test_policy_demo_exact_origin():
    assert (
        await validate_target(f"http://127.0.0.1:{PORT}/faulty/#here")
        == f"http://127.0.0.1:{PORT}/faulty/"
    )


async def test_browser_redirect_and_subresource_policy(demo_server, tmp_path):
    redirect = await run_scan(
        f"http://127.0.0.1:{PORT}/faulty/redirect-private", tmp_path / "redirect"
    )
    assert redirect["confirmed"] == []
    assert "http://127.0.0.1:8542/internal" in redirect["coverage"]["blocked_requests"]
    assert redirect["coverage"]["incomplete_pages"]
    resource = await run_scan(
        f"http://127.0.0.1:{PORT}/faulty/resource-policy", tmp_path / "resource"
    )
    assert "http://127.0.0.1:8542/internal" in resource["coverage"]["blocked_requests"]


def test_failed_navigation_is_not_a_fault():
    assert (
        scripted(
            [
                {
                    "url": "https://example.com/",
                    "viewport": {"width": 1280, "height": 900},
                    "links": ["https://example.com/fail"],
                    "facts": [],
                },
                {
                    "url": "https://example.com/fail",
                    "viewport": {"width": 1280, "height": 900},
                    "status": None,
                    "links": [],
                    "facts": [],
                },
            ]
        )
        == []
    )


async def test_provider_transport_and_budget(monkeypatch):
    from app.core.config import settings

    for key, value in {
        "model_base_url": "https://model.test/v1",
        "model_api_key": "server-secret",
        "model_name": "test-model",
        "model_max_output_tokens": 100,
        "model_cost_ceiling_usd": 0.25,
        "model_input_usd_per_million": 1.0,
        "model_output_usd_per_million": 1.0,
    }.items():
        monkeypatch.setattr(settings, key, value)
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer server-secret"
        assert json.loads(request.content)["max_tokens"] == 100
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]}
        )

    candidates, provider = await generate_candidates([], transport=httpx.MockTransport(respond))
    assert candidates == [] and provider["live_call"] is False
    assert len(calls) == 1
    monkeypatch.setattr(settings, "model_cost_ceiling_usd", 0.00000001)
    with pytest.raises(ModelConfigurationError):
        await generate_candidates([], transport=httpx.MockTransport(respond))
    assert len(calls) == 1
    monkeypatch.setattr(settings, "model_api_key", "")
    with pytest.raises(ModelConfigurationError):
        await generate_candidates([], transport=httpx.MockTransport(respond))


def _provider_settings(monkeypatch):
    from app.core.config import settings

    for key, value in {
        "model_base_url": "https://model.test/v1",
        "model_api_key": "server-secret",
        "model_name": "test-model",
        "model_max_output_tokens": 100,
        "model_cost_ceiling_usd": 0.25,
        "model_input_usd_per_million": 1.0,
        "model_output_usd_per_million": 1.0,
    }.items():
        monkeypatch.setattr(settings, key, value)
    return settings


@pytest.mark.parametrize("temperature", [None, 0, 1.0])
async def test_provider_temperature_only_when_set(monkeypatch, temperature):
    settings = _provider_settings(monkeypatch)
    monkeypatch.setattr(settings, "model_temperature", temperature)
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]}
        )

    await generate_candidates([], transport=httpx.MockTransport(respond))
    if temperature is None:
        assert "temperature" not in bodies[0]
    else:
        assert bodies[0]["temperature"] == temperature


async def test_provider_cut_off_answer(monkeypatch):
    _provider_settings(monkeypatch)

    def respond(request):
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": '{"cand'}}]},
        )

    with pytest.raises(ValueError, match="cut off"):
        await generate_candidates([], transport=httpx.MockTransport(respond))


@pytest.mark.parametrize("failure", ["invalid-json", "malformed", "timeout"])
async def test_provider_failures(monkeypatch, failure):
    from app.core.config import settings

    for key, value in {
        "model_base_url": "https://model.test/v1",
        "model_api_key": "server-secret",
        "model_name": "test-model",
        "model_max_output_tokens": 100,
        "model_cost_ceiling_usd": 0.25,
        "model_input_usd_per_million": 1.0,
        "model_output_usd_per_million": 1.0,
    }.items():
        monkeypatch.setattr(settings, key, value)

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(200, content=b"not JSON" if failure == "invalid-json" else b"{}")

    with pytest.raises((ValueError, KeyError, httpx.TimeoutException)):
        await generate_candidates([], transport=httpx.MockTransport(respond))


async def test_uncertain_model_reservation_is_not_repeated(demo_server, tmp_path, monkeypatch):
    from app.agents import specialists

    calls = []

    async def fake_provider(*args, **kwargs):
        calls.append(True)
        return [], {}

    monkeypatch.setattr(specialists, "generate_candidates", fake_provider)
    url = f"http://127.0.0.1:{PORT}/faulty/healthy"
    state = {
        "selected": [url],
        "observations": [{"url": url, "viewport": viewport} for viewport in VIEWPORTS.values()],
        "checks": {},
        "activity": [],
        "model_reservations": {"navigation": True},
    }
    with pytest.raises(ValueError, match="uncertain charge"):
        await run_scan(url, tmp_path, mode="model", checkpoint=state)
    assert calls == []


async def test_model_does_not_see_page_loads_that_arsune_did_not_complete(monkeypatch):
    from app.agents import specialists

    seen = []

    async def fake_provider(observations, *, role):
        seen.append(observations)
        return [], {"reserved_cost_usd": 0}

    async def nothing(*args):
        pass

    monkeypatch.setattr(specialists, "generate_candidates", fake_provider)
    url = "https://example.com/"
    desktop, mobile = VIEWPORTS["desktop"], VIEWPORTS["mobile"]
    complete = {"url": url, "viewport": desktop, "status": 200, "layout_complete": True}
    # Arsune's own budget stopped these loads: one blank page and one without its styles.
    blank = {
        "url": url + "guias",
        "viewport": desktop,
        "status": None,
        "error": "The browser could not complete this page request.",
        "layout_complete": False,
    }
    unstyled = {"url": url, "viewport": mobile, "status": 200, "layout_complete": False}
    state = {"selected": [url, url + "guias"], "observations": [complete, blank, unstyled]}
    await specialists.propose_candidates(state, "model", False, url, nothing, nothing)
    assert seen == [[complete]] * 3
    assert state["observations"] == [complete, blank, unstyled]


async def test_exhausted_request_budget_survives_resume(demo_server, tmp_path):
    url = f"http://127.0.0.1:{PORT}/faulty/"
    state = {
        "selected": [url],
        "observations": [],
        "checks": {},
        "activity": [],
        "request_count": 400,
    }
    report = await run_scan(url, tmp_path, checkpoint=state)
    assert report["coverage"]["requests"] == 400
    assert report["coverage"]["incomplete_pages"]
    assert report["confirmed"] == []


PAGE = "https://example.com/x"


def suggestion(**changes):
    return {
        "role": "navigation",
        "claim_type": "broken_link",
        "urls": [PAGE],
        "explanation": "Broken",
        "expected_behavior": "Works",
        "observed_behavior": "Fails",
        "reproduction_steps": ["Open"],
        **changes,
    }


def test_out_of_scope_suggestion_is_dropped_and_the_rest_kept():
    items = [suggestion(urls=["http://private/"]), suggestion()]
    valid, dropped = validate_candidates(items, [PAGE])
    assert [c["urls"] for c in valid] == [[PAGE]]
    assert dropped == ["names a page outside the checked pages"]


def test_out_of_scope_details_target_is_dropped():
    item = suggestion(details={"source": PAGE, "target": "http://private/"})
    assert validate_candidates([item], [PAGE]) == (
        [],
        ["names a page outside the checked pages"],
    )


@pytest.mark.parametrize("selected, written", [(PAGE, PAGE + "/"), (PAGE + "/", PAGE)])
def test_trailing_slash_variant_maps_to_the_selected_page(selected, written):
    item = suggestion(urls=[written], details={"page": written})
    valid, dropped = validate_candidates([item], [selected])
    assert dropped == []
    assert valid[0]["urls"] == [selected] and valid[0]["details"]["page"] == selected


def test_other_url_variants_are_not_mapped():
    valid, dropped = validate_candidates([suggestion(urls=[PAGE + "?a=1"])], [PAGE])
    assert valid == [] and len(dropped) == 1


def specialist_state():
    return {"selected": [PAGE], "observations": []}


async def run_specialists(monkeypatch, state, output):
    from app.agents import specialists

    async def fake_provider(*args, **kwargs):
        return output, {"reserved_cost_usd": 0}

    async def nothing(*args):
        pass

    events = []

    async def event(role, message):
        events.append((role, message))

    monkeypatch.setattr(specialists, "generate_candidates", fake_provider)
    await specialists.propose_candidates(state, "model", False, PAGE, nothing, event)
    return events


async def test_model_specialist_wrong_role_is_dropped(monkeypatch):
    state = specialist_state()
    events = await run_specialists(monkeypatch, state, [suggestion(role="mobile")])
    assert [c["role"] for c in state["candidates"]] == ["mobile"]
    assert any("Dropped 1 suggestions: belongs to another role" in m for _, m in events)
    assert sum(state["dropped_suggestions"].values()) == 2


async def test_all_invalid_model_output_gives_no_candidates(monkeypatch):
    state = specialist_state()
    await run_specialists(monkeypatch, state, [suggestion(urls=["http://private/"])])
    assert state["candidates"] == []
    assert sum(state["dropped_suggestions"].values()) == 3


async def test_scan_completes_when_every_model_suggestion_is_invalid(
    demo_server, tmp_path, monkeypatch
):
    from app.agents import specialists

    async def invalid(*args, **kwargs):
        return [suggestion(urls=["http://private/"])], {"reserved_cost_usd": 0}

    monkeypatch.setattr(specialists, "generate_candidates", invalid)
    report = await run_scan(f"http://127.0.0.1:{PORT}/faulty/", tmp_path, mode="model")
    assert report["confirmed"] == []
    assert report["coverage"]["dropped_suggestions"] == 3
