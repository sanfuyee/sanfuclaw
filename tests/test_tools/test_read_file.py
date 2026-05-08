"""read_file tool tests — happy path, binary refusal, offset/limit window."""

from __future__ import annotations

import pytest

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.tools.read_file import ReadFileTool


@pytest.fixture
def session() -> Session:
    return Session(id="s-1", channel_id="cli", sender_id="u")


@pytest.fixture
def tool() -> ReadFileTool:
    return ReadFileTool()


async def test_reads_text_file_with_line_numbers(tool, session, tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("import os\nprint('hi')\n")

    out = await tool.execute({"path": str(f)}, session)

    assert "import os" in out
    assert "print('hi')" in out
    # cat -n style — line numbers + tab + content
    assert "\timport os" in out
    assert "\tprint('hi')" in out


async def test_offset_and_limit_window(tool, session, tmp_path):
    f = tmp_path / "many.txt"
    f.write_text("\n".join(f"line-{i}" for i in range(1, 11)) + "\n")

    out = await tool.execute({"path": str(f), "offset": 4, "limit": 2}, session)

    assert "line-4" in out
    assert "line-5" in out
    assert "line-3" not in out
    assert "line-6" not in out
    # Truncation hint when we don't show the tail
    assert "truncated" in out


async def test_offset_past_end_returns_empty_slice(tool, session, tmp_path):
    f = tmp_path / "short.txt"
    f.write_text("only-one\n")

    out = await tool.execute({"path": str(f), "offset": 50}, session)

    assert "empty slice" in out
    assert "1 line" in out


async def test_missing_file_raises(tool, session, tmp_path):
    with pytest.raises(ToolError, match="File not found"):
        await tool.execute({"path": str(tmp_path / "nope.txt")}, session)


async def test_directory_path_raises(tool, session, tmp_path):
    with pytest.raises(ToolError, match="directory"):
        await tool.execute({"path": str(tmp_path)}, session)


async def test_binary_file_refused(tool, session, tmp_path):
    f = tmp_path / "binary.bin"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    with pytest.raises(ToolError, match="binary"):
        await tool.execute({"path": str(f)}, session)


async def test_relative_path_resolves_against_cwd(tool, session, tmp_path, monkeypatch):
    f = tmp_path / "rel.txt"
    f.write_text("relative ok\n")
    monkeypatch.chdir(tmp_path)

    out = await tool.execute({"path": "rel.txt"}, session)

    assert "relative ok" in out


async def test_oversized_file_refused(tool, session, tmp_path, monkeypatch):
    # Lower the cap rather than writing a real 5 MB file in tests.
    from sanfuclaw.tools import read_file as rf

    monkeypatch.setattr(rf, "_MAX_BYTES", 64)
    f = tmp_path / "big.txt"
    f.write_text("x" * 200)

    with pytest.raises(ToolError, match="too large"):
        await tool.execute({"path": str(f)}, session)
