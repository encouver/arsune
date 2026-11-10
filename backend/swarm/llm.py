"""The swarm's own model client: settings, a per-run USD budget, and strict JSON answers.

AI agents call an OpenAI-compatible chat API (NEAR AI Cloud by default). This client is
separate from Arsune's judge model on purpose: an agent never uses Arsune's internals. The
model sees only page summaries and tip offers. It never sees keys, and its answers are only
data that the agent validates before anything is signed.
"""

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass, field

import httpx
from app.core.config import ENV_FILE
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

REASON_CHARS = 140
MAX_RESPONSE_BYTES = 65_536
DEFAULT_TIMEOUT_SECONDS = 60.0  # a cloud API; a model on a local GPU can need more
FRAMING_TOKENS = 64  # chat framing on top of the message text
NOT_BILLED = {"prompt_tokens": 0, "completion_tokens": 0}
FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class BudgetSpent(Exception):
    """The run's AI budget cannot pay for another call."""


class ModelFailure(Exception):
    """The call failed or the answer is not a JSON object. The agent takes no action."""


class _Env(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARSUNE_", env_file=ENV_FILE, extra="ignore", env_ignore_empty=True
    )

    swarm_model_base_url: str = ""
    swarm_model_api_key: SecretStr = SecretStr("")
    swarm_model_name: str = ""
    swarm_model_input_usd_per_million: float | None = None
    swarm_model_output_usd_per_million: float | None = None
    swarm_model_timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=900)
    swarm_model_temperature: float | None = Field(default=None, ge=0, le=2)
    model_base_url: str = ""
    model_api_key: SecretStr = SecretStr("")
    model_name: str = ""
    model_input_usd_per_million: float | None = None
    model_output_usd_per_million: float | None = None


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    api_key: str = field(repr=False)
    name: str
    input_usd_per_million: float | None
    output_usd_per_million: float | None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    temperature: float | None = None  # None: not sent, the model's default

    def problem(self) -> str | None:
        """Why AI agents cannot start with these settings, or None."""
        names = "ARSUNE_SWARM_MODEL_* (or ARSUNE_MODEL_*)"
        if not self.base_url.startswith(("http://", "https://")):
            return f"AI agents need a model base URL that uses HTTP or HTTPS: set {names}."
        if not self.api_key or not self.name:
            return f"AI agents need a model API key and a model name: set {names}."
        # A price of 0 is a free model, such as one on this machine; it never spends the budget.
        prices = (self.input_usd_per_million, self.output_usd_per_million)
        if not all(p is not None and math.isfinite(p) and p >= 0 for p in prices):
            return (
                "AI agents need token prices to keep to their budget: set "
                "INPUT_USD_PER_MILLION and OUTPUT_USD_PER_MILLION in " + names + "."
            )
        return None


def model_settings(env_file: str | None = ENV_FILE) -> ModelSettings:
    """ARSUNE_SWARM_MODEL_* values; each one that is not set falls back to ARSUNE_MODEL_*.

    The timeout and the temperature (ARSUNE_SWARM_MODEL_TIMEOUT_SECONDS and
    ARSUNE_SWARM_MODEL_TEMPERATURE) are the swarm's own and have no fallback.
    """
    env = _Env(_env_file=env_file)

    def pick(name: str):
        own, shared = getattr(env, f"swarm_model_{name}"), getattr(env, f"model_{name}")
        if isinstance(own, SecretStr):
            own, shared = own.get_secret_value(), shared.get_secret_value()
        return own if own not in ("", None) else shared

    return ModelSettings(
        pick("base_url"),
        pick("api_key"),
        pick("name"),
        pick("input_usd_per_million"),
        pick("output_usd_per_million"),
        env.swarm_model_timeout_seconds,
        env.swarm_model_temperature,
    )


def clean(text, limit: int = REASON_CHARS) -> str:
    """One line of plain text from the model, at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class AiBudget:
    """Shared by every AI agent of a run. A call is paid for before it starts."""

    limit_usd: float
    input_usd_per_million: float
    output_usd_per_million: float
    spent: float = 0.0
    held: float = 0.0
    calls: int = 0
    exhausted: bool = False

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_usd_per_million + output_tokens * self.output_usd_per_million
        ) / 1_000_000

    def reserve(self, prompt_bytes: int, max_tokens: int) -> float:
        """Hold the worst case of one call; UTF-8 bytes are an upper bound of text tokens."""
        worst = self.cost(prompt_bytes + FRAMING_TOKENS, max_tokens)
        if self.exhausted or self.spent + self.held + worst > self.limit_usd:
            self.exhausted = True
            raise BudgetSpent
        self.held += worst
        self.calls += 1
        return worst

    def settle(self, held: float, usage: dict | None):
        """Charge the reported usage; without a usage report, keep the whole reservation."""
        self.held -= held
        charge = held
        if isinstance(usage, dict):
            used_in, used_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if isinstance(used_in, int) and isinstance(used_out, int):
                charge = self.cost(used_in, used_out)
        self.spent += charge


def parse_answer(content) -> dict:
    """The model's reply as a JSON object. A Markdown code fence around it is tolerated."""
    if not isinstance(content, str):
        raise ModelFailure("the answer has no text")
    text = content.strip()
    fenced = FENCE.match(text)
    try:
        value = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        raise ModelFailure("the answer is not valid JSON") from None
    if not isinstance(value, dict):
        raise ModelFailure("the answer is not a JSON object")
    return value


class ModelClient:
    def __init__(
        self,
        settings: ModelSettings,
        budget: AiBudget,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 4,
    ):
        self.settings, self.budget = settings, budget
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds, connect=10),
            transport=transport,
            follow_redirects=False,
        )
        self._slots = asyncio.Semaphore(concurrency)

    async def ask(self, system: str, user: str, max_tokens: int) -> dict:
        """One chat call that must answer a JSON object. Raises BudgetSpent or ModelFailure."""
        if self.budget.exhausted:
            raise BudgetSpent
        async with self._slots:
            held = self.budget.reserve(len((system + user).encode()), max_tokens)
            usage = None
            try:
                status, data, problem = await self._post(system, user, max_tokens)
                if status == 200 and isinstance(data, dict):
                    usage = data.get("usage")
                elif status is not None:
                    usage = NOT_BILLED  # an error answer produced no completion
            finally:
                self.budget.settle(held, usage)
        if problem:
            raise ModelFailure(problem)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelFailure("the answer has no message") from None
        try:
            return parse_answer(content)
        except ModelFailure:
            logger.warning("The model's answer is not a JSON object: %r", str(content)[:200])
            raise

    async def _post(self, system: str, user: str, max_tokens: int):
        """(HTTP status or None, JSON body or None, problem or "")."""
        settings = self.settings
        request = {
            "model": settings.name,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        if settings.temperature is not None:
            request["temperature"] = settings.temperature
        try:
            async with self.http.stream(
                "POST",
                settings.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.api_key}"},
                json=request,
            ) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return None, None, "the answer is too large"
        except httpx.HTTPError as exc:
            # The exception names the URL only; the request headers are never logged.
            return None, None, f"the model API did not answer ({type(exc).__name__})"
        if response.status_code != 200:
            return response.status_code, None, f"the model API answered HTTP {response.status_code}"
        try:
            return 200, json.loads(body), ""
        except ValueError:
            return None, None, "the model API sent a body that is not JSON"

    def usage(self) -> dict:
        return {
            "calls": self.budget.calls,
            "usd_spent": round(self.budget.spent, 6),
            "budget_usd": self.budget.limit_usd,
            "model": self.settings.name,
        }

    async def close(self):
        await self.http.aclose()
