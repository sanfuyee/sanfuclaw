"""WeChat (iLink Bot) channel — reverse-engineered from @tencent-weixin/openclaw-weixin."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import secrets
import string
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.types import MessageRole

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
APP_ID = "wx_bot"
APP_VERSION = "0.0.1"
CHANNEL_VERSION = "2.1.7"


def _generate_client_id() -> str:
    """Generate a unique client_id matching the OpenClaw format."""
    return f"sanfuclaw:{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def _random_uin() -> str:
    """Generate a random X-WECHAT-UIN header value (base64-encoded random bytes)."""
    raw = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    return base64.b64encode(raw.encode()).decode()


class WeixinCredentials:
    """Persisted iLink Bot credentials."""

    def __init__(self, path: str | Path = "weixin_credentials.json"):
        self.path = Path(path)
        self.bot_token: str = ""
        self.bot_id: str = ""
        self.user_id: str = ""
        self.base_url: str = DEFAULT_BASE_URL
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.bot_token = data.get("bot_token", "")
                self.bot_id = data.get("bot_id", "")
                self.user_id = data.get("user_id", "")
                self.base_url = data.get("base_url", DEFAULT_BASE_URL)
            except Exception:
                logger.warning("Failed to load WeChat credentials, will need re-login")

    def save(self) -> None:
        self.path.write_text(json.dumps({
            "bot_token": self.bot_token,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "base_url": self.base_url,
        }, indent=2))

    @property
    def is_valid(self) -> bool:
        return bool(self.bot_token)


class WeixinAPI:
    """Low-level HTTP client for the iLink Bot API."""

    def __init__(self, credentials: WeixinCredentials):
        self.creds = credentials
        self._uin = _random_uin()
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.creds.base_url,
                timeout=httpx.Timeout(60.0, connect=10.0),
                verify=False,
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.creds.bot_token}",
            "X-WECHAT-UIN": self._uin,
            "iLink-App-Id": APP_ID,
            "iLink-App-ClientVersion": APP_VERSION,
        }

    def _base_info(self) -> dict[str, str]:
        return {"channel_version": CHANNEL_VERSION}

    async def get_updates(
        self, get_updates_buf: str = "", timeout_ms: int = 35000
    ) -> dict[str, Any]:
        """Long-poll for new messages."""
        client = await self._ensure_client()
        resp = await client.post(
            "/ilink/bot/getupdates",
            headers=self._headers(),
            json={"get_updates_buf": get_updates_buf, "base_info": self._base_info()},
            timeout=httpx.Timeout(timeout_ms / 1000 + 10, connect=10.0),
        )
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, req: dict[str, Any]) -> dict[str, Any]:
        """Send a message to a WeChat user."""
        client = await self._ensure_client()
        body = {**req, "base_info": self._base_info()}
        resp = await client.post(
            "/ilink/bot/sendmessage",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    async def send_typing(self, typing_ticket: str) -> dict[str, Any]:
        """Send a typing indicator."""
        client = await self._ensure_client()
        resp = await client.post(
            "/ilink/bot/sendtyping",
            headers=self._headers(),
            json={"typing_ticket": typing_ticket, "base_info": self._base_info()},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_config(self, user_id: str, context_token: str = "") -> dict[str, Any]:
        """Get bot configuration (includes typing_ticket)."""
        client = await self._ensure_client()
        resp = await client.post(
            "/ilink/bot/getconfig",
            headers=self._headers(),
            json={
                "ilink_user_id": user_id,
                "context_token": context_token,
                "base_info": self._base_info(),
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


def _print_qr_terminal(url: str) -> None:
    """Generate and render a QR code from a URL in the terminal."""
    try:
        import qrcode
    except ImportError:
        print(f"QR URL: {url}")
        print("(Install 'qrcode' for terminal QR display: pip install 'sanfuclaw[weixin]')")
        return

    qr = qrcode.QRCode(box_size=1, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print()


async def qr_login(base_url: str = DEFAULT_BASE_URL) -> WeixinCredentials:
    """Perform QR code login flow. Returns credentials on success."""
    async with httpx.AsyncClient(base_url=base_url, verify=False, timeout=30.0) as client:
        headers = {
            "iLink-App-Id": APP_ID,
            "iLink-App-ClientVersion": APP_VERSION,
        }

        max_retries = 3
        for attempt in range(max_retries):
            # Step 1: Get QR code
            resp = await client.get(
                "/ilink/bot/get_bot_qrcode",
                params={"bot_type": "3"},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            qrcode = data.get("qrcode", "")
            qrcode_url = data.get("qrcode_img_content", "")

            if not qrcode:
                raise RuntimeError("Failed to get QR code from server")

            # Display QR code — qrcode_img_content is a URL to encode as QR
            print("\n=== WeChat Login ===")
            if qrcode_url:
                print("Scan this QR code with WeChat:\n")
                _print_qr_terminal(qrcode_url)
                print(f"Or open this link in browser: {qrcode_url}")
            else:
                print(f"No QR URL received. Token: {qrcode}")

            print("Waiting for scan...")

            # Step 2: Poll for QR code status
            current_base_url = base_url
            while True:
                try:
                    resp = await client.get(
                        "/ilink/bot/get_qrcode_status",
                        params={"qrcode": qrcode},
                        headers=headers,
                        timeout=60.0,
                    )
                    resp.raise_for_status()
                    status_data = resp.json()
                except httpx.TimeoutException:
                    continue

                status = status_data.get("status", "")

                if status == "wait":
                    continue
                elif status == "scaned":
                    print("QR code scanned! Waiting for confirmation...")
                    continue
                elif status == "confirmed":
                    print("Login confirmed!")
                    creds = WeixinCredentials()
                    creds.bot_token = status_data.get("bot_token", "")
                    creds.bot_id = status_data.get("ilink_bot_id", "")
                    creds.user_id = status_data.get("ilink_user_id", "")
                    creds.base_url = status_data.get("baseurl", "") or current_base_url
                    creds.save()
                    return creds
                elif status == "scaned_but_redirect":
                    # IDC redirect — switch to new host
                    redirect_host = status_data.get("redirect_host", "")
                    if redirect_host:
                        current_base_url = f"https://{redirect_host}"
                        print(f"Redirecting to {current_base_url}...")
                    continue
                elif status == "expired":
                    print(f"QR code expired. Retrying ({attempt + 1}/{max_retries})...")
                    break
                else:
                    logger.warning(f"Unknown QR status: {status}")
                    continue

        raise RuntimeError("QR login failed after max retries")


class WeixinChannel:
    """WeChat channel adapter using the iLink Bot API.

    Implements the Channel protocol for sanfuclaw.
    """

    name: str = "weixin"

    def __init__(self, credentials_path: str | Path = "weixin_credentials.json"):
        self._creds = WeixinCredentials(credentials_path)
        self._api = WeixinAPI(self._creds)
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self._poll_task: asyncio.Task | None = None
        self._get_updates_buf: str = ""
        self._context_tokens: dict[str, str] = {}  # user_id -> context_token
        self._typing_ticket: str = ""
        self._running = False
        self._consecutive_failures = 0
        self._poll_timeout_ms = 35000
        self._response_buffers: dict[str, str] = {}  # session_id -> accumulated text

    async def start(self) -> None:
        if not self._creds.is_valid:
            raise RuntimeError(
                "WeChat credentials not found. Run 'sanfuclaw weixin-login' first."
            )

        # Typing ticket will be fetched on first message (needs user_id + context_token)

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("WeChat channel started")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._api.close()
        logger.info("WeChat channel stopped")

    async def _poll_loop(self) -> None:
        """Long-poll loop for incoming messages."""
        logger.info("WeChat poll loop started")
        while self._running:
            try:
                data = await self._api.get_updates(
                    get_updates_buf=self._get_updates_buf,
                    timeout_ms=self._poll_timeout_ms,
                )

                # Check for session expiry
                errcode = data.get("errcode", 0)
                if errcode == -14:
                    logger.error("WeChat session expired. Please re-login with 'sanfuclaw weixin-login'")
                    self._running = False
                    return
                if errcode and errcode != 0:
                    logger.warning(f"WeChat API errcode={errcode}: {data.get('errmsg', '')}")

                # Update pagination cursor
                new_buf = data.get("get_updates_buf", "")
                if new_buf:
                    self._get_updates_buf = new_buf

                # Update poll timeout if server suggests one
                suggested_timeout = data.get("longpolling_timeout_ms", 0)
                if suggested_timeout > 0:
                    self._poll_timeout_ms = suggested_timeout

                # Process messages
                msgs = data.get("msgs", [])
                if msgs:
                    logger.info(f"WeChat received {len(msgs)} message(s)")
                for msg in msgs:
                    self._process_inbound(msg)

                self._consecutive_failures = 0

            except httpx.TimeoutException:
                # Normal for long-polling
                continue
            except Exception as e:
                self._consecutive_failures += 1
                logger.error(f"WeChat poll error: {e}")
                if self._consecutive_failures >= 3:
                    logger.warning("3 consecutive failures, backing off 30s")
                    await asyncio.sleep(30)
                    self._consecutive_failures = 0
                else:
                    await asyncio.sleep(2)

    def _process_inbound(self, raw_msg: dict[str, Any]) -> None:
        """Convert a raw WeChat message into an Envelope and enqueue it."""
        msg_type = raw_msg.get("message_type", 0)
        from_user = raw_msg.get("from_user_id", "")
        # Only process user messages (message_type == 1)
        if msg_type != 1:
            return

        context_token = raw_msg.get("context_token", "")

        # Store context_token for replies
        if from_user and context_token:
            self._context_tokens[from_user] = context_token
            # Refresh typing ticket if we don't have one
            if not self._typing_ticket:
                asyncio.create_task(self._fetch_typing_ticket(from_user, context_token))

        # Extract text from item_list
        text = self._extract_text(raw_msg.get("item_list", []))
        if not text:
            return

        logger.info(f"WeChat message from {from_user}")
        session_id = f"wx-{from_user}"
        message = Message(
            role=MessageRole.USER,
            content=text,
            channel_id=self.name,
            session_id=session_id,
            sender_id=from_user,
            metadata={
                "context_token": context_token,
                "client_id": raw_msg.get("client_id", ""),
            },
        )
        self._queue.put_nowait(Envelope(
            message=message,
            source_channel=self.name,
        ))

    def _extract_text(self, item_list: list[dict[str, Any]]) -> str:
        """Extract text content from message item_list."""
        parts = []
        for item in item_list:
            item_type = item.get("type", 0)
            if item_type == 1:  # TEXT
                text_item = item.get("text_item", {})
                text = text_item.get("text", "")
                if text:
                    parts.append(text)
            elif item_type == 3:  # VOICE — use voice-to-text if available
                voice_item = item.get("voice_item", {})
                vtt = voice_item.get("text", "")
                if vtt:
                    parts.append(vtt)
        return "\n".join(parts)

    async def send(self, session_id: str, content: str, **kwargs) -> None:
        """Send a message back to the WeChat user.

        Accumulates streaming chunks and sends as one message when done.
        """
        done = kwargs.get("done", False)

        if done:
            text = self._response_buffers.pop(session_id, content).strip()
            if text:
                await self._send_text(session_id, text)
        else:
            if session_id not in self._response_buffers:
                self._response_buffers[session_id] = ""
            self._response_buffers[session_id] += content

    async def _send_text(self, session_id: str, text: str) -> None:
        """Send a complete text message to the WeChat user."""
        user_id = session_id.removeprefix("wx-")
        context_token = self._context_tokens.get(user_id, "")
        logger.info(f"WeChat reply to {user_id} ({len(text)} chars)")

        req = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": _generate_client_id(),
                "message_type": 2,  # BOT
                "message_state": 2,  # FINISH
                "item_list": [
                    {
                        "type": 1,  # TEXT
                        "text_item": {"text": text},
                    }
                ],
                "context_token": context_token,
            }
        }

        try:
            await self._api.send_message(req)
        except Exception as e:
            logger.error(f"Failed to send WeChat message: {e}")

    async def _fetch_typing_ticket(self, user_id: str, context_token: str) -> None:
        """Fetch typing ticket from getconfig."""
        try:
            config = await self._api.get_config(user_id, context_token)
            self._typing_ticket = config.get("typing_ticket", "")
        except Exception as e:
            logger.warning(f"Failed to get typing ticket: {e}")

    async def send_typing(self, session_id: str) -> None:
        """Send typing indicator via WeChat."""
        if not self._typing_ticket:
            return
        try:
            await self._api.send_typing(self._typing_ticket)
        except Exception:
            pass

    async def receive(self) -> AsyncIterator[Envelope]:
        """Yield envelopes from the internal queue."""
        while True:
            envelope = await self._queue.get()
            yield envelope
