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
            "redirects": state.get("redirects", []),
            "resource_issues": state.get("resource_issues", []),
            "transfer_bytes": state.get("transfer_reserved_bytes", 0),
            "incomplete_layout_pages": list(
                dict.fromkeys(
                    o["url"] for o in state["observations"] if not o.get("layout_complete", True)
                )
            ),
            "browser_limits": "Frames, workers, popups, forms, sockets and data requests are blocked.",
            "external_resources": "Public style sheets, scripts, images, and fonts can load after each address passes the safety checks. Other resource types are blocked.",
            "redirect_policy": "At most three redirects per chain; document redirects stay on the same host or www counterpart, allow HTTPS upgrades, and count toward the five URL limit.",
            "consistency_scope": "Only explicitly identified data-fact-key and data-context facts can be confirmed; other consistency claims remain unverified in both modes.",
            "incomplete_pages": [o["url"] for o in state["observations"] if o.get("error")],
        },
    }
    for status in ["confirmed", "rejected", "unverified"]:
        report[status] = [f for f in findings if f["status"] == status]
    return report
