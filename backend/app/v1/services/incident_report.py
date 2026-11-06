"""Portable incident reports rendered from a fresh scripted check of the accepted sources."""

import json
from html import escape

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from app.core.db import Session
from app.incidents.engine import recompute
from app.incidents.parser import validate_bundle
from app.v1.services.incidents import load_sources
from app.v1.services.jobs import find_job

CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
# Both evidence limits count escaped HTML bytes, which is what a record adds to the file.
MAX_RECORD_BYTES = 64_000
MAX_EVIDENCE_BYTES = 512_000
# The engine bounds everything outside the evidence budget: 100 timeline rows, at most 42
# findings with 20 citations each, 40 signatures of at most 4,600 characters (at most 7 bytes
# per character after JSON and HTML escaping, about 1.3 MB), record IDs of at most 200 safe
# ASCII characters, and a 2,000-character description. That part stays below 2 MB, so a valid
# job stays below 2.6 MB with the evidence budget; this limit is only a backstop.
MAX_OUTPUT_BYTES = 4_000_000
REPORT_FIELDS = (
    "description",
    "service",
    "start",
    "end",
    "mode",
    "provider",
    "coverage",
    "missing_evidence",
    "next_checks",
)
FINDING_FIELDS = (
    "status",
    "claim_type",
    "explanation",
    "signature",
    "evidence_records_total",
    "evidence_records_omitted",
)
GROUPS = (
    ("observed_fact", "Observed facts"),
    ("supported_explanation", "Supported explanations"),
    ("rejected_explanation", "Rejected explanations"),
    ("could_not_verify", "Could not verify"),
)
BUDGET_ORDER = (
    "supported_explanation",
    "observed_fact",
    "rejected_explanation",
    "could_not_verify",
)
HEAD = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    f'<meta http-equiv="Content-Security-Policy" content="{escape(CSP)}">'
    "<title>Arsune incident report</title><style>body{font:16px sans-serif;max-width:960px;margin:32px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #aaa;margin-top:24px}table{border-collapse:collapse}td,th{padding:8px;text-align:left;vertical-align:top}</style></head><body>"
)
# A saved report renders only if it matches the scripted recheck, so these statements hold.
PROCESS = (
    "Three specialists read the accepted records. An independent source checker checked each "
    "claim. This export ran the same checks again on the accepted files and matched the saved "
    "report. No model or production connection was used."
)


def _text(value):
    return escape(str(value), quote=True)


def render_incident_report(job_id):
    report, scope, created = _completed_report(job_id)
    try:
        bundle = _accepted_bundle(job_id, scope)
        view = verified_view(report, bundle)
    except ValueError as exc:
        raise HTTPException(409, f"Incident export is unavailable: {exc}") from exc
    output = document(job_id, created, view, bundle)
    if len(output.encode()) > MAX_OUTPUT_BYTES:
        raise HTTPException(409, "The incident export exceeds the output size limit.")
    return HTMLResponse(
        output,
        headers={
            "Content-Disposition": f'attachment; filename="arsune-incident-{job_id}.html"',
            "Content-Security-Policy": CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def _completed_report(job_id):
    with Session() as session:
        job = find_job(session, job_id)
        if job.kind != "incident":
            raise HTTPException(422, "This job has no incident report.")
        if job.status != "completed" or not job.report:
            raise HTTPException(409, "The incident report needs a completed check.")
        return job.report, job.incident_input, job.created_at


def _accepted_bundle(job_id, scope):
    sources = load_sources(job_id)
    return validate_bundle(
        **scope, events_text=sources["events.ndjson"], deployments_text=sources["deployments.json"]
    )


def verified_view(report, bundle):
    expected = _view(recompute(bundle))
    try:
        saved = _view(report)
    except (AttributeError, KeyError, TypeError):
        saved = None
    if saved != expected:
        raise ValueError("the saved report does not match a fresh check of the accepted sources.")
    return expected


def _citation(ref):
    # The export prints exact source text, never the saved excerpt, so excerpts are not compared.
    return {key: value for key, value in ref.items() if key != "excerpt"}


def _view(report):
    """Keep only what the export renders, so a match covers every rendered value."""
    return {key: report.get(key) for key in REPORT_FIELDS} | {
        "timeline": [
            {
                "timestamp": row["timestamp"],
                "kind": row["kind"],
                "evidence": _citation(row["evidence"]),
            }
            for row in report["timeline"]
        ],
        "findings": [
            {key: finding.get(key) for key in FINDING_FIELDS}
            | {"evidence": [_citation(ref) for ref in finding["evidence"]]}
            for finding in report["findings"]
        ],
    }


def _source_records(bundle):
    """Map each accepted record to its citation label and exact source text."""
    records = {}
    for filename, name, selector, position in (
        ("events.ndjson", "events", "line", "_line"),
        ("deployments.json", "deployments", "record", "_record"),
    ):
        for record in bundle[name]:
            label = f"{filename}, {selector} {record[position]}, record {record['id']}"
            records[(filename, record["id"])] = (label, record["_text"])
    return records


def _key(ref):
    return ref["filename"], ref["record_id"]


def citation_order(view):
    """List citation occurrences with finding evidence first, then the timeline."""
    keys = [
        _key(ref)
        for status in BUDGET_ORDER
        for finding in view["findings"]
        if finding["status"] == status
        for ref in finding["evidence"]
    ]
    return keys + [_key(row["evidence"]) for row in view["timeline"]]


def embed_records(keys, records):
    """Choose the cited records that fit the limits; return them and the omitted occurrences."""
    embedded, skipped, used = {}, set(), 0
    for key in keys:
        if key in embedded or key in skipped:
            continue
        escaped = _text(records[key][1])
        size = len(escaped.encode())
        if size > MAX_RECORD_BYTES or used + size > MAX_EVIDENCE_BYTES:
            skipped.add(key)
            continue
        embedded[key] = (len(embedded) + 1, escaped)
        used += size
    return embedded, sum(key not in embedded for key in keys)


def _links(keys, records, embedded):
    links = {}
    for key in set(keys):
        label = _text(records[key][0])
        links[key] = (
            f'<a href="#evidence-{embedded[key][0]}">{label}</a>'
            if key in embedded
            else label + " (exact record omitted: export size limit)"
        )
    return links


def document(job_id, created, view, bundle):
    records = _source_records(bundle)
    keys = citation_order(view)
    embedded, omitted = embed_records(keys, records)
    links = _links(keys, records, embedded)
    return "".join(
        [
            HEAD,
            _summary(job_id, created, view),
            _coverage(view["coverage"]),
            _timeline(view["timeline"], links),
            _findings(view["findings"], links),
            _notes(view),
            _exact_records(embedded, records, omitted),
            "</body></html>",
        ]
    )


def _summary(job_id, created, view):
    return (
        "<h1>Incident report</h1>"
        f"<p>Check: {_text(job_id)}. Created: {_text(created)}.</p>"
        f"<p>Service: {_text(view['service'])}</p>"
        f"<p>Time window: {_text(view['start'])} to {_text(view['end'])}</p>"
        f"<p>Description: {_text(view['description'])}</p>"
        f"<p>Mode: {_text(view['mode'])}. {PROCESS}</p>"
        "<p>These records can support a possible cause. No cause was reproduced. This report does not show that the service was repaired.</p>"
    )


def _coverage(coverage):
    items = "".join(
        f"<li>{_text(key.replace('_', ' '))}: {_text(value)}</li>"
        for key, value in coverage.items()
        if key != "source_hashes"
    )
    hashes = "".join(
        f"<li>{_text(name)}: <code>{_text(sha)}</code></li>"
        for name, sha in sorted(coverage["source_hashes"].items())
    )
    return (
        f"<h2>Coverage and omissions</h2><ul>{items}</ul>"
        f"<h3>Accepted file hashes (SHA-256)</h3><ul>{hashes}</ul>"
    )


def _timeline(rows, links):
    body = "".join(
        f"<tr><td>{_text(row['timestamp'])}</td><td>{_text(row['kind'])}</td>"
        f"<td>{links[_key(row['evidence'])]}</td></tr>"
        for row in rows
    )
    return (
        "<h2>Timeline</h2><table><tr><th>Time</th><th>Kind</th><th>Source record</th></tr>"
        + body
        + "</table>"
    )


def _findings(findings, links):
    pieces = []
    for status, title in GROUPS:
        group = [_finding(f, links) for f in findings if f["status"] == status]
        pieces.append(f"<h2>{title}</h2>" + ("".join(group) or "<p>No result in this group.</p>"))
    return "".join(pieces)


def _finding(finding, links):
    signature = ""
    if finding["signature"]:
        signature = (
            "<pre>"
            + _text(json.dumps(finding["signature"], ensure_ascii=False, indent=2))
            + "</pre>"
        )
    citations = "".join(f"<li>{links[_key(ref)]}</li>" for ref in finding["evidence"])
    return (
        f"<section><h3>{_text(finding['claim_type'].replace('_', ' '))}</h3>"
        f"<p>{_text(finding['explanation'])}</p>{signature}"
        f"<p>Source records counted: {_text(finding['evidence_records_total'])}. "
        f"Citation records omitted by analysis: {_text(finding['evidence_records_omitted'])}.</p>"
        f"<ul>{citations}</ul></section>"
    )


def _notes(view):
    return "".join(
        f"<h2>{title}</h2><ul>" + "".join(f"<li>{_text(item)}</li>" for item in view[key]) + "</ul>"
        for key, title in (("missing_evidence", "Missing evidence"), ("next_checks", "Next checks"))
    )


def _exact_records(embedded, records, omitted):
    sections = "".join(
        f'<section id="evidence-{number}"><h3>{_text(records[key][0])}</h3><pre>{escaped}</pre></section>'
        for key, (number, escaped) in embedded.items()
    )
    return (
        "<h2>Exact cited source records</h2><p>Only cited records are included. Each record "
        "below is exact accepted text. Deployment records retain their internal JSON spacing. "
        "Repeated citations share one record. Finding evidence receives the size budget before "
        f"timeline records. Export limits, measured as escaped HTML: {MAX_RECORD_BYTES} bytes "
        f"per record and {MAX_EVIDENCE_BYTES} bytes total. Citation occurrences omitted by "
        f"these limits: {omitted}. File hashes identify the complete accepted files; omitted "
        "file content is not included.</p>" + sections
    )
