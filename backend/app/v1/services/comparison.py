"""Compare saved claims only after fresh checks of their original contexts."""

import hashlib
import json
from copy import deepcopy


def mapped_claim(finding, mapping):
    claim = deepcopy(finding)

    def mapped(url):
        for source, target in mapping.items():
            if url.startswith(source):
                return target + url[len(source) :]
        return url

    claim["urls"] = [mapped(url) for url in claim["urls"]]
    for key in ("source", "target", "url", "page", "image"):
        if key in claim.get("details", {}):
            claim["details"][key] = mapped(claim["details"][key])
    return claim


def identity(finding):
    return hashlib.sha256(
        json.dumps(
            [
                finding.get("role"),
                finding.get("claim_type"),
                sorted(finding.get("urls", [])),
                finding.get("details", {}),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()[:20]


def comparison_report(baseline_id, baseline, mapping, checks, findings):
    result = {
        "baseline_job_id": baseline_id,
        "mapping": mapping,
        "remaining": [],
        "resolved": [],
        "new": [],
        "could_not_recheck": [],
    }
    known = set()
    for original in baseline.get("confirmed", []):
        known.add(identity(mapped_claim(original, mapping)))
        current = checks.get(original["id"])
        group = {"confirmed": "remaining", "rejected": "resolved"}.get(
            (current or {}).get("status"), "could_not_recheck"
        )
        result[group].append(
            {
                "baseline_finding_id": original["id"],
                "finding": original,
                "current_finding": current,
                "reason": (current or {}).get("checker_reason", "No fresh check was available."),
            }
        )
    for finding in findings:
        if finding["status"] == "confirmed" and identity(finding) not in known:
            result["new"].append(
                {
                    "baseline_finding_id": None,
                    "finding": finding,
                    "current_finding": finding,
                    "reason": finding["checker_reason"],
                }
            )
    result["summary"] = {
        key: len(result[key]) for key in ("remaining", "resolved", "new", "could_not_recheck")
    }
    return result
