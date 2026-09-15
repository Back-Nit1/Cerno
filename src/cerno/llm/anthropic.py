"""Anthropic adapter. The only place that talks to the model; records usage for every call."""

from datetime import UTC, datetime

from cerno.config import Settings
from cerno.storage.sqlite import DB


class LLM:
    def __init__(self, settings: Settings, db: DB | None = None, session_id: str | None = None, client=None):
        self.model = settings.model
        self.db, self.session_id = db, session_id
        if client is None and settings.api_key:
            import anthropic  # slow import; hooks never need it

            client = anthropic.Anthropic(api_key=settings.api_key, max_retries=3)
        self.client = client
        self.input_tokens = self.output_tokens = self.cache_read_tokens = self.calls = 0

    @property
    def available(self) -> bool:
        return self.client is not None

    def create(self, purpose: str, **kwargs):
        if self.client is None:
            raise RuntimeError("model unavailable: ANTHROPIC_API_KEY is not set")
        try:
            response = self.client.messages.create(model=self.model, **kwargs)
        except Exception as exc:
            # Fall back to non-strict tools if this model/account rejects strict tool schemas.
            if "strict" not in str(exc).lower() or not kwargs.get("tools"):
                raise
            kwargs["tools"] = [{k: v for k, v in t.items() if k != "strict"} for t in kwargs["tools"]]
            response = self.client.messages.create(model=self.model, **kwargs)
        usage = getattr(response, "usage", None)
        row = {
            "purpose": purpose,
            "model": self.model,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.calls += 1
        self.input_tokens += row["input_tokens"] + row["cache_read_tokens"] + row["cache_write_tokens"]
        self.output_tokens += row["output_tokens"]
        self.cache_read_tokens += row["cache_read_tokens"]
        if self.db is not None and self.session_id:
            self.db.put("model_usage", self.session_id, f"call-{self.calls:04d}", row, seq=self.calls)
        return response


def block_to_param(block) -> dict:
    """Convert an SDK response block into a request param for replay."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return block.model_dump() if hasattr(block, "model_dump") else dict(block)
