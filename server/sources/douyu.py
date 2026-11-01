"""斗鱼公开弹幕。按直播网页使用的那套房间协议读取。"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import struct
from typing import Awaitable, Callable

import aiohttp

from ..danmaku import DanmakuEvent
from .parse import douyu_room

log = logging.getLogger("vera.douyu")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
WS = "wss://danmuproxy.douyu.com:8506/"


def _ssl() -> ssl.SSLContext:
    """斗鱼弹幕代理不接受 Python 默认的加密套件，放宽到还能校验证书的一级。"""
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx

Emit = Callable[[DanmakuEvent], Awaitable[None]]
Status = Callable[[str, bool], None]


def encode(message: str) -> bytes:
    body = message.encode("utf-8") + b"\x00"
    length = len(body) + 12
    header = struct.pack("<IIHBB", length, length, 689, 0, 0)
    return header + body


def decode(data: bytes) -> list[str]:
    messages: list[str] = []
    pos = 0
    while pos + 12 <= len(data):
        length = struct.unpack_from("<I", data, pos)[0]
        if length < 12 or pos + length > len(data):
            break
        body = data[pos + 12:pos + length].rstrip(b"\x00")
        pos += length
        if body:
            messages.append(body.decode("utf-8", "replace"))
    return messages


def unescape(text: str) -> str:
    return text.replace("@S", "/").replace("@A", "@")


def fields(message: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in message.split("/"):
        if "@=" not in part:
            continue
        key, value = part.split("@=", 1)
        out[key] = unescape(value)
    return out


async def _real_room(session: aiohttp.ClientSession, room: str) -> str:
    url = f"https://open.douyucdn.cn/api/RoomApi/room/{room}"
    try:
        async with session.get(url, headers={"User-Agent": UA}, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            data = await resp.json(content_type=None)
        real = str((data.get("data") or {}).get("room_id") or "")
        return real or room
    except (aiohttp.ClientError, json.JSONDecodeError, asyncio.TimeoutError):
        return room


async def _heartbeat(ws: aiohttp.ClientWebSocketResponse, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=40)
            return
        except TimeoutError:
            if stop.is_set() or ws.closed:
                return
            await ws.send_bytes(encode("type@=mrkl/"))


async def run(target: str, emit: Emit, stop: asyncio.Event, status: Status) -> None:
    room = douyu_room(target)
    if not room:
        raise RuntimeError("请填写斗鱼房间号，或斗鱼直播间网址")
    async with aiohttp.ClientSession() as session:
        room = await _real_room(session, room)
        async with session.ws_connect(WS, headers={"User-Agent": UA}, ssl=_ssl(), heartbeat=None) as ws:
            await ws.send_bytes(encode(f"type@=loginreq/roomid@={room}/"))
            gid = "-9999"
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=6)
                if msg.type == aiohttp.WSMsgType.BINARY:
                    for text in decode(msg.data):
                        item = fields(text)
                        if item.get("type") == "loginres" and item.get("gid"):
                            gid = item["gid"]
            except TimeoutError:
                pass
            await ws.send_bytes(encode(f"type@=joingroup/rid@={room}/gid@={gid}/"))
            status(f"斗鱼 · 已连接 {room}", True)
            beat = asyncio.create_task(_heartbeat(ws, stop))
            try:
                async for msg in ws:
                    if stop.is_set():
                        return
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        payload = msg.data
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        payload = str(msg.data).encode("utf-8", "replace")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise RuntimeError("斗鱼弹幕连接中断")
                    else:
                        continue
                    for text in decode(payload):
                        item = fields(text)
                        if item.get("type") != "chatmsg":
                            continue
                        content = (item.get("txt") or "").strip()
                        user = (item.get("nn") or "").strip() or "观众"
                        if content:
                            await emit(DanmakuEvent("danmaku", user=user, text=content, meta={"platform": "douyu"}))
            finally:
                beat.cancel()
                try:
                    await beat
                except asyncio.CancelledError:
                    pass
            if not stop.is_set():
                raise RuntimeError("斗鱼弹幕连接中断")
