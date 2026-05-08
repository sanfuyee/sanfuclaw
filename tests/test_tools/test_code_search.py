"""code_search tool tests — exercise the Python fallback path explicitly.

We monkeypatch shutil.which to None so tests don't depend on whether
ripgrep is installed in the runner environment. A separate parametric
test covers the rg path when rg is available.
"""

from __future__ import annotations

import shutil

import pytest

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.tools.code_search import CodeSearchTool


@pytest.fixture
def session() -> Session:
    return Session(id="s-1", channel_id="cli", sender_id="u")


@pytest.fixture
def tool() -> CodeSearchTool:
    return CodeSearchTool()


@pytest.fixture
def force_python(monkeypatch):
    """Pin the search to the Python fallback regardless of host rg presence."""
    monkeypatch.setattr(
        "sanfuclaw.tools.code_search.shutil.which", lambda name: None,
    )


def _seed_repo(root):
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("def hello():\n    print('hi')\n")
    (root / "src" / "b.py").write_text("def goodbye():\n    print('bye')\n")
    (root / "README.md").write_text("# project\nhello world\n")
    # Noise dirs that should be skipped by the walker.
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.py").write_text("hello-from-noise\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("hello-from-git\n")


async def test_finds_matches_in_python_files(tool, session, tmp_path, force_python):
    _seed_repo(tmp_path)
    out = await tool.execute(
        {"pattern": "hello", "path": str(tmp_path)}, session,
    )

    # Matches both a.py (def hello) and README.md (hello world).
    assert "a.py" in out
    assert "README.md" in out
    # Noise dirs skipped.
    assert "node_modules" not in out
    assert ".git" not in out


async def test_glob_filter_narrows_results(tool, session, tmp_path, force_python):
    _seed_repo(tmp_path)
    out = await tool.execute(
        {"pattern": "hello", "path": str(tmp_path), "glob": "*.py"}, session,
    )

    assert "a.py" in out
    assert "README.md" not in out


async def test_case_insensitive_flag(tool, session, tmp_path, force_python):
    f = tmp_path / "x.txt"
    f.write_text("Hello World\n")

    out_strict = await tool.execute(
        {"pattern": "hello", "path": str(tmp_path)}, session,
    )
    assert "(no matches)" in out_strict

    out_loose = await tool.execute(
        {"pattern": "hello", "path": str(tmp_path), "case_insensitive": True},
        session,
    )
    assert "Hello World" in out_loose


async def test_no_matches_returns_marker(tool, session, tmp_path, force_python):
    (tmp_path / "x.txt").write_text("nothing here\n")
    out = await tool.execute(
        {"pattern": "totally_absent", "path": str(tmp_path)}, session,
    )
    assert "(no matches)" in out


async def test_invalid_regex_raises(tool, session, tmp_path, force_python):
    (tmp_path / "x.txt").write_text("anything\n")
    with pytest.raises(ToolError, match="Invalid regex"):
        await tool.execute({"pattern": "(unclosed", "path": str(tmp_path)}, session)


async def test_missing_path_raises(tool, session, tmp_path, force_python):
    with pytest.raises(ToolError, match="not found"):
        await tool.execute(
            {"pattern": "x", "path": str(tmp_path / "nowhere")}, session,
        )


async def test_max_results_caps_output(tool, session, tmp_path, force_python):
    f = tmp_path / "many.txt"
    f.write_text("\n".join(["match-line"] * 50) + "\n")

    out = await tool.execute(
        {"pattern": "match-line", "path": str(tmp_path), "max_results": 3},
        session,
    )

    assert out.count("match-line") == 3


@pytest.mark.skipif(
    shutil.which("rg") is None, reason="ripgrep not available on PATH",
)
async def test_ripgrep_path_works_when_available(tool, session, tmp_path):
    """Smoke test the rg branch end-to-end when the host has rg installed."""
    _seed_repo(tmp_path)
    out = await tool.execute(
        {"pattern": "hello", "path": str(tmp_path), "glob": "*.py"}, session,
    )
    assert "a.py" in out
