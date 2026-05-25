"""Channel factory — build platform channels from settings + CLI flags.

Both `sanfuclaw start` (cli.py) and `sanfuclaw serve` (gateway/server.py)
now share this construction so adding a new channel touches one place.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field

from sanfuclaw.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ChannelBuildResult:
    """Outcome of building a set of channels.

    `channels` are unstarted instances ready for `await ch.start()`. The
    caller decides start ordering and whether a start failure is fatal.
    `skipped` lists name + reason for channels that couldn't be built
    (missing credentials, etc.) so the CLI can warn the user.
    """

    channels: list = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def build_channels(
    settings: Settings,
    requested: list[str],
    *,
    cli_session_id: str | None = None,
) -> ChannelBuildResult:
    """Build the requested channels. Unknown names are skipped with a reason.

    `requested` is a flat list — `["all"]` expands to every supported
    channel. `cli_session_id`, when provided, pins the CLI channel to an
    existing session (used by `--resume`); otherwise a fresh id is generated.
    """
    result = ChannelBuildResult()
    names = _expand_requested(requested)

    for name in names:
        builder = _BUILDERS.get(name)
        if builder is None:
            result.skipped.append((name, f"Unknown channel: {name}"))
            continue
        channel, reason = builder(settings, cli_session_id=cli_session_id)
        if channel is not None:
            result.channels.append(channel)
        else:
            result.skipped.append((name, reason or "unavailable"))

    return result


def _expand_requested(requested: list[str]) -> list[str]:
    if "all" in requested:
        return list(_BUILDERS.keys())
    seen: set[str] = set()
    out: list[str] = []
    for name in requested:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _build_cli(settings: Settings, *, cli_session_id: str | None):
    from sanfuclaw.channels.cli_channel import CLIChannel

    sid = cli_session_id or f"cli-{uuid.uuid4().hex[:8]}"
    return CLIChannel(session_id=sid), ""


def _build_telegram(settings: Settings, *, cli_session_id: str | None):
    tg_config = settings.channels.get("telegram")
    bot_token = getattr(tg_config, "bot_token", "") if tg_config else ""
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return None, (
            "Telegram bot token not found. Set channels.telegram.bot_token "
            "in config or TELEGRAM_BOT_TOKEN env var."
        )
    from sanfuclaw.channels.telegram import TelegramChannel

    allowed = getattr(tg_config, "allowed_users", None) if tg_config else None
    return TelegramChannel(bot_token=bot_token, allowed_users=allowed), ""


def _build_weixin(settings: Settings, *, cli_session_id: str | None):
    from sanfuclaw.channels.weixin import WeixinChannel

    wx = WeixinChannel()
    if not wx._creds.is_valid:
        return None, (
            "WeChat not logged in. Run 'sanfuclaw weixin-login' first "
            "(generates ~/.sanfuclaw/weixin_credentials.json)."
        )
    return wx, ""


# Registry of channel builders. Key order = expansion order for `all`.
_BUILDERS = {
    "cli": _build_cli,
    "telegram": _build_telegram,
    "weixin": _build_weixin,
}
