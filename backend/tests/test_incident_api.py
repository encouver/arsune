"""Incident API checks against PostgreSQL and the durable worker store."""

import hashlib
import json
import re
from uuid import uuid4

import pytest
from app.core.db import Session, engine
from app.core.models import Attempt, Event, IncidentSource, Job, PendingAction, Record
from app.main import app
from app.v1.services import incidents
from app.workers.tasks import scan
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError


def bundle():
    return {
        "description": "Checkout failures",
        "service": "checkout",
        "start": "2026-11-05T09:50:00Z",
        "end": "2026-11-05T10:20:00Z",
        "events_text": json.dumps(
            {
                "id": "error-1",
                "timestamp": "2026-11-05T10:01:00Z",
                "service": "checkout",
                "level": "error",
                "message": "<script>inert</script> missing column coupon_code",
                "error_code": "missing_column",
                "column": "coupon_code",
                "revision": "v2",
                "request_id": "request-1",
            }
        )
        + "\n",
        "deployments_text": json.dumps(
            [
                {
                    "id": "deploy-1",
                    "timestamp": "2026-11-05T10:00:00Z",
                    "service": "checkout",
                    "revision": "v2",
                    "migration_status": "failed",
                    "migration_column": "coupon_code",
                }
            ],
            indent=2,
        ),
    }


@pytest.fixture
def client():
    assert engine.dialect.name == "postgresql"
    ids = []
    test_client = TestClient(app)
    original = test_client.post

    def post(*args, **kwargs):
        result = original(*args, **kwargs)
        if result.status_code == 202:
            ids.append(result.json()["id"])
        return result

    test_client.post = post
    yield test_client
    with Session.begin() as session:
        for model in (IncidentSource, Record, Event, Attempt, PendingAction):
            session.execute(delete(model).where(model.job_id.in_(ids)))
        session.execute(delete(Job).where(Job.id.in_(ids)))


def test_atomic_sources_queue_history_and_worker(client):
    payload = bundle()
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert response.json()["kind"] == "incident"
    with Session() as session:
        job = session.get(Job, job_id)
        assert job.status == "queued"
        assert session.get(PendingAction, f"{job_id}:scan")
        sources = session.query(IncidentSource).filter_by(job_id=job_id).all()
        assert len(sources) == 2
        assert {s.filename: s.content for s in sources} == incidents.load_sources(job_id)
        for source in sources:
            assert source.sha256 == hashlib.sha256(source.content.encode()).hexdigest()
        assert "events_text" not in job.incident_input
    assert payload["events_text"] not in response.text
    history = client.get("/api/jobs?limit=50").json()["items"]
    row = next(row for row in history if row["id"] == job_id)
    assert row["kind"] == "incident" and row["title"] == "Incident: checkout"
    assert "sources" not in row
    scan.run(job_id)
    finished = client.get(f"/api/jobs/{job_id}").json()
    assert finished["status"] == "completed", finished
    assert finished["report"]["service"] == "checkout"
    assert finished["generation"] == 1
    scan.run(job_id)
    assert client.get(f"/api/jobs/{job_id}").json() == finished
    assert client.post(f"/api/jobs/{job_id}/rerun", json={}).status_code == 422
    assert client.get(f"/api/jobs/{job_id}/repair-report").status_code == 422
    assert client.get(f"/api/jobs/{job_id}/evidence/screenshot.png").status_code == 422


def test_exact_evidence_selection_and_immutable_hash(client):
    payload = bundle()
    job_id = client.post("/api/incidents", json=payload).json()["id"]
    route = f"/api/incidents/{job_id}/evidence/"
    result = client.get(route + "events.ndjson", params={"record_id": "error-1"})
    assert result.status_code == 200
    value = result.json()
    assert value["raw"] == payload["events_text"].rstrip("\n")
    assert value["record"]["message"].startswith("<script>")
    assert "&lt;script&gt;" in value["excerpt"]
    assert value["sha256"] == hashlib.sha256(payload["events_text"].encode()).hexdigest()
    deployment = client.get(route + "deployments.json", params={"record_id": "deploy-1"}).json()
    assert deployment["line"] is None and deployment["record_index"] == 1
    assert client.get(route + "deployments.json", params={"line": 1}).status_code == 422
    assert client.get(route + "events.ndjson", params={"line": 1}).json() == value
    assert client.get(route + "events.ndjson").status_code == 422
    assert client.get(route + "events.ndjson", params={"record_id": "missing"}).status_code == 404
    assert client.get(route + ".env", params={"line": 1}).status_code == 404
    with pytest.raises(DBAPIError), Session.begin() as session:
        session.execute(
            text("UPDATE incident_sources SET content = 'changed' WHERE job_id = :id"),
            {"id": job_id},
        )
    # A mismatched source hash must fail closed even if privileged corruption bypasses the trigger.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE incident_sources DISABLE TRIGGER immutable_incident_sources"
        )
        connection.execute(
            text("UPDATE incident_sources SET sha256 = :hash WHERE job_id = :id"),
            {"hash": "0" * 64, "id": job_id},
        )
        connection.exec_driver_sql(
            "ALTER TABLE incident_sources ENABLE TRIGGER immutable_incident_sources"
        )
    assert client.get(route + "events.ndjson", params={"line": 1}).status_code == 409
    with pytest.raises(ValueError, match="hash"):
        incidents.load_sources(job_id)


def test_reject_invalid_and_bounded_uploads(client, monkeypatch):
    payload = bundle()
    for key, value in (
        ("start", "2026-11-05T09:50:00"),
        ("description", "\ud800"),
        ("events_text", "\ud800"),
        ("description", "bad\x00description"),
        ("service", ""),
        ("events_text", "{}\n"),
    ):
        assert (
            client.post(
                "/api/incidents", content=json.dumps({**payload, key: value}).encode()
            ).status_code
            == 422
        )
    assert client.post("/api/incidents", content=b"{oops").status_code == 422
    assert (
        client.post("/api/incidents", json={**payload, "url": "file:///etc/passwd"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/incidents",
            json={**payload, "events_text": "x" * (incidents.MAX_SOURCE_BYTES + 1)},
        ).status_code
        == 413
    )
    monkeypatch.setattr(incidents, "MAX_REQUEST_BYTES", 100)
    assert client.post("/api/incidents", content=(b"x" * 101)).status_code == 413
    assert client.get(f"/api/incidents/{uuid4()}/evidence/events.ndjson?line=1").status_code == 404


def test_download_incident_report_is_portable_and_escaped(client):
    from html import escape

    payload = bundle()
    payload["description"] = "<img src=x onerror=alert(1)> Checkout failures"
    job_id = client.post("/api/incidents", json=payload).json()["id"]
    route = f"/api/incidents/{job_id}/report"
    assert client.get(route).status_code == 409
    scan.run(job_id)
    response = client.get(route)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        f'attachment; filename="arsune-incident-{job_id}.html"'
    )
    assert response.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "base-uri 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert "<script>" not in html and "<img " not in html
    assert "&lt;script&gt;inert&lt;/script&gt;" in html
    assert escape(payload["description"]) in html
    assert escape(payload["events_text"].rstrip("\n")) in html
    # Exact JSON record text retains the accepted internal deployment spacing.
    deployment_text = payload["deployments_text"]
    assert (
        escape(deployment_text[deployment_text.index("{") : deployment_text.rindex("}") + 1])
        in html
    )
    assert '"migration_status": "failed"'.replace('"', "&quot;") in html
    for title in (
        "Timeline",
        "Observed facts",
        "Supported explanations",
        "Rejected explanations",
        "Could not verify",
        "Missing evidence",
        "Next checks",
        "Coverage and omissions",
        "Mode: scripted",
    ):
        assert title in html
    for key in ("events_text", "deployments_text"):
        assert hashlib.sha256(payload[key].encode()).hexdigest() in html
    assert 'href="#evidence-' in html
    assert 'href="http' not in html and 'src="http' not in html
    assert "No cause was reproduced" in html


def test_report_guards_sources_and_saved_citations(client):
    job_id = client.post("/api/incidents", json=bundle()).json()["id"]
    scan.run(job_id)
    route = f"/api/incidents/{job_id}/report"
    assert client.get(f"/api/incidents/{uuid4()}/report").status_code == 404
    with Session.begin() as session:
        job = session.get(Job, job_id)
        original = job.report
        job.report = {
            **original,
            "timeline": [
                {
                    **original["timeline"][0],
                    "evidence": {
                        **original["timeline"][0]["evidence"],
                        "record_id": "../../etc/passwd",
                    },
                }
            ],
        }
    assert client.get(route).status_code == 409
    with Session.begin() as session:
        job = session.get(Job, job_id)
        job.report = {**original, "coverage": {**original["coverage"], "source_hashes": {}}}
    assert client.get(route).status_code == 409
    with Session.begin() as session:
        job = session.get(Job, job_id)
        job.report = original
        job.kind = "website"
    assert client.get(route).status_code == 422
    with Session.begin() as session:
        session.get(Job, job_id).kind = "incident"
        session.execute(
            delete(IncidentSource).where(
                IncidentSource.job_id == job_id,
                IncidentSource.filename == "deployments.json",
            )
        )
    response = client.get(route)
    assert response.status_code == 409 and "unavailable" in response.text


def test_report_rejects_tampered_source(client):
    job_id = client.post("/api/incidents", json=bundle()).json()["id"]
    scan.run(job_id)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE incident_sources DISABLE TRIGGER immutable_incident_sources"
        )
        try:
            connection.execute(
                text("UPDATE incident_sources SET content = :value WHERE job_id = :id"),
                {"value": "tampered", "id": job_id},
            )
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE incident_sources ENABLE TRIGGER immutable_incident_sources"
            )
    response = client.get(f"/api/incidents/{job_id}/report")
    assert response.status_code == 409 and "hash" in response.text


def test_report_evidence_limits_and_known_bundle(client, monkeypatch):
    from pathlib import Path

    from app.v1.services import incident_report

    fixture = Path(__file__).parent / "fixtures/incidents/known"
    payload = json.loads((fixture / "request.json").read_text())
    payload.update(
        events_text=(fixture / "events.ndjson").read_text(),
        deployments_text=(fixture / "deployments.json").read_text(),
    )
    job_id = client.post("/api/incidents", json=payload).json()["id"]
    scan.run(job_id)
    route = f"/api/incidents/{job_id}/report"
    response = client.get(route)
    assert response.status_code == 200, response.text
    assert "failed migration" in response.text
    assert "checkout_token" in response.text
    assert "timeline omitted" in response.text
    monkeypatch.setattr(incident_report, "MAX_RECORD_BYTES", 1)
    limited = client.get(route)
    assert limited.status_code == 200
    assert "exact record omitted: export size limit" in limited.text
    assert "<pre>{" not in limited.text.split("<h2>Exact cited source records</h2>")[1]
    monkeypatch.setattr(incident_report, "MAX_RECORD_BYTES", 64_000)
    monkeypatch.setattr(incident_report, "MAX_EVIDENCE_BYTES", 1)
    assert "exact record omitted: export size limit" in client.get(route).text
    monkeypatch.setattr(incident_report, "MAX_OUTPUT_BYTES", 1)
    assert client.get(route).status_code == 409


def completed(client, payload):
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    scan.run(job_id)
    return job_id


def section(html, title):
    return html.split(f"<h2>{title}</h2>")[1].split("<h2>")[0]


def exact_record_bytes(html):
    records = html.split("<h2>Exact cited source records</h2>")[1]
    return sum(len(value.encode()) for value in re.findall(r"<pre>(.*?)</pre>", records, re.DOTALL))


def grouped(message, groups=20, per=12, description="d"):
    """Missing-column groups, each with a matching failed migration."""
    lines, deployments = [], []
    for g in range(groups):
        deployments.append(
            {
                "id": f"dep{g}",
                "timestamp": "2026-11-05T00:00:01Z",
                "service": "checkout",
                "revision": f"r{g}",
                "migration_status": "failed",
                "migration_column": f"c{g}",
            }
        )
        lines += [
            json.dumps(
                {
                    "id": f"e{g}-{i}",
                    "timestamp": f"2026-11-05T01:{g:02d}:{i:02d}Z",
                    "service": "checkout",
                    "level": "error",
                    "message": message + str(g),
                    "revision": f"r{g}",
                    "error_code": "missing_column",
                    "column": f"c{g}",
                },
                ensure_ascii=False,
            )
            for i in range(per)
        ]
    return {
        "description": description,
        "service": "checkout",
        "start": "2026-11-05T00:00:00Z",
        "end": "2026-11-05T23:00:00Z",
        "events_text": "\n".join(lines) + "\n",
        "deployments_text": json.dumps(deployments),
    }


def test_export_size_is_not_measured_on_ascii_escaped_saved_json(client):
    # About 260 KB of sources; the saved report is over 2 MB only as ASCII-escaped JSON.
    job_id = completed(client, grouped("数据库列不存在" * 42))
    response = client.get(f"/api/incidents/{job_id}/report")
    assert response.status_code == 200, response.text
    assert "数据库列不存在" in section(response.text, "Exact cited source records")


def test_finding_evidence_receives_the_record_budget_before_the_timeline(client, monkeypatch):
    from app.v1.services import incident_report

    lines = [
        json.dumps(
            {
                "id": f"info-{i}",
                "timestamp": f"2026-11-05T09:10:{i:02d}Z",
                "service": "checkout",
                "level": "info",
                "message": "request ok " + "at frame x.y.z line 10; " * 80,
                "revision": "v1",
            }
        )
        for i in range(30)
    ]
    lines += [
        json.dumps(
            {
                "id": f"err-{i}",
                "timestamp": f"2026-11-05T11:00:{i:02d}Z",
                "service": "checkout",
                "level": "error",
                "message": "column checkout_token does not exist " + "stack frame; " * 230,
                "error_code": "missing_column",
                "column": "checkout_token",
                "revision": "v2",
            }
        )
        for i in range(5)
    ]
    deployment = {
        "id": "deploy-v2",
        "timestamp": "2026-11-05T10:59:00Z",
        "service": "checkout",
        "revision": "v2",
        "migration_status": "failed",
        "migration_column": "checkout_token",
    }
    job_id = completed(
        client,
        {
            "description": "Checkout errors after v2",
            "service": "checkout",
            "start": "2026-11-05T09:00:00Z",
            "end": "2026-11-05T12:00:00Z",
            "events_text": "\n".join(lines) + "\n",
            "deployments_text": json.dumps([deployment]),
        },
    )
    monkeypatch.setattr(incident_report, "MAX_EVIDENCE_BYTES", 20_000)
    html = client.get(f"/api/incidents/{job_id}/report").text
    supported = section(html, "Supported explanations")
    assert supported.count("<li>") == 6
    assert supported.count('href="#evidence-') == 6
    assert "exact record omitted" not in supported
    assert "exact record omitted: export size limit" in section(html, "Timeline")
    assert exact_record_bytes(html) <= 20_000


def test_record_budget_counts_escaped_bytes_and_valid_exports_fit(client):
    from app.v1.services import incident_report

    description = "Customer's order can't load; it's the 'checkout' page " + "'" * 1900
    job_id = completed(client, grouped("'" * 3990, per=7, description=description))
    response = client.get(f"/api/incidents/{job_id}/report")
    assert response.status_code == 200, response.text
    html = response.text
    assert len(html.encode()) < 2_600_000
    assert exact_record_bytes(html) <= incident_report.MAX_EVIDENCE_BYTES
    omitted = re.search(r"Citation occurrences omitted by these limits: (\d+)", html)
    assert int(omitted.group(1)) > 0
    assert "exact record omitted: export size limit" in section(html, "Supported explanations")


def test_export_size_is_bounded_at_the_engine_limits():
    from app.core.models import utcnow
    from app.incidents import recompute, validate_bundle
    from app.v1.services import incident_report

    # Every field at its length limit, using the characters that grow most when escaped.
    quote, service = '"', "'" * 200
    lines, deployments = [], []
    for g in range(20):
        revision, column = quote * 197 + f"r{g:02d}", quote * 197 + f"c{g:02d}"
        deployments.append(
            {
                "id": f"d{g}-".ljust(200, "x"),
                "timestamp": "2026-11-05T00:00:01Z",
                "service": service,
                "revision": revision,
                "migration_status": "failed",
                "migration_column": column,
            }
        )
        lines += [
            json.dumps(
                {
                    "id": f"e{g}-{i}-".ljust(200, "x"),
                    "timestamp": f"2026-11-05T01:{g:02d}:{i:02d}Z",
                    "service": service,
                    "level": "error",
                    "message": quote * 3997 + f"m{g:02d}",
                    "error_code": "missing_column",
                    "column": column,
                    "revision": revision,
                    "request_id": quote * 200,
                }
            )
            for i in range(12)
        ]
    accepted = validate_bundle(
        quote * 2000,
        service,
        "2026-11-05T00:00:00Z",
        "2026-11-05T23:59:59Z",
        "\n".join(lines),
        json.dumps(deployments),
    )
    report = recompute(accepted)
    assert len(report["findings"]) == 42 and len(report["timeline"]) == 100
    view = incident_report.verified_view(report, accepted)
    html = incident_report.document(str(uuid4()), utcnow(), view, accepted)
    total = len(html.encode())
    assert total - exact_record_bytes(html) < 2_000_000
    assert total < 2_600_000 < incident_report.MAX_OUTPUT_BYTES


def test_record_budget_counts_escaped_bytes_in_priority_order(monkeypatch):
    from app.v1.services import incident_report

    records = {("events.ndjson", "a"): ("a", "'" * 10), ("events.ndjson", "b"): ("b", "x" * 10)}
    monkeypatch.setattr(incident_report, "MAX_EVIDENCE_BYTES", 60)
    keys = [("events.ndjson", "a"), ("events.ndjson", "b"), ("events.ndjson", "a")]
    embedded, omitted = incident_report.embed_records(keys, records)
    assert list(embedded) == [("events.ndjson", "a")] and omitted == 1
    view = {
        "findings": [
            {"status": "observed_fact", "evidence": [{"filename": "f", "record_id": "observed"}]},
            {"status": "supported_explanation", "evidence": [{"filename": "f", "record_id": "s"}]},
        ],
        "timeline": [{"evidence": {"filename": "f", "record_id": "timeline"}}],
    }
    assert incident_report.citation_order(view) == [
        ("f", "s"),
        ("f", "observed"),
        ("f", "timeline"),
    ]


def test_export_rejects_saved_results_that_differ_from_a_fresh_check(client):
    import copy
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures/incidents/unrelated"
    payload = json.loads((fixture / "request.json").read_text()) | {
        "events_text": (fixture / "events.ndjson").read_text(),
        "deployments_text": (fixture / "deployments.json").read_text(),
    }
    job_id = completed(client, payload)
    route = f"/api/incidents/{job_id}/report"
    with Session() as session:
        original = session.get(Job, job_id).report

    def migration(report):
        return next(f for f in report["findings"] if f["claim_type"] == "migration_explanation")

    def supported(report):
        migration(report)["status"] = "supported_explanation"
        migration(report)["explanation"] = "Missing-column errors match the failed migration."

    def errors(report):
        return next(f for f in report["findings"] if f["claim_type"] == "error_group")

    changes = [
        supported,
        lambda report: report["timeline"][0].update(timestamp="1999-01-01T00:00:00+00:00"),
        lambda report: errors(report)["signature"].update(message="Database column missing"),
        lambda report: errors(report).update(claim_type="migration_explanation"),
        lambda report: errors(report).update(evidence_records_total=99),
        lambda report: report.update(mode="model"),
        lambda report: report["next_checks"].append("Restart the service."),
        lambda report: report.update(timeline="not a list"),
    ]
    for change in changes:
        tampered = copy.deepcopy(original)
        change(tampered)
        with Session.begin() as session:
            session.get(Job, job_id).report = tampered
        response = client.get(route)
        assert response.status_code == 409, tampered
        assert "does not match a fresh check" in response.text
    # The export shows exact source text, so a changed saved excerpt is not rendered.
    excerpt = copy.deepcopy(original)
    excerpt["timeline"][0]["evidence"]["excerpt"] = "older excerpt form"
    with Session.begin() as session:
        session.get(Job, job_id).report = excerpt
    response = client.get(route)
    assert response.status_code == 200 and "older excerpt form" not in response.text
    assert "Mode: scripted." in response.text


def test_export_viewer_and_engine_share_exact_deployment_text(client):
    from html import escape

    first = (
        '{"id": "d\\u0031",\t"timestamp": "2026-11-05T04:00:00-06:00",\r\n'
        '    "service":"checkout", "revision": "v]2,[",  "migration_status": "failed"}'
    )
    second = (
        '{ "id":"d2","timestamp":"2026-11-05T10:05:00Z","service":"checkout",'
        '"revision":"v3","migration_status":"completed" }'
    )
    payload = bundle() | {"deployments_text": f"\t[\r\n\t{first}\r\n ,\r\n{second}\r\n]\r\n"}
    job_id = completed(client, payload)
    route = f"/api/incidents/{job_id}/evidence/deployments.json"
    for index, (record_id, exact) in enumerate([("d1", first), ("d2", second)], 1):
        source = client.get(route, params={"record_id": record_id}).json()
        assert source["raw"] == exact and source["record_index"] == index
    records = section(
        client.get(f"/api/incidents/{job_id}/report").text, "Exact cited source records"
    )
    assert f"<pre>{escape(first)}</pre>" in records and f"<pre>{escape(second)}</pre>" in records
    timeline = client.get(f"/api/jobs/{job_id}").json()["report"]["timeline"]
    assert {row["id"]: row["evidence"]["excerpt"] for row in timeline} == {
        "error-1": escape(payload["events_text"].rstrip("\n")),
        "d1": escape(first),
        "d2": escape(second),
    }


def test_export_time_window_uses_the_normalized_utc_scope(client):
    payload = bundle() | {"start": "2026-11-05T03:50:00-06:00", "end": "2026-11-05T04:20:00-06:00"}
    html = client.get(f"/api/incidents/{completed(client, payload)}/report").text
    assert (
        "Time window: 2026-11-05T09:50:00.000000+00:00 to 2026-11-05T10:20:00.000000+00:00" in html
    )
    assert "-06:00" not in html.split("<h2>")[0]


@pytest.mark.parametrize("job_id", ["%00", "not-a-job", str(uuid4()).upper(), str(uuid4())])
def test_malformed_and_unknown_job_ids_are_not_found(client, job_id):
    for method, route in (
        ("get", f"/api/jobs/{job_id}"),
        ("post", f"/api/jobs/{job_id}/rerun"),
        ("get", f"/api/jobs/{job_id}/repair-report"),
        ("get", f"/api/jobs/{job_id}/evidence/screenshot.png"),
        ("get", f"/api/incidents/{job_id}/report"),
        ("get", f"/api/incidents/{job_id}/evidence/events.ndjson?line=1"),
    ):
        kwargs = {"json": {}} if method == "post" else {}
        response = getattr(client, method)(route, **kwargs)
        assert response.status_code == 404, (route, response.text)
        assert response.json()["detail"] == "Job not found"
