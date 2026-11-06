"""Portable incident reports backed by the accepted, hash-checked records."""

import hashlib
import json
import re
from html import escape

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from app.core.db import Session
from app.core.models import Job
from app.incidents.parser import timestamp, validate_bundle
from app.v1.services.incidents import load_sources

CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
MAX_REPORT_BYTES = 2_000_000
MAX_RECORD_BYTES = 64_000
MAX_EVIDENCE_BYTES = 512_000
MAX_OUTPUT_BYTES = 4_000_000


def _text(value):
    return escape(str(value), quote=True)


def _deployment_records(text):
    # Preserve the exact JSON text of each record, including its internal spacing.
    decoder = json.JSONDecoder()
    position = text.index("[") + 1
    records = []
    while True:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if text[position] == "]":
            return records
        start = position
        record, position = decoder.raw_decode(text, position)
        records.append((record, text[start:position], len(records) + 1))


def _verified_records(job_id, scope, report):
    sources = load_sources(job_id)
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in sources.items()}
    if hashes != report.get("coverage", {}).get("source_hashes"):
        raise ValueError("Report hashes do not match the accepted sources.")
    accepted = validate_bundle(
        **scope, events_text=sources["events.ndjson"], deployments_text=sources["deployments.json"]
    )
    if any(report.get(key) != accepted[key] for key in scope):
        raise ValueError("Report scope does not match the accepted incident.")
    records = {}
    for line, raw in enumerate(sources["events.ndjson"].splitlines(), 1):
        if raw.strip():
            record = json.loads(raw)
            records[("events.ndjson", record["id"])] = (record, raw, line)
    for record, raw, index in _deployment_records(sources["deployments.json"]):
        records[("deployments.json", record["id"])] = (record, raw, index)
    return hashes, records


def render_incident_report(job_id):
    with Session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        if job.kind != "incident":
            raise HTTPException(422, "This job has no incident report.")
        if job.status != "completed" or not job.report:
            raise HTTPException(409, "The incident report needs a completed check.")
        report, scope, created = job.report, job.incident_input, job.created_at
    if len(json.dumps(report).encode()) > MAX_REPORT_BYTES:
        raise HTTPException(409, "The saved report exceeds the export size limit.")
    try:
        hashes, records = _verified_records(job_id, scope, report)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(409, f"Incident export is unavailable: {exc}") from exc
    pieces = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_text(CSP)}">',
        "<title>Arsune incident report</title><style>body{font:16px sans-serif;max-width:960px;margin:32px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #aaa;margin-top:24px}table{border-collapse:collapse}td,th{padding:8px;text-align:left;vertical-align:top}</style></head><body>",
        "<h1>Incident report</h1>",
        f"<p>Check: {_text(job_id)}. Created: {_text(created)}.</p>",
        f"<p>Service: {_text(scope['service'])}</p>",
        f"<p>Time window: {_text(scope['start'])} to {_text(scope['end'])}</p>",
        f"<p>Description: {_text(scope['description'])}</p>",
        "<p>Mode: scripted. Three specialists read the accepted records. An independent source checker checked each claim. No model or production connection was used.</p>",
        "<p>These records can support a possible cause. No cause was reproduced. This report does not show that the service was repaired.</p>",
        "<h2>Coverage and omissions</h2><ul>",
    ]
    for key, value in report.get("coverage", {}).items():
        if key != "source_hashes":
            pieces.append(f"<li>{_text(key.replace('_', ' '))}: {_text(value)}</li>")
    pieces.append("</ul><h3>Accepted file hashes (SHA-256)</h3><ul>")
    for name, digest in sorted(hashes.items()):
        pieces.append(f"<li>{_text(name)}: <code>{_text(digest)}</code></li>")
    pieces.append("</ul>")
    embedded = {}
    used_bytes = 0
    omitted = 0

    def citation(ref):
        nonlocal used_bytes, omitted
        key = (ref.get("filename"), ref.get("record_id"))
        entry = records.get(key)
        if not entry or ref.get("sha256") != hashes.get(key[0]):
            raise HTTPException(409, "A report citation does not match the accepted sources.")
        record, raw, index = entry
        selector = "line" if key[0] == "events.ndjson" else "record"
        # The parser normalizes timestamps; compare to its normalized representation.
        if ref.get(selector) != index or ref.get("timestamp") != timestamp(
            record["timestamp"], "timestamp"
        ):
            raise HTTPException(
                409, "A report citation has an invalid source position or timestamp."
            )
        label = f"{key[0]}, {selector} {index}, record {key[1]}"
        if key in embedded:
            return f'<a href="#evidence-{embedded[key][0]}">{_text(label)}</a>'
        size = len(raw.encode())
        if size > MAX_RECORD_BYTES or used_bytes + size > MAX_EVIDENCE_BYTES:
            omitted += 1
            return _text(label) + " (exact record omitted: export size limit)"
        number = len(embedded) + 1
        embedded[key] = (number, raw, label)
        used_bytes += size
        return f'<a href="#evidence-{number}">{_text(label)}</a>'

    pieces.append(
        "<h2>Timeline</h2><table><tr><th>Time</th><th>Kind</th><th>Source record</th></tr>"
    )
    for row in report.get("timeline", []):
        pieces.append(
            f"<tr><td>{_text(row['timestamp'])}</td><td>{_text(row['kind'])}</td><td>{citation(row['evidence'])}</td></tr>"
        )
    pieces.append("</table>")
    for status, title in (
        ("observed_fact", "Observed facts"),
        ("supported_explanation", "Supported explanations"),
        ("rejected_explanation", "Rejected explanations"),
        ("could_not_verify", "Could not verify"),
    ):
        pieces.append(f"<h2>{title}</h2>")
        findings = [f for f in report.get("findings", []) if f.get("status") == status]
        if not findings:
            pieces.append("<p>No result in this group.</p>")
        for finding in findings:
            pieces.append(
                f"<section><h3>{_text(finding.get('claim_type', '').replace('_', ' '))}</h3><p>{_text(finding['explanation'])}</p>"
            )
            if finding.get("signature"):
                pieces.append(
                    "<pre>"
                    + _text(json.dumps(finding["signature"], ensure_ascii=False, indent=2))
                    + "</pre>"
                )
            pieces.append(
                f"<p>Source records counted: {_text(finding.get('evidence_records_total', 0))}. Citation records omitted by analysis: {_text(finding.get('evidence_records_omitted', 0))}.</p><ul>"
            )
            for ref in finding.get("evidence", []):
                pieces.append("<li>" + citation(ref) + "</li>")
            pieces.append("</ul></section>")
    for key, title in (("missing_evidence", "Missing evidence"), ("next_checks", "Next checks")):
        pieces.append(f"<h2>{title}</h2><ul>")
        pieces.extend("<li>" + _text(item) + "</li>" for item in report.get(key, []))
        pieces.append("</ul>")
    pieces.append(
        f"<h2>Exact cited source records</h2><p>Only cited records are included. Each record below is exact accepted text. Deployment records retain their internal JSON spacing. Repeated citations share one record. Export limits: {MAX_RECORD_BYTES} bytes per record and {MAX_EVIDENCE_BYTES} bytes total. Citation occurrences omitted by these limits: {omitted}. File hashes identify the complete accepted files; omitted file content is not included.</p>"
    )
    for number, raw, label in embedded.values():
        pieces.append(
            f'<section id="evidence-{number}"><h3>{_text(label)}</h3><pre>{_text(raw)}</pre></section>'
        )
    pieces.append("</body></html>")
    output = "".join(pieces)
    if len(output.encode()) > MAX_OUTPUT_BYTES:
        raise HTTPException(409, "The incident export exceeds the output size limit.")
    # Use only a safe token in the attachment name, even for malformed route input.
    token = re.sub(r"[^A-Za-z0-9-]", "", job_id)[:64]
    return HTMLResponse(
        output,
        headers={
            "Content-Disposition": f'attachment; filename="arsune-incident-{token}.html"',
            "Content-Security-Policy": CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
