from app.core.scan import ROLES, VIEWPORTS


def build_report(url, mode, state, blocked):
    findings = list(state["checks"].values())
    desktop = [o for o in state["observations"] if o["viewport"] == VIEWPORTS["desktop"]]
    contexts = {}
    exclusions = []
    for observation in desktop:
        for fact in observation.get("facts", []):
            if fact.get("source") != "visible_price":
                continue
            contexts.setdefault(fact["key"], set()).add(observation["url"])
        exclusions.extend(
            {"url": observation["url"], **item} for item in observation.get("price_exclusions", [])
        )
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
            "consistency_scope": "Checks compare explicit fact attributes and visible English price cards with a named plan, an explicit supported ISO currency, one amount, and an exact monthly or yearly billing and unit context. Other text and ambiguous offers are not checked.",
            "price_consistency": {
                "supported_scope": "English plan or product headings; explicit USD, EUR, GBP, CAD, AUD, NZD, CHF, JPY, or INR; one monthly or yearly amount; matching units. Promotions, quantity limits, annual equivalents, ambiguous symbols, and other billing terms are excluded.",
                "extracted_count": sum(
                    f.get("source") == "visible_price" for o in desktop for f in o.get("facts", [])
                ),
                "excluded_count": len(exclusions),
                "exclusions": exclusions,
                "comparable_contexts": sum(len(urls) > 1 for urls in contexts.values()),
                "extraction_truncated_pages": [
                    o["url"] for o in desktop if o.get("price_extraction_truncated")
                ],
            },
            "dropped_suggestions": sum(state.get("dropped_suggestions", {}).values()),
            "incomplete_pages": [o["url"] for o in state["observations"] if o.get("error")],
        },
    }
    for status in ["confirmed", "rejected", "unverified"]:
        report[status] = [f for f in findings if f["status"] == status]
    return report
