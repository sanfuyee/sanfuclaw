"""Memory registry and loader tests."""

from __future__ import annotations

import pytest

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.memory.registry import MemoryRegistry
from sanfuclaw.tools.memory_loader import LoadMemoryTool


def test_registry_loads_entries_and_index(tmp_path):
    (tmp_path / "MEMORY.md").write_text(
        "- [User role](user_role.md) — Go dev\n- [Workflow](workflow.md) — commits on dev\n"
    )
    (tmp_path / "user_role.md").write_text(
        "---\nname: user_role\ndescription: user profile\n---\nSenior Go engineer.\n"
    )
    (tmp_path / "workflow.md").write_text("Commit directly on dev.\n")

    r = MemoryRegistry(tmp_path)

    assert len(r) == 2
    assert r.get("user_role").description == "user profile"
    assert r.get("workflow").name == "workflow"
    assert "[User role](user_role.md)" in r.system_prompt_block()


def test_registry_missing_dir_is_silent(tmp_path):
    r = MemoryRegistry(tmp_path / "does-not-exist")
    assert len(r) == 0
    assert r.system_prompt_block() == ""


def test_registry_autogenerates_block_when_no_index(tmp_path):
    (tmp_path / "fact.md").write_text("---\nname: fact\ndescription: a fact\n---\nbody\n")

    r = MemoryRegistry(tmp_path)
    block = r.system_prompt_block()

    assert "**fact**: a fact" in block


def test_registry_truncates_runaway_index(tmp_path):
    huge = "\n".join(f"line {i}" for i in range(500))
    (tmp_path / "MEMORY.md").write_text(huge)

    r = MemoryRegistry(tmp_path)
    block = r.system_prompt_block()

    assert "truncated: MEMORY.md exceeded" in block


async def test_load_memory_tool_returns_body(tmp_path):
    (tmp_path / "note.md").write_text("---\nname: note\n---\nthe body\n")
    r = MemoryRegistry(tmp_path)
    tool = LoadMemoryTool(r)

    result = await tool.execute({"name": "note"}, Session())

    assert "# Memory: note" in result
    assert "the body" in result


async def test_load_memory_tool_unknown_name_raises(tmp_path):
    r = MemoryRegistry(tmp_path)
    tool = LoadMemoryTool(r)

    with pytest.raises(ToolError, match="Unknown memory entry"):
        await tool.execute({"name": "missing"}, Session())


async def test_load_memory_tool_rejects_empty_name(tmp_path):
    r = MemoryRegistry(tmp_path)
    tool = LoadMemoryTool(r)

    with pytest.raises(ToolError, match="No memory name"):
        await tool.execute({"name": ""}, Session())
