from app.core.scan import VIEWPORTS


async def check_candidate(item, observe):
    fresh = [
        await observe(
            u,
            VIEWPORTS["mobile"]
            if item["claim_type"] == "horizontal_overflow"
            else VIEWPORTS["desktop"],
        )
        for u in item["urls"]
    ]
    status, reason = (
        "unverified",
        "The supplied claim could not be checked with the supported measurements.",
    )
    details = item.get("details", {})
    if all(o.get("status") is not None for o in fresh):
        if item["claim_type"] == "broken_link":
            source = next((o for o in fresh if o["url"] == details.get("source")), None)
            target = next((o for o in fresh if o["url"] == details.get("target")), None)
            if (
                source
                and target
                and 200 <= source["status"] < 300
                and source.get("layout_complete", True)
                and target["url"] in source["links"]
            ):
                status = (
                    "confirmed"
                    if target["status"] >= 400
                    else "rejected"
                    if 200 <= target["status"] < 300 and target.get("layout_complete", True)
                    else "unverified"
                )
                reason = f"A fresh source-page read found the link. A fresh target request returned HTTP {target['status']}."
        elif item["claim_type"] == "horizontal_overflow":
            if 200 <= fresh[0]["status"] < 300 and fresh[0].get("layout_complete", True):
                width = fresh[0].get("scroll_width", 0)
                status = "confirmed" if width > 392 else "rejected"
                reason = (
                    f"Fresh measurement: content width {width} pixels; viewport width 390 pixels."
                )
        elif (
            item["claim_type"] == "price_mismatch"
            and len(fresh) == 2
            and all(200 <= o["status"] < 300 and o.get("layout_complete", True) for o in fresh)
        ):
            values = [
                [f["value"] for f in o["facts"] if f["key"] == details.get("key")] for o in fresh
            ]
            facts = [[f for f in o["facts"] if f["key"] == details.get("key")] for o in fresh]
            ordinary_complete = not any(
                f.get("source") == "visible_price"
                and not all(
                    f.get(field) for field in ("context", "quote", "selector", "screenshot")
                )
                for group in facts
                for f in group
            )
            same_context = len(facts[0]) == len(facts[1]) == 1 and facts[0][0].get(
                "context"
            ) == facts[1][0].get("context")
            if all(len(v) == 1 for v in values) and ordinary_complete and same_context:
                status = "confirmed" if values[0] != values[1] else "rejected"
                fact = next(f for f in fresh[0]["facts"] if f["key"] == details["key"])
                context = fact.get("context")
                label = (
                    f"{context['name']}, {context['currency']}, per {context['period']}, unit {context['unit']}"
                    if context
                    else details["key"]
                )
                reason = f"Fresh page reads for {label}: {values[0][0]} and {values[1][0]}."
    if item["claim_type"] == "horizontal_overflow" and any(
        not o.get("layout_complete", True) for o in fresh
    ):
        status, reason = (
            "unverified",
            "Required page resources were unavailable or did not become ready.",
        )
    result = {
        **item,
        "status": status,
        "checker_reason": reason,
        "evidence": fresh,
        "visitor_impact": "Visitors can have difficulty using this page or choosing the stated offer.",
        "suggested_correction": {
            "broken_link": "Repair the link or restore the destination page.",
            "horizontal_overflow": "Make the content fit the mobile viewport.",
            "price_mismatch": "Use the same value for the same product and billing context.",
        }.get(item["claim_type"], "Review the supplied reproduction steps."),
    }
    if status == "rejected":
        result["visitor_impact"] = "The fresh check did not reproduce this claimed fault."
        result["suggested_correction"] = "No repair is needed for this tested claim."
    elif status == "unverified":
        result["visitor_impact"] = "The effect on visitors is not known."
        result["suggested_correction"] = "Collect more evidence before deciding on a repair."
    return result
