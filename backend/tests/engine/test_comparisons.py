"""Comparison expectations from a controlled site, exercised with real Chromium."""

import copy
import os
import subprocess
import sys
import time

import httpx
import pytest
from app.workers.workflow import run_scan


@pytest.fixture(scope="module")
def comparison_site():
    from app.core.config import settings

    previous = settings.demo_url
    settings.demo_url = "http://127.0.0.1:18543"
    with subprocess.Popen(
        [sys.executable, "-m", "app.demo"],
        env={**os.environ, "ARSUNE_DEMO_PORT": "18543"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            for _ in range(60):
                try:
                    if httpx.get(settings.demo_url + "/comparison/", timeout=0.2).is_success:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Comparison fixture did not start.")
            yield settings.demo_url
        finally:
            process.terminate()
            process.wait(timeout=5)
            settings.demo_url = previous


def set_state(site, state):
    response = httpx.post(site + "/comparison-control", json={"state": state})
    response.raise_for_status()


def assert_fresh_evidence(entries, output_dir):
    assert entries
    for entry in entries:
        current = entry["current_finding"]
        assert current is not None
        assert current["evidence"]
        for observation in current["evidence"]:
            assert observation["captured_at"]
            assert (output_dir / observation["screenshot"]).is_file()


async def test_comparison_uses_fresh_evidence_and_preserves_baseline(comparison_site, tmp_path):
    site = comparison_site
    url = site + "/comparison/"
    set_state(site, "faulty")
    baseline = await run_scan(url, tmp_path / "before", inject_demo_false_positive=True)
    preserved = copy.deepcopy(baseline)
    confirmed_ids = {finding["id"] for finding in baseline["confirmed"]}
    rejected_ids = {finding["id"] for finding in baseline["findings"] if finding["status"] == "rejected"}
    assert rejected_ids
    assert {finding["claim_type"] for finding in baseline["confirmed"]} == {
        "broken_link", "horizontal_overflow", "price_mismatch"
    }
    assert len(baseline["inspected_pages"]) == 5

    async def compare(name, state, before=baseline, **kwargs):
        set_state(site, state)
        path = tmp_path / name
        result = await run_scan(
            url, path, baseline_report=before, baseline_job_id="baseline-test", **kwargs
        )
        assert len(result["inspected_pages"]) <= 5
        assert len(set(result["inspected_pages"])) == len(result["inspected_pages"])
        comparison = result["comparison"]
        assert comparison["baseline_job_id"] == "baseline-test"
        for status in ("remaining", "resolved", "new", "could_not_recheck"):
            assert comparison["summary"][status] == len(comparison[status])
        compared_ids = [
            entry["baseline_finding_id"]
            for status in ("remaining", "resolved", "could_not_recheck")
            for entry in comparison[status]
        ]
        assert len(compared_ids) == len(set(compared_ids))
        assert not rejected_ids.intersection(compared_ids)
        return result, comparison, path

    snapshots = []
    unchanged, same, path = await compare(
        "same", "faulty", save_checkpoint=lambda state: snapshots.append(copy.deepcopy(state))
    )
    assert {entry["baseline_finding_id"] for entry in same["remaining"]} == confirmed_ids
    assert same["resolved"] == same["new"] == same["could_not_recheck"] == []
    assert_fresh_evidence(same["remaining"], path)
    resumed = await run_scan(
        url, path, baseline_report=baseline, baseline_job_id="baseline-test", checkpoint=snapshots[-1]
    )
    assert resumed["comparison"] == same
    assert [f["id"] for f in resumed["findings"]] == [f["id"] for f in unchanged["findings"]]
    assert resumed["coverage"]["requests"] == unchanged["coverage"]["requests"]

    repaired, fixed, path = await compare("fixed", "repaired")
    assert repaired["confirmed"] == []
    assert {entry["baseline_finding_id"] for entry in fixed["resolved"]} == confirmed_ids
    assert fixed["remaining"] == fixed["new"] == fixed["could_not_recheck"] == []
    assert_fresh_evidence(fixed["resolved"], path)
    assert all(entry["current_finding"]["status"] == "rejected" for entry in fixed["resolved"])

    _, changed, path = await compare("new", "new_fault")
    assert {entry["baseline_finding_id"] for entry in changed["resolved"]} == confirmed_ids
    assert len(changed["new"]) == 1
    assert changed["new"][0]["finding"]["claim_type"] == "horizontal_overflow"
    assert changed["new"][0]["finding"]["urls"] == [url + "healthy"]
    assert changed["remaining"] == changed["could_not_recheck"] == []

    overflow_id = next(f["id"] for f in baseline["confirmed"] if f["claim_type"] == "horizontal_overflow")
    for name in ("failed_page", "blocked_css"):
        _, unavailable, _ = await compare(name, name)
        assert overflow_id in {e["baseline_finding_id"] for e in unavailable["could_not_recheck"]}
        assert overflow_id not in {e["baseline_finding_id"] for e in unavailable["resolved"]}

    outside = copy.deepcopy(baseline)
    outside_finding = copy.deepcopy(next(f for f in baseline["confirmed"] if f["id"] == overflow_id))
    outside_finding.update(id="outside-baseline", urls=["https://unrelated.example/not-selected"])
    outside_finding["details"] = {"url": outside_finding["urls"][0]}
    outside["confirmed"].append(outside_finding)
    outside["findings"].append(outside_finding)
    _, scoped, _ = await compare("outside", "repaired", before=outside)
    assert "outside-baseline" in {e["baseline_finding_id"] for e in scoped["could_not_recheck"]}
    assert "outside-baseline" not in {e["baseline_finding_id"] for e in scoped["resolved"]}
    assert baseline == preserved


async def test_controlled_demo_mapping_rechecks_all_repaired_claims(comparison_site, tmp_path):
    faulty = comparison_site + "/faulty/"
    corrected = comparison_site + "/corrected/"
    baseline = await run_scan(faulty, tmp_path / "before", inject_demo_false_positive=True)
    preserved = copy.deepcopy(baseline)
    report = await run_scan(
        corrected,
        tmp_path / "after",
        baseline_report=baseline,
        baseline_job_id="faulty-demo",
        comparison_mapping={faulty: corrected},
    )
    comparison = report["comparison"]
    assert comparison["mapping"] == {faulty: corrected}
    assert comparison["summary"] == {
        "remaining": 0,
        "resolved": len(baseline["confirmed"]),
        "new": 0,
        "could_not_recheck": 0,
    }
    assert_fresh_evidence(comparison["resolved"], tmp_path / "after")
    for entry in comparison["resolved"]:
        assert all(url.startswith(faulty) for url in entry["finding"]["urls"])
        assert all(url.startswith(corrected) for url in entry["current_finding"]["urls"])
    assert baseline == preserved
