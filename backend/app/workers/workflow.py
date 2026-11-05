import asyncio
import inspect
from pathlib import Path

from app.agents.specialists import propose_candidates
from app.browser.checker import check_candidate
from app.browser.observations import observe_page
from app.browser.policy import document_scope, origin, validate_target
from app.browser.session import inspection_browser
from app.core.scan import VIEWPORTS
from app.v1.services.comparison import comparison_report, identity, mapped_claim
from app.v1.services.reports import build_report


async def run_scan(
    url: str,
    output_dir: Path,
    *,
    mode="scripted",
    inject_demo_false_positive=False,
    checkpoint=None,
    save_checkpoint=lambda state: None,
    emit_event=lambda role, message: None,
    baseline_report=None,
    baseline_job_id=None,
    comparison_mapping=None,
):
    async with asyncio.timeout(240):
        return await _scan(
            url,
            Path(output_dir),
            mode,
            inject_demo_false_positive,
            checkpoint,
            save_checkpoint,
            emit_event,
            baseline_report,
            baseline_job_id,
            comparison_mapping or {},
        )


async def _scan(
    url,
    output_dir,
    mode,
    inject,
    checkpoint,
    save_checkpoint,
    emit_event,
    baseline_report,
    baseline_job_id,
    mapping,
):
    url = await validate_target(url)
    if mode not in {"scripted", "model"}:
        raise ValueError("Mode must be scripted or model.")
    from app.core.config import settings

    demo = settings.demo_url
    if mapping and (
        mapping != {demo.rstrip("/") + "/faulty/": demo.rstrip("/") + "/corrected/"}
        or url != demo.rstrip("/") + "/corrected/"
        or (baseline_report or {}).get("site_url") != demo.rstrip("/") + "/faulty/"
    ):
        raise ValueError("Only the explicit controlled demo mapping is permitted.")
    if inject and origin(url) != origin(demo):
        raise ValueError("The false candidate is permitted only on the configured demo origin.")
    selected = [url]
    if baseline_report:
        # Reserve the old claim pages before ordinary discovery uses the URL budget.
        required = [
            u
            for f in baseline_report.get("confirmed", [])
            for u in mapped_claim(f, mapping)["urls"]
        ]
        required += [
            mapped_claim({"urls": [u]}, mapping)["urls"][0]
            for u in baseline_report.get("inspected_pages", [])
        ]
        for target in required:
            if target not in selected and len(selected) < 5 and document_scope(url, target):
                selected.append(target)
    state = checkpoint or {"selected": selected, "observations": [], "checks": {}, "activity": []}
    if not isinstance(state.get("selected"), list) or len(state["selected"]) > 5:
        raise ValueError("Invalid checkpoint scope.")
    for selected_url in state["selected"]:
        if not document_scope(url, selected_url):
            raise ValueError("Checkpoint includes an unrelated origin.")
        await validate_target(selected_url)
    output_dir.mkdir(parents=True, exist_ok=True)

    async def save():
        value = save_checkpoint(state)
        if inspect.isawaitable(value):
            await value

    async def event(role, message):
        state["activity"].append({"role": role, "message": message})
        value = emit_event(role, message)
        if inspect.isawaitable(value):
            await value

    async with inspection_browser(url, demo, state, save) as (context, blocked):

        async def observe(target, viewport):
            observation = await observe_page(context, target, viewport, output_dir, url)
            for issue in observation.get("resource_issues", []):
                if issue not in state["resource_issues"] and len(state["resource_issues"]) < 400:
                    state["resource_issues"].append(issue)
            await save()
            return observation

        index = 0
        while index < len(state["selected"]):
            target = state["selected"][index]
            for viewport in VIEWPORTS.values():
                if not any(
                    o["url"] == target and o["viewport"] == viewport for o in state["observations"]
                ):
                    await event("coordinator", f"Inspecting {target} at width {viewport['width']}.")
                    observation = await observe(target, viewport)
                    state["observations"].append(observation)
                    if viewport == VIEWPORTS["desktop"]:
                        for link in observation["links"]:
                            if len(state["selected"]) >= 5:
                                break
                            if link not in state["selected"]:
                                state["selected"].append(link)
                    await save()
            index += 1
        if baseline_report:
            rechecks = state.setdefault("baseline_checks", {})
            for original in baseline_report.get("confirmed", []):
                if original["id"] in rechecks:
                    continue
                item = mapped_claim(original, mapping)
                if any(u not in state["selected"] for u in item["urls"]):
                    rechecks[original["id"]] = {
                        **item,
                        "status": "unverified",
                        "evidence": [],
                        "checker_reason": "A required baseline page is outside the selected scope or URL budget.",
                    }
                else:
                    await event(
                        "checker", f"Checking baseline claim {original['id']} with fresh evidence."
                    )
                    rechecks[original["id"]] = await check_candidate(item, observe)
                    current = rechecks[original["id"]]
                    previous_destinations = {
                        mapped_claim({"urls": [o["url"]]}, mapping)["urls"][0]: mapped_claim(
                            {"urls": [o.get("final_url", o["url"])]}, mapping
                        )["urls"][0]
                        for o in original.get("evidence", [])
                    }
                    if any(
                        o.get("final_url", o["url"])
                        != previous_destinations.get(o["url"], o["url"])
                        for o in current.get("evidence", [])
                    ):
                        current["status"] = "unverified"
                        current["visitor_impact"] = "The effect on visitors is not known."
                        current["suggested_correction"] = (
                            "Check the original page context before deciding on a repair."
                        )
                        current["checker_reason"] = (
                            "The page destination changed. The original page context could not be checked again."
                        )
                current = rechecks[original["id"]]
                if current["status"] == "confirmed":
                    current_id = identity(item)
                    state["checks"][current_id] = {**current, "id": current_id}
                await save()
        if "candidates" not in state:
            await propose_candidates(state, mode, inject, url, save, event)
        for item in state["candidates"]:
            if item["id"] in state["checks"]:
                continue
            await event("checker", f"Repeating the {item['claim_type']} check.")
            state["checks"][item["id"]] = await check_candidate(item, observe)
            await save()
    report = build_report(url, mode, state, blocked)
    if baseline_report:
        report["comparison"] = comparison_report(
            baseline_job_id,
            baseline_report,
            mapping,
            state.get("baseline_checks", {}),
            report["findings"],
        )
    return report
