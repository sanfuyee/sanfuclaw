"""Memory write tools — save / update / forget."""

from __future__ import annotations

import pytest

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.memory.registry import MemoryRegistry, MemoryWriteError
from sanfuclaw.tools.memory_writer import (
    ForgetMemoryTool,
    SaveMemoryTool,
    UpdateMemoryTool,
)


# ---------- registry-level invariants --------------------------------------


def test_save_writes_file_and_appends_index(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(
        name="user_role", description="Senior Python dev",
        body="Works on infra teams.", type_="user",
    )
    entry_file = tmp_path / "user_role.md"
    assert entry_file.exists()
    content = entry_file.read_text()
    assert "name: user_role" in content
    assert "description: Senior Python dev" in content
    assert "type: user" in content
    assert "Works on infra teams." in content

    index = (tmp_path / "MEMORY.md").read_text()
    assert "(user_role.md)" in index
    assert "Senior Python dev" in index


def test_save_refuses_overwrite(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="d", body="body")
    with pytest.raises(MemoryWriteError, match="already exists"):
        r.save_entry(name="x", description="d2", body="body2")


def test_save_rejects_invalid_names(tmp_path):
    r = MemoryRegistry(tmp_path)
    for bad in ["", "../escape", "with/slash", ".hidden", "with space"]:
        with pytest.raises(MemoryWriteError):
            r.save_entry(name=bad, description="d", body="b")


def test_update_preserves_description_when_omitted(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="orig", body="b1")
    r.update_entry(name="x", body="b2")
    text = (tmp_path / "x.md").read_text()
    assert "description: orig" in text
    assert "b2" in text
    assert "b1" not in text


def test_update_changes_description_when_provided(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="orig", body="b1")
    r.update_entry(name="x", body="b2", description="new")
    assert "description: new" in (tmp_path / "x.md").read_text()


def test_update_unknown_raises(tmp_path):
    r = MemoryRegistry(tmp_path)
    with pytest.raises(MemoryWriteError, match="No such memory entry"):
        r.update_entry(name="ghost", body="b")


def test_forget_removes_file_and_index_line(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="alpha", description="a", body="A")
    r.save_entry(name="beta", description="b", body="B")
    assert r.forget_entry("alpha") is True

    assert not (tmp_path / "alpha.md").exists()
    assert (tmp_path / "beta.md").exists()
    index = (tmp_path / "MEMORY.md").read_text()
    assert "(alpha.md)" not in index
    assert "(beta.md)" in index


def test_forget_missing_returns_false(tmp_path):
    r = MemoryRegistry(tmp_path)
    assert r.forget_entry("ghost") is False


def test_save_then_load_via_registry_roundtrip(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="foo", description="d", body="payload")
    # Registry should have hot-reloaded so get() finds the new entry.
    entry = r.get("foo")
    assert entry is not None
    assert "payload" in entry.body


def test_save_replaces_existing_index_line_for_same_target(tmp_path):
    r = MemoryRegistry(tmp_path)
    # Pre-populate MEMORY.md with a stale line for x.md
    (tmp_path / "MEMORY.md").write_text("- [Old](x.md) — old hook\n")
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="new hook", body="b")
    index = (tmp_path / "MEMORY.md").read_text()
    # Only one line referencing x.md; old hook gone.
    assert index.count("(x.md)") == 1
    assert "old hook" not in index
    assert "new hook" in index


def test_atomic_write_no_tmp_left(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="d", body="b")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# ---------- tool wrappers ---------------------------------------------------


async def test_save_tool_creates_entry(tmp_path):
    r = MemoryRegistry(tmp_path)
    tool = SaveMemoryTool(r)
    result = await tool.execute(
        {"name": "note", "description": "d", "body": "b", "type": "user"},
        Session(),
    )
    assert "Saved memory" in result
    assert (tmp_path / "note.md").exists()


async def test_save_tool_rejects_invalid_type(tmp_path):
    r = MemoryRegistry(tmp_path)
    tool = SaveMemoryTool(r)
    with pytest.raises(ToolError, match="Invalid memory type"):
        await tool.execute(
            {"name": "n", "body": "b", "type": "garbage"}, Session()
        )


async def test_save_tool_rejects_empty_body(tmp_path):
    r = MemoryRegistry(tmp_path)
    tool = SaveMemoryTool(r)
    with pytest.raises(ToolError, match="empty"):
        await tool.execute({"name": "n", "body": "   "}, Session())


async def test_save_tool_surfaces_overwrite_as_toolerror(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="d", body="b")
    tool = SaveMemoryTool(r)
    with pytest.raises(ToolError, match="already exists"):
        await tool.execute({"name": "x", "body": "b2"}, Session())


async def test_update_tool_modifies_body(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="d", body="v1")
    tool = UpdateMemoryTool(r)
    await tool.execute({"name": "x", "body": "v2"}, Session())
    assert "v2" in (tmp_path / "x.md").read_text()


async def test_forget_tool_removes(tmp_path):
    r = MemoryRegistry(tmp_path)
    r.save_entry(name="x", description="d", body="b")
    tool = ForgetMemoryTool(r)
    result = await tool.execute({"name": "x"}, Session())
    assert "Forgot memory" in result
    assert not (tmp_path / "x.md").exists()


async def test_forget_tool_missing_raises(tmp_path):
    r = MemoryRegistry(tmp_path)
    tool = ForgetMemoryTool(r)
    with pytest.raises(ToolError, match="No such memory entry"):
        await tool.execute({"name": "ghost"}, Session())
