"""CLI channel — interactive terminal chat interface."""

from __future__ import annotations

import asyncio
import sys
from typing import AsyncIterator

from rich.console import Console
from rich.markdown import Markdown

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.types import MessageRole

console = Console()


class CLIChannel:
    """A channel that reads from stdin and writes to stdout."""

    name: str = "cli"

    def __init__(self, session_id: str = "cli-session"):
        self._session_id = session_id
        self._running = False

    async def start(self) -> None:
        self._running = True
        console.print("[bold green]Sanfuclaw[/bold green] is ready. Type your message (Ctrl+C to quit).\n")

    async def stop(self) -> None:
        self._running = False
        console.print("\n[dim]Goodbye![/dim]")

    async def send(self, session_id: str, content: str, **kwargs) -> None:
        """Print assistant response to terminal."""
        done = kwargs.get("done", False)
        if done:
            # End of response
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif kwargs.get("streaming", False):
            sys.stdout.write(content)
            sys.stdout.flush()

    async def send_typing(self, session_id: str) -> None:
        console.print("[dim]Thinking...[/dim]", end="")

    async def receive(self) -> AsyncIterator[Envelope]:
        """Read lines from stdin as user messages."""
        while self._running:
            try:
                user_input = await asyncio.to_thread(self._read_input)
                if user_input is None:
                    break
                user_input = user_input.strip()
                if not user_input:
                    continue
                if user_input.lower() in ("/quit", "/exit"):
                    break

                message = Message(
                    role=MessageRole.USER,
                    content=user_input,
                    channel_id=self.name,
                    session_id=self._session_id,
                    sender_id="cli-user",
                )
                yield Envelope(message=message, source_channel=self.name)
            except (EOFError, KeyboardInterrupt):
                break

    def _read_input(self) -> str | None:
        try:
            return console.input("[bold blue]You:[/bold blue] ")
        except (EOFError, KeyboardInterrupt):
            return None
