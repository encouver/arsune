import asyncio
import inspect
from pathlib import Path

from app.agents.specialists import propose_candidates
from app.browser.checker import check_candidate
from app.browser.observations import observe_page
from app.browser.policy import origin, validate_target
from app.browser.session import inspection_browser
from app.core.scan import VIEWPORTS
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
        )


async def _scan(url, output_dir, mode, inject, checkpoint, save_checkpoint, emit_event):
    url = await validate_target(url)
    if mode not in {"scripted", "model"}:
        raise ValueError("Mode must be scripted or model.")
    from app.core.config import settings

    demo = settings.demo_url
    if inject and origin(url) != origin(demo):
        raise ValueError("The false candidate is permitted only on the configured demo origin.")
    state = checkpoint or {"selected": [url], "observations": [], "checks": {}, "activity": []}
    if not isinstance(state.get("selected"), list) or len(state["selected"]) > 5:
        raise ValueError("Invalid checkpoint scope.")
    for selected_url in state["selected"]:
        if origin(selected_url) != origin(url):
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
            return await observe_page(context, target, viewport, output_dir, url)

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
        if "candidates" not in state:
            await propose_candidates(state, mode, inject, url, save, event)
        for item in state["candidates"]:
            if item["id"] in state["checks"]:
                continue
            await event("checker", f"Repeating the {item['claim_type']} check.")
            state["checks"][item["id"]] = await check_candidate(item, observe)
            await save()
    return build_report(url, mode, state, blocked)
