"""OpenAI Responses API adapter; records usage for every model call."""

import json
import time
from collections import deque
from datetime import UTC, datetime

from cerno.config import Settings
from cerno.storage.sqlite import DB

# Provider failures that are the *operator's* problem, not a gap in the evidence. Matched by class name so
# this module keeps working without importing the (slow) openai package.
FATAL_ERRORS = {"AuthenticationError", "PermissionDeniedError", "APIConnectionError", "APITimeoutError"}


class ProviderError(RuntimeError):
    """Authentication or connection failure talking to the model provider. Never an UNCERTAIN verdict."""


def _is_fatal(exc: Exception) -> bool:
    return any(cls.__name__ in FATAL_ERRORS for cls in type(exc).__mro__)


class LLM:
    def __init__(self, settings: Settings, db: DB | None = None, session_id: str | None = None, client=None):
        self.model = settings.model
        self.db, self.session_id = db, session_id
        if client is None and settings.api_key:
            from openai import OpenAI  # slow import; hooks never need it

            client = OpenAI(api_key=settings.api_key, max_retries=3, timeout=settings.request_timeout)
        self.client = client
        self.max_calls, self.max_tokens = settings.max_model_calls, settings.max_total_tokens
        self.per_minute, self._recent = settings.max_calls_per_minute, deque()
        self.input_tokens = self.output_tokens = self.cache_read_tokens = self.calls = 0

    @property
    def available(self) -> bool:
        return self.client is not None

    def _throttle(self) -> None:
        """Refuse once the run's budget is spent; otherwise wait so calls stay under the per-minute limit.
        Bounds what a huge diff, a hostile rules file, or a prompt-injected loop can cost the key's owner."""
        if self.calls >= self.max_calls or self.input_tokens + self.output_tokens >= self.max_tokens:
            raise ProviderError(
                f"model budget exhausted ({self.calls} calls, {self.input_tokens + self.output_tokens} tokens); "
                "raise CERNO_MAX_MODEL_CALLS / CERNO_MAX_TOTAL_TOKENS if this run is legitimate")
        now = time.monotonic()
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()
        if len(self._recent) >= self.per_minute:
            time.sleep(max(0.0, 60 - (now - self._recent[0])))
            self._recent.popleft()
        self._recent.append(time.monotonic())

    def _create_raw(self, **kwargs):
        self._throttle()
        try:
            return self.client.responses.create(model=self.model, **kwargs)
        except Exception as exc:
            if _is_fatal(exc):
                raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
            raise

    def create(self, purpose: str, **kwargs):
        if self.client is None:
            raise RuntimeError("model unavailable: OPENAI_API_KEY is not set")
        kwargs.setdefault("store", False)
        kwargs.setdefault("include", ["reasoning.encrypted_content"])
        try:
            response = self._create_raw(**kwargs)
        except Exception as exc:
            # Some models/accounts may reject strict schemas; retain a best-effort fallback.
            if "strict" not in str(exc).lower() or not kwargs.get("tools"):
                raise
            kwargs["tools"] = [{**tool, "strict": False} for tool in kwargs["tools"]]
            response = self._create_raw(**kwargs)
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        row = {
            "purpose": purpose,
            "model": self.model,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(details, "cached_tokens", 0) or 0,
            "cache_write_tokens": 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.calls += 1
        self.input_tokens += row["input_tokens"]
        self.output_tokens += row["output_tokens"]
        self.cache_read_tokens += row["cache_read_tokens"]
        if self.db is not None and self.session_id:
            self.db.put("model_usage", self.session_id, f"call-{self.calls:04d}", row, seq=self.calls)
        return response


def function_calls(response) -> list:
    """Return function-call items from a Responses API response."""
    return [item for item in getattr(response, "output", ()) if getattr(item, "type", None) == "function_call"]


def call_arguments(call) -> dict:
    """Decode the JSON arguments attached to a function-call item."""
    value = getattr(call, "arguments", "{}")
    return value if isinstance(value, dict) else json.loads(value)


def output_to_param(item) -> dict:
    """Convert an SDK response output item into input for the next response."""
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return {key: value for key, value in vars(item).items() if value is not None}
