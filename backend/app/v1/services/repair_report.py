"""A portable report with escaped text and bounded local screenshot evidence."""

import base64
import json
from html import escape

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from app.v1.services.evidence import evidence_path
from app.v1.services.jobs import get_job


def render_repair_report(job_id):
    job = get_job(job_id)
    if job["status"] != "completed" or not job["report"]:
        raise HTTPException(409, "The repair report needs a completed check.")
    report = job["report"]
    pieces = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">",
        "<title>Arsune repair report</title><style>body{font:16px sans-serif;max-width:960px;margin:32px auto;padding:16px}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #aaa;margin-top:24px}</style>",
        "<h1>Website repair report</h1>",
        f"<p>Site: {escape(job['url'])}</p><p>Check: {escape(job_id)}</p>",
        "<p>The site owner makes repairs. This report covers the selected pages only. It does not establish that the whole site is safe or complete.</p>",
        f"<p>Mode: {escape(job['mode'])}. Created: {escape(str(job['created_at']))}</p>",
    ]
    pieces.append(
        "<h2>Selected pages</h2><ul>"
        + "".join("<li>" + escape(url) + "</li>" for url in report.get("inspected_pages", []))
        + "</ul>"
    )
    pieces.append(
        "<p>Page limit: 5. Capture times and screen sizes are listed with each piece of evidence.</p>"
    )
    pieces.append(
        "<details><summary>Full scope and limitations</summary><pre>"
        + escape(json.dumps(report.get("coverage", {}), indent=2))
        + "</pre></details>"
    )
    labels = {
        "remaining": "Still present",
        "resolved": "Resolved",
        "new": "New",
        "could_not_recheck": "Could not recheck",
    }
    comparison = report.get("comparison")
    if comparison:
        pieces.append(
            "<h2>Changes since the baseline</h2><p>Baseline check: "
            + escape(comparison["baseline_job_id"])
            + "</p><ul>"
        )
        for key, label in labels.items():
            pieces.append("<li>" + label + ": " + str(comparison["summary"][key]) + "</li>")
        pieces.append("</ul>")
        if comparison.get("mapping"):
            pieces.append(
                "<p>Controlled demo address change: "
                + escape(json.dumps(comparison["mapping"]))
                + "</p>"
            )
        entries = [
            (
                group,
                entry["finding"],
                entry.get("current_finding"),
                comparison["baseline_job_id"] if group != "new" else job_id,
                entry["reason"],
            )
            for group in ("remaining", "resolved", "new", "could_not_recheck")
            for entry in comparison[group]
        ]
    else:
        entries = [
            (finding["status"], finding, None, job_id, finding.get("checker_reason", ""))
            for finding in report.get("findings", [])
        ]
    embedded = set()
    total_bytes = 0
    for group, finding, current, source_id, reason in entries:
        pieces.append(
            "<section><h2>"
            + escape(labels.get(group, group.replace("_", " ")))
            + ": "
            + escape(finding.get("explanation", finding.get("claim_type", "Finding")))
            + "</h2>"
        )
        pieces.append("<p>" + escape(reason) + "</p>")
        for label, value in (("Original claim", finding), ("Fresh check", current)):
            if not value:
                continue
            evidence_job = job_id if label == "Fresh check" else source_id
            pieces.append("<h3>" + label + "</h3>")
            for title, key in (
                ("Visitor effect", "visitor_impact"),
                ("Suggested repair", "suggested_correction"),
            ):
                pieces.append(
                    "<p><strong>"
                    + title
                    + ": </strong>"
                    + escape(str(value.get(key, "Not supplied.")))
                    + "</p>"
                )
            pieces.append("<p>Affected pages: " + escape(", ".join(value.get("urls", []))) + "</p>")
            pieces.append(
                "<h4>Steps to repeat the check</h4><ol>"
                + "".join(
                    "<li>" + escape(step) + "</li>" for step in value.get("reproduction_steps", [])
                )
                + "</ol>"
            )
            pieces.append(
                "<details><summary>Full evidence record</summary><pre>"
                + escape(json.dumps(value, indent=2))
                + "</pre></details>"
            )
            for observation in value.get("evidence", []):
                pieces.append(
                    "<p>Evidence: "
                    + escape(observation.get("url", ""))
                    + "; captured "
                    + escape(str(observation.get("captured_at", "unknown")))
                    + "; screen size "
                    + escape(str(observation.get("viewport", {})))
                    + "</p>"
                )
                screenshots = [(observation.get("screenshot"), "Page screenshot")]
                for fact in observation.get("facts", []):
                    if fact.get("source") != "visible_price" or fact.get("key") != value.get(
                        "details", {}
                    ).get("key"):
                        continue
                    pieces.append(
                        "<h4>Matched price card</h4><blockquote>"
                        + escape(fact.get("quote", ""))
                        + "</blockquote><p>Context: "
                        + escape(json.dumps(fact.get("context", {}), sort_keys=True))
                        + "; source: "
                        + escape(str(fact.get("selector", "")))
                        + "</p>"
                    )
                    screenshots.append((fact.get("screenshot"), "Price card screenshot"))
                for name, caption in screenshots:
                    pieces.append("<p>" + caption + "</p>")
                    if not name:
                        pieces.append(
                            "<p>No screenshot is available. Use the steps and evidence record above.</p>"
                        )
                        continue
                    if (evidence_job, name) in embedded:
                        pieces.append("<p>This screenshot is shown earlier in the report.</p>")
                        continue
                    try:
                        path = evidence_path(evidence_job, name)
                        if (
                            path.stat().st_size > 4_000_000
                            or total_bytes + path.stat().st_size > 24_000_000
                        ):
                            pieces.append(
                                "<p>The screenshot exceeds the export size limit. See the evidence reference above.</p>"
                            )
                            continue
                        data = path.read_bytes()
                        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                            continue
                    except (HTTPException, OSError):
                        pieces.append(
                            "<p>The screenshot file is unavailable. Use the steps and evidence reference above.</p>"
                        )
                        continue
                    embedded.add((evidence_job, name))
                    total_bytes += len(data)
                    pieces.append(
                        '<img alt="Screenshot evidence" src="data:image/png;base64,'
                        + base64.b64encode(data).decode("ascii")
                        + '">'
                    )
        pieces.append("</section>")
    pieces.append("</html>")
    return HTMLResponse(
        "".join(pieces),
        headers={
            "Content-Disposition": f'attachment; filename="arsune-repair-{job_id}.html"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
        },
    )
