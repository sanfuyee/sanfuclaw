"""Task / plan tool — structured plan-and-execute scaffold.

The model maintains a numbered task list as a structured artifact rather
than as free-form text scattered across history. Each turn the agent
sees the current plan in the system prompt, so it doesn't have to
re-derive its strategy from scrolling back through tool results.

Pattern is borrowed from Claude Code's TodoWrite: a single tool that
takes the *complete* current plan each call and overwrites previous
state. Avoids the index-drift bugs of separate create/update tools and
makes replanning trivial — the model just writes the new full plan.
"""

from __future__ import annotations

from typing import Any, Iterable

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


_VALID_STATUSES = {"pending", "in_progress", "completed", "skipped"}
_STATUS_GLYPH = {
    "pending":     "[ ]",
    "in_progress": "[~]",
    "completed":   "[x]",
    "skipped":     "[-]",
}


def format_plan(tasks: Iterable[dict]) -> str:
    """Render a plan as a compact, human/LLM-readable text block."""
    tasks = list(tasks)
    if not tasks:
        return "(no plan)"
    lines = []
    for i, t in enumerate(tasks, 1):
        glyph = _STATUS_GLYPH.get(t.get("status", "pending"), "[?]")
        text = (t.get("text") or "").strip()
        line = f"{glyph} {i}. {text}"
        note = (t.get("note") or "").strip()
        if note:
            line += f"  — {note}"
        lines.append(line)
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t.get("status", "pending")] = counts.get(t.get("status", "pending"), 0) + 1
    parts = [f"{c} {s}" for s, c in counts.items() if c]
    lines.append(f"Progress: {' / '.join(parts)}")
    return "\n".join(lines)


class TaskWriteTool:
    """Maintain the current plan/todo list for the active session."""

    name = "task_write"
    description = (
        "Maintain a structured plan/todo list for the current task. "
        "Pass the COMPLETE current state of the task list each call — "
        "this tool replaces, not appends.\n"
        "\n"
        "Use this when:\n"
        "- The request needs 3+ distinct subtasks, or\n"
        "- Different tools / sources are required, or\n"
        "- A previous attempt failed and you need to replan, or\n"
        "- The user asked you to track progress.\n"
        "\n"
        "Each task is {text, status, note?}. Status is one of: "
        "pending, in_progress, completed, skipped. Mark at most ONE task "
        "as in_progress at any moment. As work progresses, call this "
        "again with the updated state: flip completed tasks to "
        "'completed' (with a brief 'note' on what you learned), advance "
        "the next one to 'in_progress'.\n"
        "\n"
        "Skip this tool entirely for one-shot questions like 'what's "
        "the weather' — planning overhead isn't worth it."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "Complete current task list. Replaces prior state.",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "What to do."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "skipped"],
                        },
                        "note": {
                            "type": "string",
                            "description": "Optional one-line observation / why-skipped.",
                        },
                    },
                    "required": ["text", "status"],
                },
            },
        },
        "required": ["tasks"],
    }

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        raw = params.get("tasks")
        if not isinstance(raw, list):
            raise ToolError("`tasks` must be a list of task objects")

        cleaned: list[dict] = []
        for i, t in enumerate(raw, 1):
            if not isinstance(t, dict):
                raise ToolError(f"task #{i} is not an object")
            text = str(t.get("text") or "").strip()
            if not text:
                raise ToolError(f"task #{i} has empty text")
            status = str(t.get("status") or "pending").strip()
            if status not in _VALID_STATUSES:
                raise ToolError(
                    f"task #{i} has invalid status {status!r}; must be one "
                    f"of {sorted(_VALID_STATUSES)}"
                )
            note = str(t.get("note") or "").strip()
            cleaned.append({"text": text, "status": status, "note": note})

        in_progress = sum(1 for t in cleaned if t["status"] == "in_progress")
        if in_progress > 1:
            raise ToolError(
                f"plan has {in_progress} tasks marked in_progress; only one "
                "task may be active at a time"
            )

        session.metadata["plan"] = cleaned
        return "Plan updated:\n" + format_plan(cleaned)
