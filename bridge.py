# -*- coding: utf-8 -*-
"""防空洞核心：把 WebUI 里发来的文字，包装成和 QQ 私聊完全同一个会话的 AstrMessageEvent。"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)

ROLE_USER = "user"
ROLE_BOT = "bot"
ROLE_SYSTEM = "system"


def _to_chain(message: Any) -> MessageChain | None:
    """把 send() 收到的各种东西统一成 MessageChain。"""
    if isinstance(message, MessageChain):
        return message
    if isinstance(message, str):
        return MessageChain([Plain(message)])
    if isinstance(message, list):
        try:
            return MessageChain(message)
        except Exception:  # noqa: BLE001
            return None
    chain = getattr(message, "chain", None)
    if isinstance(chain, list):
        try:
            return MessageChain(chain)
        except Exception:  # noqa: BLE001
            return None
    return None


class BunkerEvent(AstrMessageEvent):
    """一个假的私聊事件：不连任何平台，只把回复收进列表。"""

    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.replies: list[str] = []
        self.finished = asyncio.Event()

    def _record(self, chain: MessageChain | None) -> None:
        if chain is None:
            return
        parts: list[str] = []
        for comp in getattr(chain, "chain", []) or []:
            kind = getattr(comp, "type", "")
            kind = str(getattr(kind, "value", kind) or "").lower()
            if kind == "plain":
                parts.append(str(getattr(comp, "text", "") or ""))
            elif kind == "image":
                parts.append("[图片]")
            elif kind == "record":
                parts.append("[语音]")
            elif kind == "file":
                parts.append("[文件]")
        text = "".join(parts).strip()
        if text:
            self.replies.append(text)

    async def send(self, message: MessageChain) -> None:  # type: ignore[override]
        try:
            await super().send(message)
        except Exception:  # noqa: BLE001
            logger.debug("[Bunker] 基线 send 记录失败，忽略", exc_info=True)
        self._record(_to_chain(message))
        self.finished.set()

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:  # type: ignore[override]
        self._has_send_oper = True
        try:
            async for chain in generator:
                self._record(_to_chain(chain))
        finally:
            self.finished.set()


class BunkerBridge:
    """会话映射 + 历史存储。"""

    def __init__(self, context: Any, config: dict[str, Any] | None, data_dir: Path) -> None:
        self.context = context
        self.config = dict(config or {})
        self.owner_qq = str(self.config.get("owner_qq") or "").strip()
        self.platform_id = str(self.config.get("platform_id") or "").strip()
        self.sender_name = str(self.config.get("sender_name") or "用户").strip() or "用户"
        self.bot_name = str(self.config.get("bot_name") or "鲸鲸").strip() or "鲸鲸"
        try:
            self.reply_timeout = max(20.0, float(self.config.get("reply_timeout") or 180))
        except (TypeError, ValueError):
            self.reply_timeout = 180.0
        try:
            self.max_history = max(40, int(self.config.get("max_history") or 300))
        except (TypeError, ValueError):
            self.max_history = 300

        self.data_dir = Path(data_dir)
        self.history_path = self.data_dir / "history.json"
        self._history: list[dict[str, Any]] = self._load_history()
        self._counter = max((int(item.get("id") or 0) for item in self._history), default=0)
        self._lock = asyncio.Lock()

        self.platform_meta = PlatformMetadata(
            name="aiocqhttp",
            description="防空洞旁路通道",
            id=self.platform_id,
            support_streaming_message=False,
            support_proactive_message=False,
        )

    # ---------- 会话标识 ----------

    @property
    def umo(self) -> str:
        return f"{self.platform_id}:{MessageType.FRIEND_MESSAGE.value}:{self.owner_qq}"

    def describe(self) -> dict[str, Any]:
        return {
            "umo": self.umo,
            "platform_id": self.platform_id,
            "owner_qq": self.owner_qq,
            "sender_name": self.sender_name,
            "bot_name": self.bot_name,
            "timeout": self.reply_timeout,
            "count": len(self._history),
        }

    # ---------- 历史 ----------

    def _load_history(self) -> list[dict[str, Any]]:
        try:
            raw = self.history_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            logger.warning("[Bunker] 历史文件读取失败，忽略", exc_info=True)
        return []

    def _save_history(self) -> None:
        try:
            payload = json.dumps(self._history[-self.max_history :], ensure_ascii=False)
            tmp = self.history_path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.history_path)
        except Exception:  # noqa: BLE001
            logger.warning("[Bunker] 历史写入失败", exc_info=True)

    def add(self, role: str, text: str) -> dict[str, Any]:
        self._counter += 1
        item = {
            "id": self._counter,
            "role": role,
            "text": str(text or ""),
            "ts": int(time.time()),
        }
        self._history.append(item)
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history :]
        self._save_history()
        return item

    def since(self, after_id: int = 0) -> list[dict[str, Any]]:
        try:
            after = int(after_id)
        except (TypeError, ValueError):
            after = 0
        return [item for item in self._history if int(item.get("id") or 0) > after]

    def clear(self) -> None:
        self._history = []
        self._save_history()

    # ---------- 事件构造 ----------

    def _build_event(self, text: str) -> BunkerEvent:
        abm = AstrBotMessage()
        abm.type = MessageType.FRIEND_MESSAGE
        abm.self_id = self.platform_id
        abm.session_id = self.owner_qq
        abm.message_id = uuid.uuid4().hex
        abm.sender = MessageMember(user_id=self.owner_qq, nickname=self.sender_name)
        abm.message = [Plain(text)]
        abm.message_str = text
        abm.timestamp = int(time.time())
        abm.raw_message = {"source": "bunker"}
        return BunkerEvent(text, abm, self.platform_meta, self.owner_qq)

    def _event_queue(self) -> Any:
        getter = getattr(self.context, "get_event_queue", None)
        if callable(getter):
            queue = getter()
            if queue is not None:
                return queue
        return getattr(self.context, "_event_queue", None)

    # ---------- 收发 ----------

    async def send_text(self, text: str) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "empty", "message": "内容是空的"}
        queue = self._event_queue()
        if queue is None:
            return {"ok": False, "error": "no_queue", "message": "拿不到事件队列，插件没装好？"}

        async with self._lock:
            event = self._build_event(text)
            user_item = self.add(ROLE_USER, text)
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                return {"ok": False, "error": "queue_full", "message": "事件队列满了，等一下再发"}
            timed_out = False
            try:
                await asyncio.wait_for(event.finished.wait(), timeout=self.reply_timeout)
            except asyncio.TimeoutError:
                timed_out = True
                logger.warning("[Bunker] %.0f 秒内没有收到回复", self.reply_timeout)
            except asyncio.CancelledError:
                raise
            bot_items = [self.add(ROLE_BOT, reply) for reply in event.replies]

        replies = [item["text"] for item in bot_items]
        if not replies and timed_out:
            note = self.add(ROLE_SYSTEM, "这次没等到回复，可能回复发到 QQ 那边去了，或者模型超时。")
            return {
                "ok": True,
                "timed_out": True,
                "replies": [],
                "user": user_item,
                "messages": [note],
            }
        return {
            "ok": True,
            "timed_out": timed_out,
            "replies": replies,
            "user": user_item,
            "messages": bot_items,
        }

    def leave_message(self, text: str) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "empty", "message": "没内容"}
        item = self.add(ROLE_BOT, text)
        return {"ok": True, "message_id": item["id"], "message": "留在防空洞了"}
