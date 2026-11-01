"""Kick 公开聊天。2026 年 10 月起走 Centrifugo，按网页自己的步骤去连。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Awaitable, Callable

import aiohttp

from ..danmaku import DanmakuEvent
from .parse import kick_slug

log = logging.getLogger("vera.kick")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ORIGIN = "https://kick.com"

Emit = Callable[[DanmakuEvent], Awaitable[None]]
Status = Callable[[str, bool], None]


def _headers() -> dict:
    return {
        "User-Agent": UA,
        "Accept": "application/json",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/",
    }


async def _json(session: aiohttp.ClientSession, method: str, url: str, body: dict | None = None) -> dict:
    async with session.request(method, url, headers=_headers(), json=body) as resp:
        text = await resp.text(errors="replace")
        if resp.status >= 400:
            raise RuntimeError(f"Kick 没有放行这个请求（{resp.status}）")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Kick 返回的内容无法识别") from exc


def _message(data: object) -> DanmakuEvent | None:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    event = str(data.get("event") or "")
    inner = data.get("data", data)
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            return None
    if not isinstance(inner, dict):
        return None
    if event and "ChatMessage" not in event and "message" not in event.lower():
        content = inner.get("content")
        if not content:
            return None
    content = str(inner.get("content") or inner.get("message") or "").strip()
    sender = inner.get("sender") or inner.get("user") or {}
    if isinstance(sender, dict):
        user = str(sender.get("username") or sender.get("slug") or "").strip()
    else:
        user = str(sender or "").strip()
    if not content:
        return None
    return DanmakuEvent("danmaku", user=user or "观众", text=content, meta={"platform": "kick"})


async def run(target: str, emit: Emit, stop: asyncio.Event, status: Status) -> None:
    slug = kick_slug(target)
    if not slug:
        raise RuntimeError("请填写 Kick 频道名，或 kick.com/频道 这样的网址")
    client_id = str(uuid.uuid4())
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        info = await _json(session, "GET", f"https://kick.com/api/v2/channels/{slug}")
        channel = info.get("id") or (info.get("data") or {}).get("id")
        chatroom = (info.get("chatroom") or {}).get("id") or (info.get("data") or {}).get("chatroom", {}).get("id")
        if not channel or not chatroom:
            raise RuntimeError("没有找到这个 Kick 频道")
        body = {
            "client": {"id": client_id, "type": "web"},
            "capabilities": {"accepted_providers": [{"provider": "centrifugo"}]},
        }
        connected = await _json(
            session,
            "POST",
            f"https://web.kick.com/api/v1/realtime/channels/{channel}/chat/connection",
            body,
        )
        connections = ((connected.get("data") or connected).get("connections") or [])
        url = ""
        for item in connections:
            creds = item.get("credentials") or {}
            if item.get("provider") == "centrifugo" and creds.get("url"):
                url = creds["url"]
                break
        if not url:
            raise RuntimeError("Kick 没有给出聊天服务器")
        token_payload = await _json(
            session,
            "POST",
            "https://web.kick.com/api/v1/realtime/auth/connection",
            {"client_id": client_id},
        )
        token = str((token_payload.get("data") or token_payload).get("token") or "")
        if not token:
            raise RuntimeError("Kick 没有给出聊天凭证")
        async with session.ws_connect(url, headers={"Origin": ORIGIN, "User-Agent": UA}, heartbeat=20) as ws:
            await ws.send_json({"id": 1, "connect": {"token": token}})
            await ws.send_json({"id": 2, "subscribe": {"channel": f"chatrooms.{chatroom}.v2"}})
            status(f"Kick · 已连接 {slug}", True)
            async for msg in ws:
                if stop.is_set():
                    return
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise RuntimeError("Kick 聊天连接中断")
                    continue
                try:
                    frame = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if frame == {} or frame.get("ping") is not None:
                    await ws.send_json({})
                    continue
                pub = ((frame.get("push") or {}).get("pub") or {}).get("data")
                event = _message(pub)
                if event:
                    await emit(event)
            if not stop.is_set():
                raise RuntimeError("Kick 聊天连接中断")
