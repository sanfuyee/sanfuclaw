"""History trimming under a token budget.

Long-running sessions accumulate history without bound; sending all of it
back into every LLM turn inflates input cost and eventually overflows the
provider's context window. ``HistoryBudget.fit`` drops the oldest messages
until the estimated tokens fit ``context_window − fixed_overhead``.

The trim is *pair-aware*: an orphan ``tool_result`` at the head of the
list is rejected by both Anthropic and OpenAI as a 400, so when an
assistant message carrying ``tool_calls`` is dropped, the trailing TOOL
rows go with it.

Token counts here are estimates (3 chars/token heuristic). They
overestimate pure English (~4 chars/tok) and underestimate dense CJK
(~1.5 chars/tok). Overestimating is safe — the trim is more
conservative than necessary. A real tokenizer would tighten the budget
but is intentionally out of scope for this layer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sanfuclaw.core.message import Message
from sanfuclaw.core.types import MessageRole

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Rough char→token estimate. See module docstring for tradeoffs."""
    return max(1, len(text) // 3)


def estimate_message_tokens(msg: Message) -> int:
    total = estimate_tokens(msg.content or "")
    if "tool_calls" in msg.metadata:
        total += estimate_tokens(json.dumps(msg.metadata["tool_calls"]))
    return total + 4  # small per-message structural overhead


@dataclass
class HistoryBudget:
    """Computes how much of ``history`` fits in the input token budget.

    ``context_window`` may be None for providers/configs where no budget
    is enforced; ``fit`` then becomes a no-op.
    """

    context_window: int | None
    max_tokens: int
    input_safety_margin: int
    system_prompt_tokens: int
    tool_schema_tokens: int

    @classmethod
    def from_components(
        cls,
        *,
        context_window: int | None,
        max_tokens: int,
        input_safety_margin: int,
        system_prompt: str,
        tool_schemas: list[dict] | None,
    ) -> "HistoryBudget":
        sys_tok = estimate_tokens(system_prompt)
        tool_tok = 0
        if tool_schemas:
            try:
                tool_tok = estimate_tokens(json.dumps(tool_schemas))
            except Exception:
                logger.debug("Could not estimate tool schema tokens", exc_info=True)
        return cls(
            context_window=context_window,
            max_tokens=max_tokens,
            input_safety_margin=input_safety_margin,
            system_prompt_tokens=sys_tok,
            tool_schema_tokens=tool_tok,
        )

    @property
    def fixed_overhead(self) -> int:
        return (
            self.system_prompt_tokens
            + self.tool_schema_tokens
            + self.max_tokens
            + self.input_safety_margin
        )

    def fit(self, history: list[Message]) -> list[Message]:
        if not self.context_window:
            return history

        budget = self.context_window - self.fixed_overhead
        if budget <= 0:
            logger.warning(
                "Input budget exhausted by fixed overhead "
                "(context_window=%d, max_tokens=%d, margin=%d). Sending empty history.",
                self.context_window, self.max_tokens, self.input_safety_margin,
            )
            return []

        sizes = [estimate_message_tokens(m) for m in history]
        total = sum(sizes)
        if total <= budget:
            return history

        i = 0
        n = len(history)
        while total > budget and i < n:
            total -= sizes[i]
            i += 1
            # A leading TOOL message would be an orphaned tool_result — drop it.
            while i < n and history[i].role == MessageRole.TOOL:
                total -= sizes[i]
                i += 1

        logger.info(
            "Trimmed %d/%d history messages to fit input budget (%d tokens est.)",
            i, n, budget,
        )
        return history[i:]
