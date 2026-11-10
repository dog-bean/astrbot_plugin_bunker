# -*- coding: utf-8 -*-
"""防空洞：不经过 QQ 也能跟同一个会话聊天的 WebUI 旁路通道。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .bridge import BunkerBridge
from .page_api import BunkerPageApi

PLUGIN_NAME = "astrbot_plugin_bunker"
PLUGIN_VERSION = "0.1.0"


@register(
    PLUGIN_NAME,
    "dog-bean",
    "防空洞：在 WebUI 插件扩展页里，用同一个会话和 Bot 聊天，不依赖 QQ 登录。",
    PLUGIN_VERSION,
    "",
)
class BunkerPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        super().__init__(context)
        self.context = context
        self.config = config if isinstance(config, dict) else {}
        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.bridge = BunkerBridge(context, self.config, data_dir)
        self.page_api: BunkerPageApi | None = None
        self._register_page_api()
        logger.info("[Bunker] 防空洞已启动，目标会话=%s", self.bridge.umo)

    async def initialize(self) -> None:
        logger.info("[Bunker] 防空洞就绪，历史 %d 条", len(self.bridge.since(0)))

    def _register_page_api(self) -> None:
        if not callable(getattr(self.context, "register_web_api", None)):
            logger.warning("[Bunker] 当前 AstrBot 不支持插件拓展页 API")
            return
        try:
            self.page_api = BunkerPageApi(self)
            self.page_api.register_routes()
        except Exception as exc:  # noqa: BLE001
            self.page_api = None
            logger.warning("[Bunker] 拓展页 API 注册失败: %s", exc, exc_info=True)

    @filter.command("防空洞自测")
    async def bunker_selftest(self, event: AstrMessageEvent):
        """从 QQ 这边丢一条测试消息进防空洞，验证旁路通道是否走通。"""
        if str(event.get_sender_id() or "") != self.bridge.owner_qq:
            yield event.plain_result("这个只有配置里的对话对象能用。")
            return
        asyncio.create_task(self.bridge.send_text("自测：防空洞走通了没有，回一句。"))
        yield event.plain_result("测试消息已经丢进去了，去防空洞网页看有没有回话。")

    @filter.llm_tool(name="bunker_leave_message")
    async def bunker_leave_message(self, event: AstrMessageEvent, text: str) -> str:
        """给防空洞留一句话。只有确实想主动留话、或对方不在 QQ 这边时才用。

        Args:
            text(string): 要留下的话，一句话，用你自己的口吻。
        """
        if str(event.get_sender_id() or "") != self.bridge.owner_qq:
            return "只有配置里的对话对象能用这个通道，别人的话我不转。"
        result = self.bridge.leave_message(text)
        if result.get("ok"):
            return "留好了，他开防空洞就能看见。"
        return "没留上。"

    async def terminate(self) -> None:
        if self.page_api is not None:
            try:
                self.page_api.unregister_routes()
            except Exception:  # noqa: BLE001
                logger.debug("[Bunker] 路由清理失败", exc_info=True)
        logger.info("[Bunker] 防空洞已关闭")
