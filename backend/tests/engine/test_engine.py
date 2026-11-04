import copy
import json
import os
import subprocess
import sys
import time

import httpx
import pytest
from arsune.engine import run_scan, scripted, validate_candidates
from arsune.engine.model import ModelConfigurationError, generate_candidates
from arsune.engine.policy import TargetPolicyError, validate_target


@pytest.fixture(scope="module", autouse=True)
def demo_server():
    from arsune.config import settings
    old_demo = settings.demo_url
    settings.demo_url = "http://127.0.0.1:18541"
    environment = {**os.environ, "ARSUNE_DEMO_PORT": "18541"}
    with subprocess.Popen([sys.executable, "-m", "arsune.demo"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, env=environment) as process:
        for _ in range(50):
            try:
                if httpx.get("http://127.0.0.1:18541/faulty/", timeout=.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(.1)
        else:
            process.terminate()
            raise RuntimeError("Demo did not start.")
        yield
        process.terminate()
        process.wait(timeout=5)
        settings.demo_url = old_demo


async def test_real_browser_faults_rejection_cap_and_corrected(demo_server, tmp_path):
    snapshots = []
    report = await run_scan("http://127.0.0.1:18541/faulty/", tmp_path / "faulty",
                            inject_demo_false_positive=True,
                            save_checkpoint=lambda state: snapshots.append(copy.deepcopy(state)))
    assert len(report["inspected_pages"]) == 5
    assert {f["claim_type"] for f in report["confirmed"]} == {
        "broken_link", "horizontal_overflow", "price_mismatch"}
    assert any(f["injected"] and f["status"] == "rejected" for f in report["findings"])
    assert {a["role"] for a in report["activity"]} >= {"navigation", "mobile", "consistency", "checker"}
    for finding in report["findings"]:
        assert finding["reproduction_steps"]
        for evidence in finding["evidence"]:
            assert (tmp_path / "faulty" / evidence["screenshot"]).is_file()
    assert all(o["url"] in report["inspected_pages"] for o in report["observations"])
    resumed = await run_scan("http://127.0.0.1:18541/faulty/", tmp_path / "faulty",
                             inject_demo_false_positive=True, checkpoint=snapshots[-1])
    assert [f["id"] for f in resumed["findings"]] == [f["id"] for f in report["findings"]]
    assert resumed["coverage"]["requests"] == report["coverage"]["requests"]
    corrected = await run_scan("http://127.0.0.1:18541/corrected/", tmp_path / "corrected")
    assert corrected["confirmed"] == []
    (tmp_path / "faulty-report.json").write_text(json.dumps(report, indent=2))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:secret@example.com",
    "http://127.0.0.1:8542/", "http://localhost:18541/", "http://169.254.169.254/", "http://[::1]/"])
async def test_policy_rejects_private_and_unsafe(url):
    with pytest.raises(TargetPolicyError):
        await validate_target(url)


async def test_policy_demo_exact_origin():
    assert await validate_target("http://127.0.0.1:18541/faulty/#here") == "http://127.0.0.1:18541/faulty/"


async def test_browser_redirect_and_subresource_policy(demo_server, tmp_path):
    redirect = await run_scan("http://127.0.0.1:18541/faulty/redirect-private", tmp_path / "redirect")
    assert redirect["confirmed"] == []
    assert "http://127.0.0.1:8542/internal" in redirect["coverage"]["blocked_requests"]
    assert redirect["coverage"]["incomplete_pages"]
    resource = await run_scan("http://127.0.0.1:18541/faulty/resource-policy", tmp_path / "resource")
    assert "http://127.0.0.1:8542/internal" in resource["coverage"]["blocked_requests"]


def test_failed_navigation_is_not_a_fault():
    assert scripted([{"url": "https://example.com/", "viewport": {"width": 1280, "height": 900},
                      "links": ["https://example.com/fail"], "facts": []},
                     {"url": "https://example.com/fail", "viewport": {"width": 1280, "height": 900},
                      "status": None, "links": [], "facts": []}]) == []


async def test_provider_transport_and_budget(monkeypatch):
    from arsune.config import settings
    for key, value in {"model_base_url": "https://model.test/v1", "model_api_key": "server-secret",
        "model_name": "test-model", "model_max_output_tokens": 100,
        "model_cost_ceiling_usd": .25, "model_input_usd_per_million": 1.,
        "model_output_usd_per_million": 1.}.items():
        monkeypatch.setattr(settings, key, value)
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer server-secret"
        assert json.loads(request.content)["max_tokens"] == 100
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]})

    candidates, provider = await generate_candidates([], transport=httpx.MockTransport(respond))
    assert candidates == [] and provider["live_call"] is False
    assert len(calls) == 1
    monkeypatch.setattr(settings, "model_cost_ceiling_usd", .00000001)
    with pytest.raises(ModelConfigurationError):
        await generate_candidates([], transport=httpx.MockTransport(respond))
    assert len(calls) == 1
    monkeypatch.setattr(settings, "model_api_key", "")
    with pytest.raises(ModelConfigurationError):
        await generate_candidates([], transport=httpx.MockTransport(respond))


@pytest.mark.parametrize("failure", ["invalid-json", "malformed", "timeout"])
async def test_provider_failures(monkeypatch, failure):
    from arsune.config import settings
    for key, value in {"model_base_url": "https://model.test/v1", "model_api_key": "server-secret",
        "model_name": "test-model", "model_max_output_tokens": 100,
        "model_cost_ceiling_usd": .25, "model_input_usd_per_million": 1.,
        "model_output_usd_per_million": 1.}.items():
        monkeypatch.setattr(settings, key, value)

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(200, content=b"not JSON" if failure == "invalid-json" else b'{}')

    with pytest.raises((ValueError, KeyError, httpx.TimeoutException)):
        await generate_candidates([], transport=httpx.MockTransport(respond))


async def test_uncertain_model_reservation_is_not_repeated(demo_server, tmp_path, monkeypatch):
    from arsune import engine
    calls = []

    async def fake_provider(*args, **kwargs):
        calls.append(True)
        return [], {}

    monkeypatch.setattr(engine, "generate_candidates", fake_provider)
    url = "http://127.0.0.1:18541/faulty/healthy"
    state = {"selected": [url], "observations": [{"url": url, "viewport": viewport} for viewport in engine.VIEWPORTS.values()],
             "checks": {}, "activity": [], "model_reservations": {"navigation": True}}
    with pytest.raises(ValueError, match="uncertain charge"):
        await run_scan(url, tmp_path, mode="model", checkpoint=state)
    assert calls == []


async def test_exhausted_request_budget_survives_resume(demo_server, tmp_path):
    url = "http://127.0.0.1:18541/faulty/"
    state = {"selected": [url], "observations": [], "checks": {}, "activity": [], "request_count": 400}
    report = await run_scan(url, tmp_path, checkpoint=state)
    assert report["coverage"]["requests"] == 400
    assert report["coverage"]["incomplete_pages"]
    assert report["confirmed"] == []


async def test_model_specialist_cannot_claim_another_role(demo_server, tmp_path, monkeypatch):
    from arsune import engine

    async def wrong_role(*args, **kwargs):
        return [{"role": "mobile"}], {}

    monkeypatch.setattr(engine, "generate_candidates", wrong_role)
    with pytest.raises(ValueError, match="another role"):
        await run_scan("http://127.0.0.1:18541/faulty/", tmp_path, mode="model")


def test_model_cannot_expand_scope():
    with pytest.raises(ValueError):
        validate_candidates([{"role": "navigation", "claim_type": "broken_link", "urls": ["http://private/"],
            "explanation": "Broken", "expected_behavior": "Works", "observed_behavior": "Fails",
            "reproduction_steps": ["Open"]}], ["https://example.com/"])
