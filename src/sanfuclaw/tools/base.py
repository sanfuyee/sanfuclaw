"""Tool protocol — the interface all tools must satisfy."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sanfuclaw.core.session import Session


@runtime_checkable
class Tool(Protocol):
    """An executable tool that an agent can invoke."""

    name: str
    description: str
    parameters_schema: dict

    async def execute(self, params: dict[str, Any], session: Session) -> Any:
        """Run the tool and return the result."""
        ...
