"""Twitch 公开聊天。匿名就能听，填了令牌就用令牌。"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Awaitable, Callable

import aiohttp

from ..config import config
from ..danmaku import DanmakuEvent
from .parse import twitch_login

log = logging.getLogger("vera.twitch")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
IRC = "wss://irc-ws.chat.twitch.tv:443"
_PRIVMSG = re.compile(
    r"^(?:@(?P<tags>\S*) )?(?::(?P<prefix>\S+) )?PRIVMSG #\S+ :(?P<text>.*)$"
)

Emit = Callable[[DanmakuEvent], Awaitable[None]]
Status = Callable[[str, bool], None]


def _token() -> str:
    raw = str(config.get("twitch.token") or "").strip()
    if not raw:
        return ""
    return raw if raw.lower().startswith("oauth:") else f"oauth:{raw}"


def _display(tags: str, prefix: str) -> str:
    for part in (tags or "").split(";"):
        if part.startswith("display-name="):
            name = part.split("=", 1)[1]
            if name:
                return name
    if prefix.startswith(":"):
        prefix = prefix[1:]
    return prefix.split("!", 1)[0]


async def run(target: str, emit: Emit, stop: asyncio.Event, status: Status) -> None:
    login = twitch_login(target)
    if not login:
        raise RuntimeError("请填写 Twitch 频道名，或 twitch.tv/频道 这样的网址")
    nick = f"justinfan{random.randint(10000, 99999)}"
    token = _token()
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(IRC, heartbeat=None, headers={"User-Agent": UA}) as ws:
            await ws.send_str("CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
            if token:
                await ws.send_str(f"PASS {token}\r\n")
            await ws.send_str(f"NICK {nick}\r\n")
            await ws.send_str(f"JOIN #{login}\r\n")
            status(f"Twitch · 正在进入 #{login}", False)
            async for msg in ws:
                if stop.is_set():
                    return
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise RuntimeError("Twitch 聊天连接中断")
                    continue
                for line in str(msg.data).split("\r\n"):
                    if not line:
                        continue
                    if line.startswith("PING"):
                        await ws.send_str("PONG :tmi.twitch.tv\r\n")
                        continue
                    if "Login authentication failed" in line:
                        raise RuntimeError("Twitch 令牌无效。可以先留空，用公开方式听；或换一把新的")
                    if " JOIN #" in line or line.startswith(":tmi.twitch.tv 001"):
                        status(f"Twitch · 已连接 #{login}", True)
                    matched = _PRIVMSG.match(line)
                    if not matched:
                        continue
                    text = matched.group("text").strip()
                    if not text:
                        continue
                    user = _display(matched.group("tags") or "", matched.group("prefix") or "")
                    await emit(DanmakuEvent("danmaku", user=user or "观众", text=text, meta={"platform": "twitch"}))
            if not stop.is_set():
                raise RuntimeError("Twitch 聊天连接中断")
