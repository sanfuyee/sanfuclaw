"""code_search — regex grep across files. Prefers ripgrep, falls back to Python.

`shell grep` works but has surprising portability gaps (BSD vs GNU grep on
macOS), and the LLM has to remember `--include` / `-r` / `-n` every call.
This tool gives a stable wrapper: ripgrep when available, a pure-Python
walker otherwise. Same output shape either way: `path:line:matched_text`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import shutil
from pathlib import Path
from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


# Directories the fallback walker skips outright. Searching node_modules or
# .venv is almost never what the user wants and tanks performance. ripgrep
# applies its own ignore rules so this list is fallback-only.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".jj",
    "node_modules", "__pycache__", ".pytest_cache",
    ".venv", "venv", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".next", ".turbo",
    "target",  # rust
})

# Per-file size cap for the Python fallback. Bigger files are skipped — a
# huge minified bundle or generated SQL dump is rarely worth grepping.
_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB

# Output cap on the joined result string. Keeps a runaway match count
# from blowing up the next LLM turn.
_MAX_OUTPUT_BYTES = 64 * 1024


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return text
    head = encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return head + f"\n[truncated: output exceeded {_MAX_OUTPUT_BYTES} bytes]"


async def _run_ripgrep(
    pattern: str,
    path: Path,
    glob: str | None,
    case_insensitive: bool,
    max_results: int,
) -> str:
    args: list[str] = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color=never",
        f"--max-count={max_results}",
    ]
    if case_insensitive:
        args.append("-i")
    if glob:
        args += ["--glob", glob]
    args += ["-e", pattern, str(path)]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace").rstrip()

    # rg exit codes: 0 = matched, 1 = no match, 2+ = error.
    if proc.returncode not in (0, 1):
        err = stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"ripgrep failed: {err or 'exit ' + str(proc.returncode)}")

    return out


def _python_search_sync(
    pattern: str,
    root: Path,
    glob: str | None,
    case_insensitive: bool,
    max_results: int,
) -> list[str]:
    """Pure-Python fallback. Walks `root`, skips noisy dirs, regex per line."""
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        raise ToolError(f"Invalid regex {pattern!r}: {e}")

    matches: list[str] = []
    for path in _iter_files(root, glob):
        if len(matches) >= max_results:
            break
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        try:
            with path.open("rb") as f:
                # Sniff for nulls — skip binaries before paying decode cost.
                head = f.read(4096)
                if b"\x00" in head:
                    continue
                rest = f.read()
        except OSError:
            continue

        text = (head + rest).decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{path}:{lineno}:{line.rstrip()}")
                if len(matches) >= max_results:
                    break
    return matches


def _iter_files(root: Path, glob: str | None):
    """Walk `root`, skipping VCS/build dirs and applying the optional glob."""
    if root.is_file():
        if glob is None or fnmatch.fnmatch(root.name, glob):
            yield root
        return

    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                    # Hidden dirs blocked too — `.git`, `.idea`, etc. The
                    # explicit skip set covers the common cases by name in
                    # case the user un-hides one of them.
                    continue
                stack.append(entry)
                continue
            if not entry.is_file():
                continue
            if glob is not None and not fnmatch.fnmatch(entry.name, glob):
                continue
            yield entry


class CodeSearchTool:
    """Regex grep across files; ripgrep when present, Python fallback otherwise."""

    name = "code_search"
    description = (
        "Search files for a regex pattern. Returns `path:line:matched_text` "
        "rows, one per match. Prefer this over `shell grep` — it skips "
        "node_modules / .venv / .git automatically and gives stable output. "
        "Pass `glob` (e.g. '*.py') to narrow by filename, `case_insensitive` "
        "for /i, and `max_results` to cap the response size."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search. Default: current working directory.",
            },
            "glob": {
                "type": "string",
                "description": "Glob filter for filenames, e.g. '*.py' or 'test_*.py'.",
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "If true, ignore case when matching.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "description": "Max number of matches to return (default: 100).",
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        pattern = str(params.get("pattern", "")).strip()
        if not pattern:
            raise ToolError("Missing required field: pattern")

        raw_path = str(params.get("path") or ".").strip() or "."
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise ToolError(f"Path not found: {path}")

        glob = params.get("glob")
        glob = str(glob).strip() if glob else None
        case_insensitive = bool(params.get("case_insensitive", False))
        max_results = int(params.get("max_results") or 100)
        max_results = max(1, min(max_results, 1000))

        rg_path = shutil.which("rg")
        if rg_path:
            output = await _run_ripgrep(
                pattern, path, glob, case_insensitive, max_results,
            )
        else:
            matches = await asyncio.to_thread(
                _python_search_sync,
                pattern, path, glob, case_insensitive, max_results,
            )
            output = "\n".join(matches)

        if not output.strip():
            return "(no matches)"
        return _truncate(output)
