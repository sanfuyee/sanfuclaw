"""speak — read a short text aloud via the host's TTS engine.

macOS ships `say` (always present); Linux distros vary, so we probe a
short ordered list of common engines: espeak-ng, espeak, spd-say. The
voice/rate flags are normalized so a skill written for macOS still works
on Linux (and vice versa) — the caller passes a "voice hint" and we map
it to whatever the chosen backend understands, falling back to defaults
when the hint doesn't translate.

This is fire-and-forget: speech runs in the background by design so the
agent's turn doesn't stall on a 30-second sentence. The tool returns
once the engine has accepted the text, not after audio finishes playing.
"""

from __future__ import annotations

import asyncio
import platform
import shutil
from dataclasses import dataclass
from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


# Hard cap on payload length. The point of this tool is short interjections
# (morning briefs, alerts) — anything longer should be a written reply.
_MAX_CHARS = 4000


@dataclass(frozen=True)
class _Backend:
    """Describes how to invoke one TTS engine.

    `args_for(text, rate)` returns the full argv to exec; the text is
    passed as the final argv element rather than via stdin because
    `say`, `espeak`, and `spd-say` all accept it that way and it keeps
    the dispatch shape uniform.
    """

    name: str
    rate_flag: str | None  # None means "this engine has no rate knob"

    def args_for(self, text: str, rate: int | None) -> list[str]:
        argv = [self.name]
        if rate is not None and self.rate_flag is not None:
            argv += [self.rate_flag, str(rate)]
        argv.append(text)
        return argv


_BACKENDS_LINUX = [
    # espeak-ng is the modern fork; -s is words-per-minute.
    _Backend(name="espeak-ng", rate_flag="-s"),
    _Backend(name="espeak", rate_flag="-s"),
    # spd-say (speech-dispatcher) — -r is rate in [-100, 100], not WPM.
    # We don't translate WPM↔percent; the caller's `rate` is dropped here
    # to avoid quietly producing nonsensical speeds.
    _Backend(name="spd-say", rate_flag=None),
]


def _select_backend() -> _Backend | None:
    system = platform.system()
    if system == "Darwin":
        # macOS `say` is always installed; -r is words-per-minute.
        return _Backend(name="say", rate_flag="-r")
    if system == "Linux":
        for be in _BACKENDS_LINUX:
            if shutil.which(be.name):
                return be
        return None
    # Windows etc. — not in the required-platforms list.
    return None


def _missing_backend_hint() -> str:
    system = platform.system()
    if system == "Linux":
        return (
            "No TTS backend found. Install one of: "
            "espeak-ng (recommended), espeak, or speech-dispatcher (spd-say)."
        )
    if system == "Darwin":
        return "macOS `say` command is missing — that's unusual; check $PATH."
    return f"Speech is not supported on this platform ({system})."


class SpeakTool:
    """Speak a short text aloud through the host TTS engine."""

    name = "speak"
    description = (
        "Read a short text aloud through the host speakers. macOS uses `say`; "
        "Linux uses espeak-ng / espeak / spd-say (whichever is installed). "
        "Use this only when speaking aloud is genuinely better than printing — "
        "e.g. a hands-free morning brief, a quick alert. The call returns as "
        "soon as the engine has accepted the text; audio plays in the "
        "background. Capped at 4000 characters per call."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to speak aloud.",
            },
            "rate": {
                "type": "integer",
                "minimum": 50,
                "maximum": 500,
                "description": (
                    "Words per minute. Roughly 150–200 is natural; default leaves "
                    "the engine's own default. Honored on macOS `say` and "
                    "espeak[-ng]; ignored by spd-say."
                ),
            },
        },
        "required": ["text"],
    }

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        text = params.get("text", "")
        if not isinstance(text, str) or not text.strip():
            raise ToolError("`text` must be a non-empty string")
        if len(text) > _MAX_CHARS:
            raise ToolError(
                f"Text too long ({len(text):,} chars; cap is {_MAX_CHARS:,}). "
                "Reply in writing for longer content."
            )

        rate = params.get("rate")
        if rate is not None:
            try:
                rate = int(rate)
            except (TypeError, ValueError):
                raise ToolError("`rate` must be an integer (words per minute)")

        backend = _select_backend()
        if backend is None:
            raise ToolError(_missing_backend_hint())

        argv = backend.args_for(text.strip(), rate)
        try:
            # Detached: we don't await communicate() — speech runs while
            # the agent moves on. Reaping is best-effort via the loop's
            # subprocess watcher; if it lingers as a zombie that's a host
            # issue, not a correctness one.
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            # Helper vanished between detection and exec — race with a
            # package upgrade, basically. Surface the install hint.
            raise ToolError(_missing_backend_hint())
        except Exception as e:
            raise ToolError(f"Speech failed: {e}")

        return (
            f"Speaking via {backend.name} ({len(text)} chars"
            + (f", {rate} wpm" if rate is not None and backend.rate_flag else "")
            + f"); pid={proc.pid}."
        )
