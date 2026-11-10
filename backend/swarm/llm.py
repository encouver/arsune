"""The swarm's own model client: settings, a per-run USD budget, and JSON object answers.

AI agents call an OpenAI-compatible chat API (NEAR AI Cloud by default) or, with the provider
"anthropic", Claude through the official Anthropic SDK. This client is
separate from Arsune's judge model on purpose: an agent never uses Arsune's internals. The
model sees only page summaries and tip offers. It never sees keys, and its answers are only
data that the agent validates before anything is signed.
"""

import asyncio
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Literal

import anthropic
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
OPENAI_COMPATIBLE, ANTHROPIC = "openai_compatible", "anthropic"
# Claude thinks before it answers, and thinking tokens count in max_tokens and are billed.
CLAUDE_MIN_MAX_TOKENS = 4000
CLAUDE_SDK_RETRIES = 2
# Only these models can retry a policy refusal on the server (beta header for fallbacks="default").
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class BudgetSpent(Exception):
    """The run's AI budget cannot pay for another call."""


class ModelFailure(Exception):
    """The call failed or the answer is not a JSON object. The agent takes no action."""


class _Env(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARSUNE_", env_file=ENV_FILE, extra="ignore", env_ignore_empty=True
    )

    swarm_model_provider: Literal["openai_compatible", "anthropic"] = OPENAI_COMPATIBLE
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
    provider: str = OPENAI_COMPATIBLE

    def problem(self) -> str | None:
        """Why AI agents cannot start with these settings, or None."""
        names = "ARSUNE_SWARM_MODEL_* (or ARSUNE_MODEL_*)"
        if self.provider == ANTHROPIC:
            names = "ARSUNE_SWARM_MODEL_*"  # no fallback: never send another provider's key
            if not (self.api_key or os.environ.get("ANTHROPIC_API_KEY")):
                return "AI agents on Claude need ARSUNE_SWARM_MODEL_API_KEY or ANTHROPIC_API_KEY."
            if not self.name:
                return f"AI agents need a model name: set {names}."
        elif not self.base_url.startswith(("http://", "https://")):
            return f"AI agents need a model base URL that uses HTTP or HTTPS: set {names}."
        elif not self.api_key or not self.name:
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
    claude = env.swarm_model_provider == ANTHROPIC

    def pick(name: str):
        own, shared = getattr(env, f"swarm_model_{name}"), getattr(env, f"model_{name}")
        if isinstance(own, SecretStr):
            own, shared = own.get_secret_value(), shared.get_secret_value()
        if claude:  # the shared settings belong to another provider: not its key, name or prices
            return own
        return own if own not in ("", None) else shared

    return ModelSettings(
        pick("base_url"),
        pick("api_key"),
        pick("name"),
        pick("input_usd_per_million"),
        pick("output_usd_per_million"),
        env.swarm_model_timeout_seconds,
        env.swarm_model_temperature,
        env.swarm_model_provider,
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
    """The model's reply as a JSON object. A Markdown code fence around it is tolerated, and so
    is prose around it: then the first complete JSON object in the text is the answer. The
    agent still validates every field of it."""
    if not isinstance(content, str):
        raise ModelFailure("the answer has no text")
    text = content.strip()
    fenced = FENCE.match(text)
    try:
        value = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        value = first_object(text)
        if value is None:
            raise ModelFailure("the answer is not valid JSON") from None
    if not isinstance(value, dict):
        raise ModelFailure("the answer is not a JSON object")
    return value


def first_object(text: str) -> dict | None:
    """The first complete JSON object in the text, or None."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            return decoder.raw_decode(text, start)[0]
        except ValueError:
            start = text.find("{", start + 1)
    return None


def _status_failure(exc: anthropic.APIStatusError) -> ModelFailure:
    """A rate limit or a 5xx may pass on a later call; any other 4xx is a wrong request."""
    if isinstance(exc, anthropic.RateLimitError):
        return ModelFailure("the model API is rate limiting (HTTP 429)")
    if exc.status_code >= 500:
        return ModelFailure(f"the model API failed (HTTP {exc.status_code}); try again later")
    return ModelFailure(f"the model API refused the request (HTTP {exc.status_code})")


def _claude_text(response) -> str:
    """The answer text of a Claude reply; thinking blocks are skipped."""
    if response.stop_reason == "refusal":
        category = getattr(getattr(response, "stop_details", None), "category", None)
        raise ModelFailure("the model refused to answer" + (f" ({category})" if category else ""))
    if response.stop_reason == "max_tokens":
        raise ModelFailure("the answer was cut off (max_tokens)")
    return "".join(b.text for b in response.content if b.type == "text")


class ModelClient:
    def __init__(
        self,
        settings: ModelSettings,
        budget: AiBudget,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 4,
        claude=None,
    ):
        self.settings, self.budget = settings, budget
        self.claude = claude
        if settings.provider == ANTHROPIC and claude is None:
            # No key given: the SDK reads ANTHROPIC_API_KEY from the environment.
            self.claude = anthropic.AsyncAnthropic(
                api_key=settings.api_key or None,
                max_retries=CLAUDE_SDK_RETRIES,
                timeout=settings.timeout_seconds,
            )
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
        if self.settings.provider == ANTHROPIC:
            return await self._ask_claude(system, user, max_tokens)
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

    async def _ask_claude(self, system: str, user: str, max_tokens: int) -> dict:
        max_tokens = max(max_tokens, CLAUDE_MIN_MAX_TOKENS)
        async with self._slots:
            held = self.budget.reserve(len((system + user).encode()), max_tokens)
            usage = None  # no report: the whole reservation stays charged
            try:
                response = await self._create(system, user, max_tokens)
                usage = {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                }
            except anthropic.APIStatusError as exc:
                usage = NOT_BILLED  # an error answer produced no completion
                raise _status_failure(exc) from None
            except anthropic.APIConnectionError as exc:  # includes APITimeoutError
                # The exception text can name the URL only; the key is never in it.
                raise ModelFailure(f"the model API did not answer ({type(exc).__name__})") from None
            finally:
                self.budget.settle(held, usage)
        text = _claude_text(response)
        try:
            return parse_answer(text)
        except ModelFailure:
            logger.warning("The model's answer is not a JSON object: %r", text[:200])
            raise

    async def _create(self, system: str, user: str, max_tokens: int):
        request = {
            "model": self.settings.name,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": "low"},
        }
        if self.settings.name in FALLBACK_MODELS:
            return await self.claude.beta.messages.create(
                **request, betas=[FALLBACK_BETA], fallbacks="default"
            )
        return await self.claude.messages.create(**request)

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
        if self.claude is not None:
            await self.claude.close()
