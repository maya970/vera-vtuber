"""弹幕入口。一次接一个平台的公开聊天，推流仍然只是把网页送出去。"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .config import config
from .danmaku import BiliDanmaku, DanmakuEvent
from .sources import douyu, kick, twitch, youtube
from .sources.parse import bili_room

log = logging.getLogger("vera.chat")

PLATFORMS = ("off", "bilibili", "youtube", "twitch", "kick", "douyu")
_RUNNERS = {
    "youtube": youtube.run,
    "twitch": twitch.run,
    "kick": kick.run,
    "douyu": douyu.run,
}


def resolve() -> tuple[str, str]:
    platform = str(config.get("chat.platform") or "").strip().lower()
    target = str(config.get("chat.target") or "").strip()
    if platform not in PLATFORMS:
        platform = "bilibili" if int(config.get("bilibili.room_id") or 0) else "off"
    if platform == "bilibili" and not target:
        room = int(config.get("bilibili.room_id") or 0)
        target = str(room) if room else ""
    if platform == "off":
        target = ""
    return platform, target


class ChatHub:
    def __init__(self, on_event: Callable[[DanmakuEvent], Awaitable[None]]):
        self._on_event = on_event
        self._bili = BiliDanmaku(self._from_bili)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.platform = "off"
        self.target = ""
        self.connected = False
        self.status = "未连接"
        self._last_error = ""

    async def _from_bili(self, event: DanmakuEvent) -> None:
        event.meta.setdefault("platform", "bilibili")
        await self._accept(event)

    async def _accept(self, event: DanmakuEvent) -> None:
        ignore = [item.lower() for item in (config.get("bilibili.ignore_users") or [])]
        if event.user and event.user.lower() in ignore:
            return
        if event.kind == "danmaku" and not (event.text or "").strip():
            return
        await self._on_event(event)

    def _mark(self, text: str, connected: bool) -> None:
        self.status = text
        self.connected = connected
        if connected:
            self._last_error = ""

    async def start(self) -> None:
        await self.stop()
        self._stop = asyncio.Event()
        self.platform, self.target = resolve()
        if self.platform in ("", "off") or not self.target:
            self.platform = "off"
            self.target = ""
            self.connected = False
            self.status = "未连接弹幕"
            return
        if self.platform == "bilibili":
            room = bili_room(self.target)
            if not room:
                self.status = "请填写 B 站房间号或直播间网址"
                self.connected = False
                return
            self._bili.room_id = room
            self.status = "连接中…"
            await self._bili.start()
            return
        self._task = asyncio.create_task(self._foreign(), name=f"chat-{self.platform}")

    async def stop(self) -> None:
        self._stop.set()
        await self._bili.stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None
        self.connected = False
        if self.platform != "off":
            self.status = "已停止"

    async def restart(self) -> None:
        await self.start()

    async def _foreign(self) -> None:
        runner = _RUNNERS.get(self.platform)
        if runner is None:
            self.status = "不认识这个平台"
            return
        backoff = 2.0
        while not self._stop.is_set():
            try:
                self._mark("连接中…", False)
                await runner(self.target, self._accept, self._stop, self._mark)
                if self._stop.is_set():
                    return
                raise RuntimeError("聊天连接中断")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                self.connected = False
                self.status = f"连接失败：{exc}"
                log.warning("%s 弹幕连接失败：%s", self.platform, exc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    return
                except TimeoutError:
                    backoff = min(backoff * 1.8, 30)

    def state(self) -> dict:
        if self.platform == "bilibili" and self._bili.room_id:
            data = self._bili.state()
            data["platform"] = "bilibili"
            data["target"] = self.target or str(data.get("room_id") or "")
            return data
        return {
            "platform": self.platform,
            "target": self.target,
            "room_id": self._bili.room_id if self.platform == "bilibili" else 0,
            "real_room_id": 0,
            "connected": self.connected,
            "status": self.status,
            "error": self._last_error,
        }
