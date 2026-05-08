"""read_file — return a file's contents with line numbers, offset/limit-aware.

Reading via `shell` + `cat` works but every quote-tricky path or binary file
turns into a debugging round-trip. This tool gives the agent a structured
read API: numbered output that matches the format used in editing tasks,
explicit binary detection, and a hard size cap that fails loudly instead
of OOM-ing the process.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


# Hard cap on file size we'll even attempt to read. Anything bigger almost
# certainly belongs in `shell head -c …` or a streaming approach the agent
# can structure itself.
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

# Default line window when no limit is requested. Chosen to match the
# typical context budget of one tool result without dominating it.
_DEFAULT_LIMIT = 2000

# Binary detection: any null byte in the first sniff window means we treat
# the file as binary and refuse rather than spamming the LLM with mojibake.
_BINARY_SNIFF_BYTES = 8192


def _is_probably_binary(blob: bytes) -> bool:
    return b"\x00" in blob[:_BINARY_SNIFF_BYTES]


def _read_sync(path: Path, offset: int, limit: int) -> tuple[list[str], int]:
    """Read the requested line slice. Returns (lines, total_lines_in_file).

    Done synchronously and called via `asyncio.to_thread` so a slow disk
    or NFS mount doesn't block the event loop.
    """
    size = path.stat().st_size
    if size > _MAX_BYTES:
        raise ToolError(
            f"File too large: {size:,} bytes (cap is {_MAX_BYTES:,}). "
            "Use `shell` with `head`/`tail` for huge files."
        )

    with path.open("rb") as f:
        sniff = f.read(_BINARY_SNIFF_BYTES)
        if _is_probably_binary(sniff):
            raise ToolError(f"File appears to be binary: {path}")
        rest = f.read()

    text = (sniff + rest).decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    total = len(all_lines)

    # offset is 1-indexed inclusive (matches editor line numbers); offset=0
    # is treated as 1 so the LLM doesn't need to remember the convention.
    start = max(offset, 1) - 1
    if start >= total:
        return [], total
    end = start + max(limit, 1)
    return all_lines[start:end], total


def _format(lines: list[str], offset: int, total: int) -> str:
    if not lines:
        return f"(empty slice; file has {total} line(s))"
    width = max(4, len(str(offset + len(lines) - 1)))
    rendered = "\n".join(
        f"{i:>{width}}\t{line}" for i, line in enumerate(lines, start=offset)
    )
    end = offset + len(lines) - 1
    if end < total:
        rendered += f"\n[truncated: showing lines {offset}-{end} of {total}]"
    return rendered


class ReadFileTool:
    """Read a UTF-8 text file and return its contents with line numbers."""

    name = "read_file"
    description = (
        "Read a UTF-8 text file and return its contents with line numbers. "
        "Use this instead of `shell cat` whenever you need structured output: "
        "tracebacks, code review, applying edits. "
        "Relative paths resolve against the process working directory. "
        "Binary files and files larger than 5 MB are refused — drop to `shell` "
        "for those. Default returns the first 2000 lines; pass `offset` (1-indexed) "
        "and `limit` to page through longer files."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to read (absolute or relative to cwd).",
            },
            "offset": {
                "type": "integer",
                "minimum": 1,
                "description": "1-indexed line to start from. Default: 1.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": f"Max lines to return. Default: {_DEFAULT_LIMIT}.",
            },
        },
        "required": ["path"],
    }

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        raw_path = str(params.get("path", "")).strip()
        if not raw_path:
            raise ToolError("Missing required field: path")

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        if not path.exists():
            raise ToolError(f"File not found: {path}")
        if path.is_dir():
            raise ToolError(f"Path is a directory: {path}")

        offset = int(params.get("offset") or 1)
        limit = int(params.get("limit") or _DEFAULT_LIMIT)
        try:
            lines, total = await asyncio.to_thread(_read_sync, path, offset, limit)
        except ToolError:
            raise
        except OSError as e:
            raise ToolError(f"Could not read {path}: {e}")

        return _format(lines, max(offset, 1), total)
