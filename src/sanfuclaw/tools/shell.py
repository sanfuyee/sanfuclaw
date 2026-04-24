"""Shell tool — execute shell commands with timeout, env scrubbing, and output cap."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


# Subset of the parent env the subprocess really needs. Anything else is
# stripped so LLM-issued commands can't `echo $SANFUCLAW_LLM__API_KEY` or
# equivalent and leak secrets out through the channel/trace path.
_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD",
    "LANG", "TZ", "TERM", "TMPDIR",
})
_ENV_ALLOW_PREFIXES = ("LC_",)

# Hard cap on returned output. Prevents `cat huge.bin` or `find /` from
# OOM-ing the agent process and from ballooning the next LLM turn's input.
_MAX_OUTPUT_BYTES = 100 * 1024
_TRUNCATE_NOTICE = f"\n[truncated: output exceeded {_MAX_OUTPUT_BYTES} bytes]"


def _filtered_env() -> dict[str, str]:
    filtered: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _ENV_ALLOWLIST or any(key.startswith(p) for p in _ENV_ALLOW_PREFIXES):
            filtered[key] = value
    return filtered


def _truncate(text: str, limit: int = _MAX_OUTPUT_BYTES) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    head = encoded[:limit].decode("utf-8", errors="replace")
    return head + _TRUNCATE_NOTICE


class ShellTool:
    """Execute shell commands."""

    name = "shell"
    description = (
        "Execute a shell command and return its output. Use for system operations, "
        "file management, and running scripts. "
        "To minimize round-trips, prefer combining steps in a single call: "
        "chain with `&&` or `;`, read multiple files with `cat f1 f2 f3`, "
        "or use `find ... -exec cat {} +`. Issue one command per call; when "
        "several INDEPENDENT commands are needed, emit multiple tool calls "
        "in the same turn rather than across successive turns."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
        },
        "required": ["command"],
    }

    def __init__(self, timeout: int = 30, max_output_bytes: int = _MAX_OUTPUT_BYTES):
        self._timeout = timeout
        self._max_output_bytes = max_output_bytes

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        command = params.get("command", "")
        if not command:
            raise ToolError("No command provided")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_filtered_env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
            output = stdout.decode(errors="replace").strip()
            errors = stderr.decode(errors="replace").strip()
            result = output if output else ""
            if errors:
                result += f"\n[stderr] {errors}" if result else f"[stderr] {errors}"
            if proc.returncode != 0:
                result += f"\n[exit code: {proc.returncode}]"
            result = _truncate(result, self._max_output_bytes)
            return result or "(no output)"
        except asyncio.TimeoutError:
            raise ToolError(f"Command timed out after {self._timeout}s")
        except Exception as e:
            raise ToolError(f"Command execution failed: {e}")
