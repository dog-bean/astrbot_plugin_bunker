# -*- coding: utf-8 -*-
"""防空洞拓展页 API。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.api.web import request

PAGE_PREFIX = "/astrbot_plugin_bunker/page/api"


class BunkerPageApi:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.bridge = plugin.bridge
        self._registered_routes: list[tuple[str, Any]] = []

    def register_routes(self) -> None:
        register = getattr(self.plugin.context, "register_web_api", None)
        if not callable(register):
            logger.warning("[Bunker] 当前 AstrBot 不支持插件拓展页 API")
            return
        routes = [
            ("/state", self.state, ["GET"], "防空洞初始化状态"),
            ("/poll", self.poll, ["GET"], "防空洞拉取新消息"),
            ("/send", self.send, ["POST"], "防空洞发送消息"),
            ("/clear", self.clear, ["POST"], "防空洞清屏"),
        ]
        for suffix, handler, methods, desc in routes:
            register(f"{PAGE_PREFIX}{suffix}", handler, methods, desc)
            self._registered_routes.append((f"{PAGE_PREFIX}{suffix}", handler))

    def unregister_routes(self) -> None:
        registered = getattr(self.plugin.context, "registered_web_apis", None)
        if not isinstance(registered, list):
            return
        owned = {(route, id(handler)) for route, handler in self._registered_routes}
        self.plugin.context.registered_web_apis = [
            item
            for item in registered
            if not (
                isinstance(item, tuple)
                and len(item) >= 2
                and (item[0], id(item[1])) in owned
            )
        ]
        self._registered_routes.clear()

    # ---------- 工具 ----------

    @staticmethod
    async def _payload() -> dict[str, Any]:
        try:
            value = await request.json(default={})
        except Exception:  # noqa: BLE001
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _arg(name: str, default: str = "") -> str:
        try:
            value = request.query.get(name)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else str(value)

    # ---------- 视图 ----------

    async def state(self):
        info = self.bridge.describe()
        return {
            "ok": True,
            **info,
            "messages": self.bridge.since(0)[-80:],
        }

    async def poll(self):
        after = self._arg("after", "0")
        try:
            after_id = int(after)
        except (TypeError, ValueError):
            after_id = 0
        return {"ok": True, "messages": self.bridge.since(after_id)}

    async def send(self):
        body = await self._payload()
        text = str(body.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "empty", "message": "内容是空的"}
        if len(text) > 4000:
            text = text[:4000]
        return await self.bridge.send_text(text)

    async def clear(self):
        self.bridge.clear()
        return {"ok": True, "messages": []}
