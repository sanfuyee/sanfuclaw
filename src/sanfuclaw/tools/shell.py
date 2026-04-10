"""Shell tool — execute shell commands with timeout and safety limits."""

from __future__ import annotations

import asyncio
from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


class ShellTool:
    """Execute shell commands."""

    name = "shell"
    description = "Execute a shell command and return the output. Use for system operations, file management, and running scripts."
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

    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        command = params.get("command", "")
        if not command:
            raise ToolError("No command provided")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
            output = stdout.decode().strip()
            errors = stderr.decode().strip()
            result = output if output else ""
            if errors:
                result += f"\n[stderr] {errors}" if result else f"[stderr] {errors}"
            if proc.returncode != 0:
                result += f"\n[exit code: {proc.returncode}]"
            return result or "(no output)"
        except asyncio.TimeoutError:
            raise ToolError(f"Command timed out after {self._timeout}s")
        except Exception as e:
            raise ToolError(f"Command execution failed: {e}")
