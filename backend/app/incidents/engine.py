"""Scripted specialists and a checker that reads the original sources again."""

import copy
import hashlib
import html
import inspect
import json
from datetime import datetime

from .parser import digest, validate_bundle


def stable_id(kind, context):
    return (
        kind + "-" + hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:20]
    )


def identity(bundle):
    return stable_id(
        "bundle",
        {k: bundle[k] for k in ("description", "service", "start", "end")}
        | {"hashes": {name: source["sha256"] for name, source in bundle["sources"].items()}},
    )


async def read_sources(bundle, source_loader=None):
    texts = (
        source_loader()
        if source_loader
        else {name: source["text"] for name, source in bundle["sources"].items()}
    )
    if inspect.isawaitable(texts):
        texts = await texts
    if not isinstance(texts, dict) or set(texts) != {"events.ndjson", "deployments.json"}:
        raise ValueError("The two original incident sources are required.")
    for filename, text in texts.items():
        if not isinstance(text, str) or digest(text) != bundle["sources"][filename]["sha256"]:
            raise ValueError(f"{filename}: source hash does not match the accepted source.")
    return validate_bundle(
        bundle["description"],
        bundle["service"],
        bundle["start"],
        bundle["end"],
        texts["events.ndjson"],
        texts["deployments.json"],
    )


def selected(bundle, name):
    start, end = datetime.fromisoformat(bundle["start"]), datetime.fromisoformat(bundle["end"])
    return sorted(
        (
            r
            for r in bundle[name]
            if r["service"] == bundle["service"]
            and start <= datetime.fromisoformat(r["timestamp"]) <= end
        ),
        key=lambda r: (r["timestamp"], r["id"]),
    )


def reference(bundle, record, filename):
    if filename == "events.ndjson":
        excerpt = bundle["sources"][filename]["text"].splitlines()[record["_line"] - 1]
    else:
        excerpt = json.dumps(
            json.loads(bundle["sources"][filename]["text"])[record["_record"] - 1],
            ensure_ascii=False,
            sort_keys=True,
        )
    return {
        "filename": filename,
        "sha256": bundle["sources"][filename]["sha256"],
        "line": record.get("_line"),
        "record": record.get("_record"),
        "record_id": record["id"],
        "timestamp": record["timestamp"],
        "excerpt": html.escape(excerpt[:400], quote=True),
    }


def signature(record):
    return {key: record.get(key) for key in ("error_code", "message", "revision", "column")}


def propose(bundle, role):
    events, deployments = selected(bundle, "events"), selected(bundle, "deployments")
    errors = [r for r in events if r["level"] in {"error", "critical"}]
    if role == "timeline":
        return [
            {
                "role": role,
                "claim_type": "timeline",
                "count": len(events),
                "first_failure": errors[0]["id"] if errors else None,
            }
        ]
    if role == "change":
        return [{"role": role, "claim_type": "changes", "count": len(deployments)}]
    groups = {}
    for record in errors:
        key = json.dumps(signature(record), sort_keys=True)
        groups.setdefault(key, []).append(record)
    result = []
    for key, records in sorted(groups.items())[:20]:
        result.append(
            {
                "role": role,
                "claim_type": "error_group",
                "signature": json.loads(key),
                "count": len(records),
            }
        )
    missing = [
        json.loads(key)
        for key in sorted(groups)
        if json.loads(key)["error_code"] == "missing_column"
    ][:20]
    for context in missing or [None]:
        result.append({"role": role, "claim_type": "migration_explanation", "signature": context})
    return result


def _refs(bundle, records, filename):
    return [reference(bundle, record, filename) for record in records[:10]]


async def check_candidate(candidate, bundle, source_loader=None):
    """Never use specialist counts, statements, or saved checks as source evidence."""
    fresh = await read_sources(bundle, source_loader)
    events, deployments = selected(fresh, "events"), selected(fresh, "deployments")
    failures = [r for r in events if r["level"] in {"error", "critical"}]
    kind = candidate.get("claim_type")
    status, explanation, evidence = "could_not_verify", "This claim type is not supported.", []
    valid = True
    evidence_total = 0
    if kind == "timeline":
        valid = candidate.get("count") == len(events) and candidate.get("first_failure") == (
            failures[0]["id"] if failures else None
        )
        explanation = f"{len(events)} events are in the selected service and time window."
        if failures:
            explanation += f" The first recorded failure is {failures[0]['id']}."
        evidence = _refs(fresh, ([failures[0]] if failures else events[:1]), "events.ndjson")
        evidence_total = len(evidence)
        status = "observed_fact"
    elif kind == "changes":
        valid = candidate.get("count") == len(deployments)
        explanation = (
            f"{len(deployments)} deployment records are in the selected service and time window."
        )
        evidence = _refs(fresh, deployments, "deployments.json")
        evidence_total = len(deployments)
        status = "observed_fact"
    elif kind == "error_group":
        matches = [r for r in failures if signature(r) == candidate.get("signature")]
        valid = bool(matches) and candidate.get("count") == len(matches)
        evidence_total = len(matches)
        requests = {r["request_id"] for r in matches if r.get("request_id")}
        explanation = f"{len(matches)} errors have this exact signature; {len(requests)} distinct request IDs are recorded."
        evidence = _refs(fresh, matches, "events.ndjson")
        status = "observed_fact"
    elif kind == "migration_explanation":
        context = candidate.get("signature")
        matches = [
            r
            for r in failures
            if signature(r) == context and r.get("error_code") == "missing_column"
        ]
        if context is not None and not matches:
            valid = False
        evidence = _refs(fresh, matches, "events.ndjson")
        evidence_total = len(matches)
        if not matches:
            status = "rejected_explanation" if failures else "could_not_verify"
            explanation = "No missing-column failure is recorded. Deployment timing alone does not support this explanation."
        elif not context.get("column") or not context.get("revision"):
            explanation = "The error records do not identify both a column and a revision."
        else:
            applicable = []
            ambiguous = []
            for error in matches:
                prior = [
                    d
                    for d in deployments
                    if d["revision"] == error["revision"] and d["timestamp"] <= error["timestamp"]
                ]
                if prior:
                    latest = [d for d in prior if d["timestamp"] == prior[-1]["timestamp"]]
                    contexts = {(d["migration_status"], d.get("migration_column")) for d in latest}
                    if len(contexts) > 1:
                        ambiguous.extend(latest)
                    applicable.append(prior[-1])
            evidence += _refs(
                fresh, list({d["id"]: d for d in applicable}.values()), "deployments.json"
            )
            evidence_total += len({d["id"] for d in applicable + ambiguous})
            if ambiguous:
                evidence += _refs(
                    fresh, list({d["id"]: d for d in ambiguous}.values()), "deployments.json"
                )
                explanation = "Deployment records at the latest matching timestamp conflict."
            elif len(applicable) != len(matches):
                explanation = "A matching deployment record before each failure is missing."
            elif any(d["migration_status"] in {"completed", "not_required"} for d in applicable):
                status = "rejected_explanation"
                explanation = "The matching deployment records contradict the claim that its required migration failed."
            elif all(
                d["migration_status"] == "failed" and d.get("migration_column") == context["column"]
                for d in applicable
            ):
                status = "supported_explanation"
                explanation = "Missing-column errors match the service, revision, column, and failed migration. These records support a possible cause; no failure was reproduced."
            else:
                explanation = "The migration records do not identify the same column as the errors."
    evidence = list({(ref["filename"], ref["record_id"]): ref for ref in evidence}.values())
    if not valid:
        status = "rejected_explanation"
        explanation = "The specialist claim does not match the original source records."
    context = {key: value for key, value in candidate.items() if key != "id"}
    expected_id = stable_id("claim", context)
    if candidate.get("id", expected_id) != expected_id:
        status, explanation = (
            "rejected_explanation",
            "The candidate identifier does not match its content.",
        )
    return {
        **context,
        "id": expected_id,
        "status": status,
        "explanation": explanation,
        "checker_reason": explanation,
        "evidence": evidence[:20],
        "evidence_limit": 10 if kind in {"error_group", "changes"} else 20,
        "evidence_records_total": evidence_total,
        "evidence_records_omitted": max(0, evidence_total - len(evidence[:20])),
    }


async def _call(callback, *args):
    if callback:
        value = callback(*args)
        if inspect.isawaitable(value):
            await value


async def run_incident(
    bundle, checkpoint=None, save_checkpoint=None, emit_event=None, source_loader=None
):
    fresh = await read_sources(bundle, source_loader)
    source_identity = identity(fresh)
    state = (
        copy.deepcopy(checkpoint)
        if checkpoint
        else {"source_identity": source_identity, "stages": {}, "activity": []}
    )
    if state.get("source_identity") != source_identity:
        raise ValueError("Checkpoint does not match the accepted source hashes and incident scope.")
    for role in ("timeline", "error", "change"):
        if role not in state["stages"]:
            candidates = propose(fresh, role)
            for candidate in candidates:
                candidate["id"] = stable_id("claim", candidate)
            state["stages"][role] = candidates
            message = f"The {role} specialist read the selected source records."
            state["activity"].append({"role": role, "message": message})
            await _call(emit_event, role, message)
            await _call(save_checkpoint, copy.deepcopy(state))
    candidates = [c for role in ("timeline", "error", "change") for c in state["stages"][role]]
    if len(candidates) > 43:
        raise ValueError("Checkpoint exceeds the candidate limit.")
    findings = [await check_candidate(candidate, bundle, source_loader) for candidate in candidates]
    if not state.get("checker_completed"):
        message = "The checker read the original sources again and checked each claim."
        state["activity"].append({"role": "checker", "message": message})
        state["checker_completed"] = True
        await _call(emit_event, "checker", message)
        await _call(save_checkpoint, copy.deepcopy(state))
    events, deployments = selected(fresh, "events"), selected(fresh, "deployments")
    timeline = [
        {
            "id": r["id"],
            "timestamp": r["timestamp"],
            "kind": kind,
            "evidence": reference(fresh, r, filename),
        }
        for records, kind, filename in [
            (events, "event", "events.ndjson"),
            (deployments, "deployment", "deployments.json"),
        ]
        for r in records
    ]
    timeline.sort(key=lambda r: (r["timestamp"], r["kind"], r["id"]))
    missing = [f["explanation"] for f in findings if f["status"] == "could_not_verify"]
    missing.append("Logs do not prove complete request coverage or reproduce a cause.")
    return {key: fresh[key] for key in ("description", "service", "start", "end")} | {
        "mode": "scripted",
        "provider": None,
        "timeline": timeline[:100],
        "findings": findings,
        "observations": [f for f in findings if f["status"] == "observed_fact"],
        "explanations": [f for f in findings if f["claim_type"] == "migration_explanation"],
        "missing_evidence": missing,
        "next_checks": [
            "Check the migration and schema in a separate controlled environment.",
            "Collect request coverage before estimating impact.",
        ],
        "coverage": {
            "accepted_events": len(fresh["events"]),
            "selected_events": len(events),
            "excluded_events": len(fresh["events"]) - len(events),
            "accepted_deployments": len(fresh["deployments"]),
            "selected_deployments": len(deployments),
            "excluded_deployments": len(fresh["deployments"]) - len(deployments),
            "timeline_total": len(timeline),
            "timeline_limit": 100,
            "error_group_limit": 20,
            "timeline_omitted": max(0, len(timeline) - 100),
            "error_groups_total": len(
                {
                    json.dumps(signature(r), sort_keys=True)
                    for r in events
                    if r["level"] in {"error", "critical"}
                }
            ),
            "error_groups_omitted": max(
                0,
                len(
                    {
                        json.dumps(signature(r), sort_keys=True)
                        for r in events
                        if r["level"] in {"error", "critical"}
                    }
                )
                - 20,
            ),
            "source_hashes": {name: value["sha256"] for name, value in fresh["sources"].items()},
        },
        "activity": state["activity"],
        "reproduced_cause": False,
    }
