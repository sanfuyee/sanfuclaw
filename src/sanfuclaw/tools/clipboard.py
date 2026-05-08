"""Clipboard tools — read/write the system clipboard on macOS and Linux.

Detection happens per-call so a user who plugs in a Wayland session after
starting the daemon doesn't have to restart. macOS uses pbcopy/pbpaste
(always present on Darwin). Linux prefers Wayland's wl-copy/wl-paste when
WAYLAND_DISPLAY is set, then X11's xclip, then xsel. If no backend is
found we surface the install hint rather than failing with an opaque
"file not found" from the subprocess layer.

Windows is best-effort: not in the required-platforms list, no backend
shipped here. Adding `pyperclip` as an optional dep would close that gap
without changing the rest of the design.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


# Per-write cap. Anything bigger almost certainly should go via a file —
# clipboards on every platform have their own implicit limits and a
# multi-megabyte payload turns the helper subprocess into a long stall.
_MAX_WRITE_BYTES = 1 * 1024 * 1024  # 1 MB

# Per-read cap so a clipboard pre-loaded with a giant binary blob doesn't
# blow up the next LLM turn's input. Same shape as ShellTool's truncation.
_MAX_READ_BYTES = 256 * 1024  # 256 KB
_TRUNCATE_NOTICE = f"\n[truncated: clipboard exceeded {_MAX_READ_BYTES} bytes]"


def _backend() -> tuple[list[str], list[str]] | None:
    """Pick (read_cmd, write_cmd) for the current platform.

    Returns None when no backend is available so the tool can surface a
    user-friendly install hint instead of a raw FileNotFoundError.
    """
    system = platform.system()
    if system == "Darwin":
        # pbcopy/pbpaste are part of macOS itself — no probe needed.
        return (["pbpaste"], ["pbcopy"])

    if system == "Linux":
        # Wayland first: wl-copy/wl-paste read the Wayland selection. If
        # WAYLAND_DISPLAY isn't set we fall back to X11 utilities, which
        # work under XWayland for users on a hybrid setup.
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste") and shutil.which("wl-copy"):
            return (["wl-paste", "--no-newline"], ["wl-copy"])
        if shutil.which("xclip"):
            return (
                ["xclip", "-selection", "clipboard", "-o"],
                ["xclip", "-selection", "clipboard"],
            )
        if shutil.which("xsel"):
            return (["xsel", "--clipboard", "--output"],
                    ["xsel", "--clipboard", "--input"])
        return None

    # Other platforms (Windows, BSDs) — not supported here. The LLM should
    # fall back to other tools (e.g. a shell command the user wires up).
    return None


def _missing_backend_hint() -> str:
    system = platform.system()
    if system == "Linux":
        return (
            "No clipboard backend found. Install one of: "
            "wl-clipboard (Wayland), xclip, or xsel."
        )
    if system == "Darwin":
        # Effectively unreachable — macOS always ships pbcopy/pbpaste.
        return "macOS clipboard helpers (pbcopy/pbpaste) are missing."
    return f"Clipboard not supported on this platform ({system})."


async def _run(cmd: list[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input=stdin)
    return proc.returncode or 0, out, err


class ClipboardReadTool:
    """Read the current contents of the system clipboard."""

    name = "clipboard_read"
    description = (
        "Return the current text contents of the system clipboard. "
        "Use when the user asks you to summarize / process / refer to "
        "'what I just copied'. Output is truncated past 256 KB. "
        "macOS and Linux only — Windows is unsupported here."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        backend = _backend()
        if backend is None:
            raise ToolError(_missing_backend_hint())
        read_cmd, _ = backend

        try:
            code, out, err = await _run(read_cmd)
        except FileNotFoundError:
            # Race: a helper disappeared between detection and exec.
            raise ToolError(_missing_backend_hint())
        except Exception as e:
            raise ToolError(f"Clipboard read failed: {e}")

        if code != 0:
            stderr = err.decode("utf-8", errors="replace").strip()
            # xclip exits 1 with an empty selection message; treat that as empty.
            if "Error: target STRING not available" in stderr or not out:
                return "(empty clipboard)"
            raise ToolError(f"Clipboard read failed (exit {code}): {stderr}")

        if not out:
            return "(empty clipboard)"

        if len(out) > _MAX_READ_BYTES:
            head = out[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
            return head + _TRUNCATE_NOTICE
        return out.decode("utf-8", errors="replace")


class ClipboardWriteTool:
    """Replace the system clipboard contents with the given text."""

    name = "clipboard_write"
    description = (
        "Replace the system clipboard with the provided text. "
        "Use when the user asks you to 'copy this' or 'put X on my clipboard'. "
        "macOS and Linux only — Windows is unsupported here."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to place on the clipboard.",
            },
        },
        "required": ["text"],
    }

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        text = params.get("text", "")
        if not isinstance(text, str):
            raise ToolError("`text` must be a string")
        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            raise ToolError(
                f"Clipboard payload too large ({len(encoded):,} bytes; "
                f"cap is {_MAX_WRITE_BYTES:,}). Write to a file instead."
            )

        backend = _backend()
        if backend is None:
            raise ToolError(_missing_backend_hint())
        _, write_cmd = backend

        try:
            code, _, err = await _run(write_cmd, stdin=encoded)
        except FileNotFoundError:
            raise ToolError(_missing_backend_hint())
        except Exception as e:
            raise ToolError(f"Clipboard write failed: {e}")

        if code != 0:
            stderr = err.decode("utf-8", errors="replace").strip()
            raise ToolError(f"Clipboard write failed (exit {code}): {stderr}")
        return f"Wrote {len(encoded)} byte(s) to clipboard."
