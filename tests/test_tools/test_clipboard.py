"""Clipboard tool tests — backend selection on macOS / Linux X11 / Wayland.

The real subprocess calls are mocked so tests don't depend on whether
xclip/xsel/wl-paste are installed on the runner.
"""

from __future__ import annotations

import pytest

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.tools import clipboard as cb
from sanfuclaw.tools.clipboard import ClipboardReadTool, ClipboardWriteTool


@pytest.fixture
def session() -> Session:
    return Session(id="s-1", channel_id="cli", sender_id="u")


class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.received_stdin: bytes | None = None

    async def communicate(self, input: bytes | None = None):
        self.received_stdin = input
        return self._stdout, self._stderr


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Capture the command + stdin and return a configurable fake process."""
    state: dict = {"calls": [], "next_proc": _FakeProc(0)}

    async def _exec(*args, **kwargs):
        state["calls"].append({"args": args, "stdin": kwargs.get("stdin")})
        proc = state["next_proc"]

        async def _communicate(input=None):
            proc.received_stdin = input
            return proc._stdout, proc._stderr

        proc.communicate = _communicate
        return proc

    monkeypatch.setattr(cb.asyncio, "create_subprocess_exec", _exec)
    return state


# --- macOS ------------------------------------------------------------------


@pytest.fixture
def mac(monkeypatch):
    """Pin platform to Darwin so _backend selects pbcopy/pbpaste."""
    monkeypatch.setattr(cb.platform, "system", lambda: "Darwin")


async def test_macos_read_uses_pbpaste(mac, fake_subprocess, session):
    fake_subprocess["next_proc"] = _FakeProc(0, stdout=b"hello mac\n")
    out = await ClipboardReadTool().execute({}, session)

    assert out == "hello mac\n"
    args = fake_subprocess["calls"][0]["args"]
    assert args[0] == "pbpaste"


async def test_macos_write_pipes_into_pbcopy(mac, fake_subprocess, session):
    fake_subprocess["next_proc"] = _FakeProc(0)
    out = await ClipboardWriteTool().execute({"text": "hi mac"}, session)

    assert "Wrote 6 byte" in out
    args = fake_subprocess["calls"][0]["args"]
    assert args[0] == "pbcopy"
    assert fake_subprocess["next_proc"].received_stdin == b"hi mac"


async def test_empty_clipboard_marker(mac, fake_subprocess, session):
    fake_subprocess["next_proc"] = _FakeProc(0, stdout=b"")
    out = await ClipboardReadTool().execute({}, session)
    assert out == "(empty clipboard)"


async def test_oversized_write_refused(mac, fake_subprocess, session, monkeypatch):
    monkeypatch.setattr(cb, "_MAX_WRITE_BYTES", 16)
    with pytest.raises(ToolError, match="too large"):
        await ClipboardWriteTool().execute({"text": "x" * 100}, session)


async def test_read_truncates_oversized_clipboard(mac, fake_subprocess, session, monkeypatch):
    monkeypatch.setattr(cb, "_MAX_READ_BYTES", 8)
    fake_subprocess["next_proc"] = _FakeProc(0, stdout=b"abcdefghijklmnop")
    out = await ClipboardReadTool().execute({}, session)
    assert out.startswith("abcdefgh")
    assert "truncated" in out


# --- Linux X11 (xclip) ------------------------------------------------------


@pytest.fixture
def linux_x11(monkeypatch):
    """Pin platform to Linux + xclip available, no Wayland session."""
    monkeypatch.setattr(cb.platform, "system", lambda: "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        cb.shutil, "which", lambda name: "/usr/bin/xclip" if name == "xclip" else None,
    )


async def test_linux_x11_read_uses_xclip(linux_x11, fake_subprocess, session):
    fake_subprocess["next_proc"] = _FakeProc(0, stdout=b"x11 selection")
    out = await ClipboardReadTool().execute({}, session)

    assert out == "x11 selection"
    args = fake_subprocess["calls"][0]["args"]
    assert args[:3] == ("xclip", "-selection", "clipboard")


async def test_linux_x11_write_uses_xclip(linux_x11, fake_subprocess, session):
    fake_subprocess["next_proc"] = _FakeProc(0)
    await ClipboardWriteTool().execute({"text": "via xclip"}, session)
    args = fake_subprocess["calls"][0]["args"]
    assert args[0] == "xclip"


# --- Linux Wayland (wl-paste) ----------------------------------------------


@pytest.fixture
def linux_wayland(monkeypatch):
    """Pin platform to Linux + Wayland session + wl-* available."""
    monkeypatch.setattr(cb.platform, "system", lambda: "Linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    def _which(name):
        return f"/usr/bin/{name}" if name in ("wl-paste", "wl-copy") else None

    monkeypatch.setattr(cb.shutil, "which", _which)


async def test_linux_wayland_read_uses_wl_paste(linux_wayland, fake_subprocess, session):
    fake_subprocess["next_proc"] = _FakeProc(0, stdout=b"wayland buffer")
    out = await ClipboardReadTool().execute({}, session)

    assert out == "wayland buffer"
    args = fake_subprocess["calls"][0]["args"]
    assert args[0] == "wl-paste"
    assert "--no-newline" in args


# --- No backend available ---------------------------------------------------


async def test_linux_no_backend_returns_install_hint(monkeypatch, session):
    monkeypatch.setattr(cb.platform, "system", lambda: "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)

    with pytest.raises(ToolError, match="wl-clipboard"):
        await ClipboardReadTool().execute({}, session)


async def test_unsupported_platform(monkeypatch, session):
    monkeypatch.setattr(cb.platform, "system", lambda: "Windows")
    with pytest.raises(ToolError, match="not supported"):
        await ClipboardReadTool().execute({}, session)
