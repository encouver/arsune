from app.agents.candidates import candidate, scripted, validate_candidates
from app.agents.model import generate_candidates
from app.core.scan import ROLES


async def propose_candidates(state, mode, inject, url, save, event):
    candidates = []
    state.setdefault("role_outputs", {})
    state.setdefault("model_reservations", {})
    provider = {"name": "scripted", "live_call": False}
    for role in ROLES:
        if role not in state["role_outputs"]:
            await event(role, "Examining the stored browser observations.")
            if mode == "model":
                if state["model_reservations"].get(role):
                    raise ValueError(
                        "A previous model call has an uncertain charge. Start a new authorized job."
                    )
                state["model_reservations"][role] = True
                await save()
                role_candidates, provider = await generate_candidates(
                    state["observations"], role=role
                )
                if any(c.get("role") != role for c in role_candidates):
                    raise ValueError("Model specialist returned another role's candidate.")
                state.setdefault("model_providers", {})[role] = provider
            else:
                role_candidates = [c for c in scripted(state["observations"]) if c["role"] == role]
            state["role_outputs"][role] = validate_candidates(role_candidates, state["selected"])
            await event(role, f"Proposed {len(role_candidates)} candidates.")
            await save()
        candidates.extend(state["role_outputs"][role])
    if mode == "model":
        provider = {
            "name": "compatible-model",
            "live_call": True,
            "specialists": state["model_providers"],
            "reserved_cost_usd": sum(
                p["reserved_cost_usd"] for p in state["model_providers"].values()
            ),
        }
    if inject:
        healthy = next((u for u in state["selected"] if "/healthy" in u), None)
        if healthy:
            candidates.append(
                candidate(
                    "navigation",
                    "broken_link",
                    [url, healthy],
                    "Injected demo claim: the healthy link returns HTTP 404.",
                    {"source": url, "target": healthy},
                    True,
                )
            )
    state["candidates"] = validate_candidates(candidates, state["selected"])
    state["provider"] = provider
    await save()
