from app.core.scan import ROLES, VIEWPORTS


def build_report(url, mode, state, blocked):
    findings = list(state["checks"].values())
    report = {
        "site_url": url,
        "mode": mode,
        "provider": state["provider"],
        "inspected_pages": state["selected"],
        "observations": state["observations"],
        "viewports": VIEWPORTS,
        "findings": findings,
        "activity": state["activity"],
        "coverage": {
            "page_limit": 5,
            "selected_count": len(state["selected"]),
            "roles": ROLES,
            "requests": state.get("request_count", 0),
            "scope": "Selected pages only; no forms submitted.",
            "blocked_requests": blocked,
            "external_resources": "External origins are blocked; rendering may be incomplete.",
            "redirect_policy": "Redirects are blocked; submit the final permitted URL as a new scan.",
            "consistency_scope": "Only explicitly identified data-fact-key and data-context facts can be confirmed; other consistency claims remain unverified in both modes.",
            "incomplete_pages": [o["url"] for o in state["observations"] if o.get("error")],
        },
    }
    for status in ["confirmed", "rejected", "unverified"]:
        report[status] = [f for f in findings if f["status"] == status]
    return report
