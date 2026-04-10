"""Gateway Server — FastAPI app with WebSocket and HTTP endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

from sanfuclaw.core.config import Settings
from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.types import MessageRole
from sanfuclaw.gateway.session_manager import SessionManager
from sanfuclaw.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class GatewayServer:
    """WebSocket-based gateway server."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store: SQLiteStore | None = None
        self.session_manager: SessionManager | None = None
        self._router = None
        self._active_connections: dict[str, WebSocket] = {}
        self.app = self._create_app()

    def _create_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Startup
            self.store = SQLiteStore()
            await self.store.init()
            self.session_manager = SessionManager(self.store)
            await self._setup_router()
            logger.info(f"Gateway started on {self.settings.gateway.host}:{self.settings.gateway.port}")
            yield
            # Shutdown
            if self.store:
                await self.store.close()

        app = FastAPI(
            title="Sanfuclaw Gateway",
            version="0.1.0",
            lifespan=lifespan,
        )

        # --- HTTP endpoints ---
        @app.get("/health")
        async def health():
            return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

        @app.get("/api/status")
        async def status():
            return {
                "version": "0.1.0",
                "connections": len(self._active_connections),
                "provider": self.settings.llm.provider,
                "model": self.settings.llm.model,
            }

        @app.get("/api/sessions")
        async def list_sessions():
            if not self.store:
                return []
            db = self.store._ensure_db()
            async with db.execute(
                "SELECT id, channel_id, sender_id, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 50"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {"id": r[0], "channel_id": r[1], "sender_id": r[2], "created_at": r[3], "updated_at": r[4]}
                    for r in rows
                ]

        @app.get("/api/sessions/{session_id}/messages")
        async def get_messages(session_id: str, limit: int = 50):
            if not self.store:
                return []
            messages = await self.store.get_history(session_id, limit)
            return [
                {"id": m.id, "role": m.role.value, "content": m.content, "timestamp": m.timestamp.isoformat()}
                for m in messages
            ]

        # --- WebSocket endpoint ---
        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await ws.accept()
            conn_id = f"ws-{id(ws)}"
            self._active_connections[conn_id] = ws
            logger.info(f"WebSocket connected: {conn_id}")

            try:
                while True:
                    data = await ws.receive_text()
                    await self._handle_ws_message(ws, conn_id, data)
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected: {conn_id}")
            finally:
                self._active_connections.pop(conn_id, None)

        # --- WebChat UI ---
        webchat_dir = Path(__file__).parent.parent / "webchat"
        if webchat_dir.exists():
            @app.get("/")
            async def webchat():
                html = (webchat_dir / "index.html").read_text()
                return HTMLResponse(html)

            app.mount("/static", StaticFiles(directory=str(webchat_dir)), name="static")

        return app

    async def _setup_router(self):
        """Set up the agent router."""
        from sanfuclaw.agents.llm_agent import LLMAgent
        from sanfuclaw.gateway.router import Router
        from sanfuclaw.tools.registry import ToolRegistry
        from sanfuclaw.tools.shell import ShellTool
        from sanfuclaw.tools.web_fetch import WebFetchTool

        tool_registry = ToolRegistry()
        tool_registry.register(ShellTool())
        tool_registry.register(WebFetchTool())

        transport = self._build_transport()
        agent = LLMAgent(
            name="default",
            transport=transport,
            tool_registry=tool_registry,
            system_prompt=self.settings.llm.system_prompt,
            model=self.settings.llm.model,
            max_tokens=self.settings.llm.max_tokens,
            temperature=self.settings.llm.temperature,
        )

        self._router = Router(session_manager=self.session_manager)
        self._router.register_agent(agent, default=True)

        # Register a WebSocket pseudo-channel
        ws_channel = WSChannel(self)
        self._router.register_channel(ws_channel)

    def _build_transport(self):
        api_key = self.settings.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
        if self.settings.llm.provider == "anthropic":
            from sanfuclaw.agents.transports.anthropic import AnthropicTransport
            return AnthropicTransport(api_key=api_key, default_model=self.settings.llm.model)
        else:
            from sanfuclaw.agents.transports.openai_compat import OpenAICompatTransport
            return OpenAICompatTransport(
                api_key=api_key,
                base_url=self.settings.llm.base_url,
                default_model=self.settings.llm.model,
            )

    async def _handle_ws_message(self, ws: WebSocket, conn_id: str, raw: str):
        """Handle incoming WebSocket message (JSON wire protocol)."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_json({"type": "error", "data": "Invalid JSON"})
            return

        msg_type = data.get("type", "")

        if msg_type == "message":
            content = data.get("content", "")
            session_id = data.get("session_id", f"ws-{conn_id}")
            sender_id = data.get("sender_id", conn_id)

            message = Message(
                role=MessageRole.USER,
                content=content,
                channel_id="webchat",
                session_id=session_id,
                sender_id=sender_id,
            )
            envelope = Envelope(message=message, source_channel="webchat")

            # Store the WebSocket connection for this session
            self._active_connections[session_id] = ws

            # Route through the agent
            if self._router:
                try:
                    await self._router.route(envelope)
                except Exception as e:
                    await ws.send_json({"type": "error", "data": str(e)})

        elif msg_type == "ping":
            await ws.send_json({"type": "pong"})

    async def send_to_ws(self, session_id: str, msg_type: str, data: str):
        """Send a message to the WebSocket client for a session."""
        ws = self._active_connections.get(session_id)
        if ws:
            try:
                await ws.send_json({"type": msg_type, "data": data})
            except Exception:
                self._active_connections.pop(session_id, None)


class WSChannel:
    """Pseudo-channel that routes messages to/from WebSocket clients."""

    name = "webchat"

    def __init__(self, server: GatewayServer):
        self._server = server

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, session_id: str, content: str, **kwargs) -> None:
        streaming = kwargs.get("streaming", False)
        if streaming:
            await self._server.send_to_ws(session_id, "stream", content)
        else:
            await self._server.send_to_ws(session_id, "message", content)

    async def send_typing(self, session_id: str) -> None:
        await self._server.send_to_ws(session_id, "typing", "")

    async def receive(self):
        # Not used — messages come via WebSocket handler directly
        while False:
            yield  # pragma: no cover
