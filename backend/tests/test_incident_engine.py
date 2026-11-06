import copy
import html
import json
from pathlib import Path

import pytest
from app.incidents import check_candidate, recompute, run_incident, validate_bundle
from app.incidents.engine import propose

FIXTURES = Path(__file__).parent / "fixtures" / "incidents"


def bundle(name="known", **changes):
    root = FIXTURES / name
    request = json.loads((root / "request.json").read_text())
    request.update(
        events_text=(root / "events.ndjson").read_text(),
        deployments_text=(root / "deployments.json").read_text(),
    )
    request.update(changes)
    return validate_bundle(**request)


@pytest.mark.parametrize(
    "name,status",
    [
        ("known", "supported_explanation"),
        ("corrected", "could_not_verify"),
        ("unrelated", "rejected_explanation"),
        ("missing_deployment", "could_not_verify"),
        ("instruction_text", "supported_explanation"),
    ],
)
async def test_actual_scripted_outcomes(name, status):
    report = await run_incident(bundle(name))
    assert report["explanations"][0]["status"] == status
    assert report["reproduced_cause"] is False
    assert {a["role"] for a in report["activity"]} == {"timeline", "error", "change", "checker"}
    if name in {"known", "instruction_text"}:
        refs = report["explanations"][0]["evidence"]
        assert {r["record_id"] for r in refs} == {"e2", "e3", "d1"}
        assert refs[0]["line"] == 2
        assert refs[-1]["record"] == 1
        assert all(len(r["sha256"]) == 64 for r in refs)
    assert "reproduced_cause" not in {f["status"] for f in report["findings"]}


async def test_recovery_stable_results_and_stage_events():
    original = bundle()
    saved, emitted = [], []
    report = await run_incident(
        original, save_checkpoint=saved.append, emit_event=lambda *args: emitted.append(args)
    )
    resumed = await run_incident(
        original,
        checkpoint=saved[-1],
        save_checkpoint=lambda _: pytest.fail("completed stage saved twice"),
        emit_event=lambda *_: pytest.fail("duplicate event"),
    )
    assert report == resumed
    assert len(saved) == 4 and len(emitted) == 4
    interrupted = await run_incident(original, checkpoint=saved[1])
    assert interrupted == report
    changed = copy.deepcopy(original)
    changed["service"] = "other"
    with pytest.raises(ValueError, match="Checkpoint"):
        await run_incident(changed, checkpoint=saved[-1])


async def test_fresh_checker_rejects_tampered_counts_and_source():
    original = bundle()
    candidate = propose(original, "error")[0]
    candidate["count"] = 100
    checked = await check_candidate(candidate, original)
    assert checked["status"] == "rejected_explanation"
    changed = {name: source["text"] for name, source in original["sources"].items()}
    changed["events.ndjson"] = changed["events.ndjson"].replace("checkout_token", "secret_token")
    with pytest.raises(ValueError, match="hash"):
        await check_candidate(candidate, original, lambda: changed)
    fake = copy.deepcopy(original)
    fake["events"][1]["level"] = "info"
    checked = await check_candidate(propose(original, "error")[0], fake)
    assert checked["count"] == 2 and checked["status"] == "observed_fact"


async def test_hash_verification_uses_fresh_storage_each_check():
    original = bundle()
    calls = []

    async def loader():
        calls.append(1)
        return {name: source["text"] for name, source in original["sources"].items()}

    await run_incident(original, source_loader=loader)
    assert len(calls) >= 5


@pytest.mark.parametrize(
    "name,message", [("duplicate", "line 4: duplicate"), ("timezone", "line 2: invalid timestamp")]
)
def test_invalid_fixtures(name, message):
    with pytest.raises(ValueError, match=message):
        bundle(name)


@pytest.mark.parametrize("bad", ["{}", "[]", "NaN", '{"id":"a","id":"b"}', "", "\n"])
def test_malformed_records_rejected(bad):
    if bad == "":
        assert bundle(events_text=bad)["events"] == []
    else:
        with pytest.raises(ValueError, match="line 1"):
            bundle(events_text=bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("timestamp", "2026-11-05T10:01:00"),
        ("timestamp", "0001-01-01T00:00:00+01:00"),
        ("timestamp", "2026-11-05T10:01:00+24:00"),
        ("timestamp", "2026-11-05T10:01:00-01:99"),
        ("level", "ERROR"),
        ("message", "\ud800"),
        ("message", "a\x00b"),
        ("extra", "x"),
    ],
)
def test_bad_event_fields(field, value):
    original = bundle()
    record = {k: v for k, v in original["events"][0].items() if not k.startswith("_")}
    record[field] = value
    with pytest.raises(ValueError, match="line 1"):
        bundle(events_text=json.dumps(record))


def test_input_limits():
    with pytest.raises(ValueError, match="10 MB"):
        bundle(events_text="é" * (5 * 1024 * 1024))
    with pytest.raises(ValueError, match="24 hours"):
        bundle(end="2026-11-06T10:10:00Z")
    event = json.loads(bundle()["sources"]["events.ndjson"]["text"].splitlines()[0])
    rows = [json.dumps(event | {"id": f"e{i}"}) for i in range(5001)]
    with pytest.raises(ValueError, match="5000"):
        bundle(events_text="\n".join(rows))
    deployment = json.loads(bundle()["sources"]["deployments.json"]["text"])[0]
    with pytest.raises(ValueError, match="20 records"):
        bundle(deployments_text=json.dumps([deployment | {"id": f"d{i}"} for i in range(21)]))


async def test_exact_context_joins_and_excluded_data():
    original = bundle()
    deployments = json.loads(original["sources"]["deployments.json"]["text"])
    for field, value in [
        ("service", "different"),
        ("revision", "v3"),
        ("migration_column", "other"),
        ("timestamp", "2026-11-05T10:05:00Z"),
    ]:
        changed = bundle(deployments_text=json.dumps([deployments[0] | {field: value}]))
        report = await run_incident(changed)
        assert report["explanations"][0]["status"] == "could_not_verify"
    rows = [json.loads(line) for line in original["sources"]["events.ndjson"]["text"].splitlines()]
    rows += [
        rows[1] | {"id": "other-service", "service": "different"},
        rows[1] | {"id": "late", "timestamp": "2026-11-05T12:00:00Z"},
    ]
    report = await run_incident(bundle(events_text="\n".join(json.dumps(r) for r in rows)))
    assert report["coverage"]["excluded_events"] == 2
    assert {r["id"] for r in report["timeline"]} == {"e1", "e2", "e3", "d1"}


async def test_offset_equivalence_boundary_and_escape():
    original = bundle("instruction_text")
    rows = [json.loads(line) for line in original["sources"]["events.ndjson"]["text"].splitlines()]
    rows[1]["timestamp"] = "2026-11-05T04:01:00-06:00"
    report = await run_incident(bundle(events_text="\n".join(json.dumps(r) for r in rows)))
    assert report["explanations"][0]["status"] == "supported_explanation"
    evidence = next(r for r in report["timeline"] if r["id"] == "e1")["evidence"]
    assert "<script>" not in evidence["excerpt"] and "&lt;script&gt;" in evidence["excerpt"]
    precise = bundle(start="2026-11-05T10:01:00.000001Z")
    assert (await run_incident(precise))["coverage"]["selected_events"] == 1


async def test_conflicting_latest_deployments_are_uncertain():
    original = bundle()
    deployment = json.loads(original["sources"]["deployments.json"]["text"])[0]
    conflicting = [deployment, deployment | {"id": "d2", "migration_status": "completed"}]
    report = await run_incident(bundle(deployments_text=json.dumps(conflicting)))
    finding = report["explanations"][0]
    assert finding["status"] == "could_not_verify"
    assert "conflict" in finding["explanation"]
    assert {r["record_id"] for r in finding["evidence"]} >= {"d1", "d2"}


async def test_missing_failure_data_does_not_prove_rejection():
    report = await run_incident(bundle(events_text=""))
    assert report["explanations"][0]["status"] == "could_not_verify"


async def test_limits_report_omitted_groups_and_evidence():
    original = bundle()
    row = json.loads(original["sources"]["events.ndjson"]["text"].splitlines()[1])
    rows = [row | {"id": f"e{i}"} for i in range(40)]
    rows += [row | {"id": f"unique{i}", "message": f"error {i}"} for i in range(25)]
    report = await run_incident(bundle(events_text="\n".join(json.dumps(r) for r in rows)))
    assert report["coverage"]["error_groups_total"] == 26
    assert report["coverage"]["error_groups_omitted"] == 6
    large_group = next(f for f in report["findings"] if f.get("count") == 40)
    assert large_group["evidence_records_total"] == 40
    assert large_group["evidence_records_omitted"] == 30
    assert len(large_group["evidence"]) == 10
    assert len(json.dumps(report)) < 200000


def test_duplicate_deployment_and_unknown_deployment_field():
    original = bundle()
    deployment = json.loads(original["sources"]["deployments.json"]["text"])[0]
    with pytest.raises(ValueError, match="record 2: duplicate"):
        bundle(deployments_text=json.dumps([deployment, deployment]))
    with pytest.raises(ValueError, match="record 1: unknown"):
        bundle(deployments_text=json.dumps([deployment | {"command": "rm -rf /"}]))


async def test_deployment_excerpt_keeps_original_offset_and_fields():
    original = bundle()
    deployment = json.loads(original["sources"]["deployments.json"]["text"])[0]
    deployment["timestamp"] = "2026-11-05T04:00:00-06:00"
    accepted = bundle(deployments_text=json.dumps([deployment]))
    report = await run_incident(accepted)
    ref = next(
        ref
        for ref in report["explanations"][0]["evidence"]
        if ref["filename"] == "deployments.json"
    )
    assert json.loads(html.unescape(ref["excerpt"])) == deployment
    assert ref["timestamp"] == "2026-11-05T10:00:00.000000+00:00"
    assert ref["sha256"] == accepted["sources"]["deployments.json"]["sha256"]
    assert ref["record"] == 1


@pytest.mark.parametrize("name", ["known", "corrected", "unrelated", "missing_deployment"])
async def test_recompute_matches_the_worker_report(name):
    # The incident export relies on this: a saved worker report must match a fresh recompute.
    original = bundle(name)
    assert recompute(original) == await run_incident(original) | {"activity": []}
