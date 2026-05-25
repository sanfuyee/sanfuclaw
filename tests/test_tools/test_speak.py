"""speak tool tests — backend selection on macOS / Linux + guards."""

from __future__ import annotations

import pytest

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.tools import speak as sp
from sanfuclaw.tools.speak import SpeakTool


@pytest.fixture
def session() -> Session:
    return Session(id="s-1", channel_id="cli", sender_id="u")


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Capture argv per spawn and return a fake process with a fixed pid."""
    state: dict = {"calls": []}

    class _FakeProc:
        pid = 4242

    async def _exec(*args, **kwargs):
        state["calls"].append({"args": args, "kwargs": kwargs})
        return _FakeProc()

    monkeypatch.setattr(sp.asyncio, "create_subprocess_exec", _exec)
    return state


# --- macOS ------------------------------------------------------------------


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sp.platform, "system", lambda: "Darwin")


async def test_macos_uses_say(mac, fake_subprocess, session):
    out = await SpeakTool().execute({"text": "hello there"}, session)

    args = fake_subprocess["calls"][0]["args"]
    assert args[0] == "say"
    assert args[-1] == "hello there"
    assert "say" in out
    assert "pid=4242" in out


async def test_macos_rate_passed_through(mac, fake_subprocess, session):
    await SpeakTool().execute({"text": "fast", "rate": 220}, session)

    args = fake_subprocess["calls"][0]["args"]
    # `say -r 220 fast`
    assert args[:3] == ("say", "-r", "220")


# --- Linux: espeak-ng preferred --------------------------------------------


@pytest.fixture
def linux_espeak_ng(monkeypatch):
    monkeypatch.setattr(sp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        sp.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "espeak-ng" else None,
    )


async def test_linux_picks_espeak_ng_first(linux_espeak_ng, fake_subprocess, session):
    await SpeakTool().execute({"text": "linux hi", "rate": 180}, session)
    args = fake_subprocess["calls"][0]["args"]
    assert args[:3] == ("espeak-ng", "-s", "180")
    assert args[-1] == "linux hi"


# --- Linux: espeak fallback when espeak-ng absent --------------------------


@pytest.fixture
def linux_espeak(monkeypatch):
    monkeypatch.setattr(sp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        sp.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "espeak" else None,
    )


async def test_linux_falls_back_to_espeak(linux_espeak, fake_subprocess, session):
    await SpeakTool().execute({"text": "fallback"}, session)
    assert fake_subprocess["calls"][0]["args"][0] == "espeak"


# --- Linux: spd-say drops rate (no WPM knob) -------------------------------


@pytest.fixture
def linux_spd(monkeypatch):
    monkeypatch.setattr(sp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        sp.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "spd-say" else None,
    )


async def test_spd_say_ignores_rate(linux_spd, fake_subprocess, session):
    out = await SpeakTool().execute({"text": "speech-d", "rate": 220}, session)
    args = fake_subprocess["calls"][0]["args"]
    assert args[0] == "spd-say"
    # Rate intentionally not forwarded — spd-say's -r isn't WPM and we
    # refuse to silently translate.
    assert "-r" not in args
    assert "220" not in args
    # And the response shouldn't claim a wpm we didn't honor.
    assert "wpm" not in out


# --- No backend / unsupported platform -------------------------------------


async def test_linux_no_backend_returns_install_hint(monkeypatch, session):
    monkeypatch.setattr(sp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sp.shutil, "which", lambda name: None)

    with pytest.raises(ToolError, match="espeak-ng"):
        await SpeakTool().execute({"text": "hi"}, session)


async def test_unsupported_platform(monkeypatch, session):
    monkeypatch.setattr(sp.platform, "system", lambda: "Windows")
    with pytest.raises(ToolError, match="not supported"):
        await SpeakTool().execute({"text": "hi"}, session)


# --- Input guards ----------------------------------------------------------


async def test_empty_text_rejected(mac, fake_subprocess, session):
    with pytest.raises(ToolError, match="non-empty"):
        await SpeakTool().execute({"text": "   "}, session)


async def test_oversized_text_refused(mac, fake_subprocess, session, monkeypatch):
    monkeypatch.setattr(sp, "_MAX_CHARS", 16)
    with pytest.raises(ToolError, match="too long"):
        await SpeakTool().execute({"text": "x" * 100}, session)


async def test_invalid_rate_rejected(mac, fake_subprocess, session):
    with pytest.raises(ToolError, match="rate"):
        await SpeakTool().execute({"text": "hi", "rate": "fast"}, session)
