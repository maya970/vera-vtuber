"""B 站直播弹幕客户端（WebSocket 直连，支持 protobuf 压缩包解析）。"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import struct
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import aiohttp
import brotli

from .config import config

log = logging.getLogger("vera.bili")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ROOM_INIT = "https://api.live.bilibili.com/room/v1/Room/room_init?id={room}"
DANMU_INFO = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id={room}&type=0"
DANMU_CONF = "https://api.live.bilibili.com/room/v1/Danmu/getConf?room_id={room}&platform=pc&player=web"
FINGER_SPI = "https://api.bilibili.com/x/frontend/finger/spi"


@dataclass
class DanmakuEvent:
    kind: str  # danmaku | super_chat | gift | guard | enter | follow
    user: str = ""
    text: str = ""
    uid: int = 0
    price: float = 0.0
    meta: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "user": self.user,
            "text": self.text,
            "uid": self.uid,
            "price": self.price,
            "meta": self.meta,
            "ts": self.ts,
        }


def _parse_header(data: bytes) -> tuple[int, int, int, int]:
    packet_len, header_len, protover, operation = struct.unpack(">IHHI", data[:12])
    return packet_len, header_len, protover, operation


def _decode_body(protover: int, body: bytes) -> bytes:
    if protover == 2:
        import zlib

        return zlib.decompress(body)
    if protover == 3:
        return brotli.decompress(body)
    return body


class BiliDanmaku:
    def __init__(self, on_event: Callable[[DanmakuEvent], Awaitable[None]]):
        self.on_event = on_event
        self.room_id = int(config.get("bilibili.room_id") or 0)
        self.real_room_id = 0
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.status = "未连接"
        self.connected = False
        self._last_error = ""
        self._buvid4 = ""

    # ---------- HTTP ----------
    def _raw_cookie(self) -> str:
        return (config.get("bilibili.sessdata") or "").strip()

    def _cookie(self) -> str:
        raw = self._raw_cookie()
        parts = []
        if raw:
            # 允许直接粘贴整段 Cookie（含 DedeUserID=…）；只填值则补成 SESSDATA=…
            parts.append(raw if "=" in raw else f"SESSDATA={raw}")
        buvid = config.get("bilibili.buvid3") or ""
        if buvid:
            parts.append(f"buvid3={buvid}")
        if getattr(self, "_buvid4", ""):
            parts.append(f"buvid4={self._buvid4}")
        parts.append(f"b_nut={int(time.time())}")
        return "; ".join(parts)

    def _uid(self) -> int:
        match = re.search(r"DedeUserID=(\d+)", self._raw_cookie())
        if match:
            return int(match.group(1))
        return int(config.get("bilibili.uid") or 0)

    def _headers(self) -> dict:
        headers = {
            "User-Agent": UA,
            "Referer": f"https://live.bilibili.com/{self.room_id or ''}",
            "Origin": "https://live.bilibili.com",
        }
        cookie = self._cookie()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    async def _get_json(self, url: str) -> dict:
        assert self._session
        async with self._session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=15)) as resp:
            return await resp.json(content_type=None)

    async def _resolve_room(self) -> int:
        try:
            data = await self._get_json(ROOM_INIT.format(room=self.room_id))
            if data.get("code") == 0:
                self.real_room_id = int(data["data"]["room_id"])
                return self.real_room_id
        except Exception as exc:  # noqa: BLE001
            log.debug("room_init 失败：%s", exc)
        # 兜底：get_info 也能给出真实房间号
        data = await self._get_json(
            f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={self.room_id}"
        )
        if data.get("code") != 0:
            raise RuntimeError(f"房间 {self.room_id} 不存在或不可访问：{data.get('code')}")
        self.real_room_id = int(data["data"]["room_id"])
        return self.real_room_id

    async def _ensure_buvid(self) -> None:
        if config.get("bilibili.buvid3") and getattr(self, "_buvid4", ""):
            return
        try:
            data = await self._get_json(FINGER_SPI)
            payload = data.get("data") or {}
            if payload.get("b_3") and not config.get("bilibili.buvid3"):
                config.set("bilibili.buvid3", payload["b_3"])
            if payload.get("b_4"):
                self._buvid4 = payload["b_4"]
        except Exception as exc:  # noqa: BLE001
            log.debug("获取 buvid 失败：%s", exc)

    async def _danmu_info(self) -> tuple[str, str, int]:
        """返回 (token, host, wss_port)。

        新版 getDanmuInfo 需要 WBI 签名，匿名请求经常 -352；
        旧版 getConf 仍可用，优先用它，失败再退回新版。
        """
        try:
            data = await self._get_json(DANMU_CONF.format(room=self.real_room_id))
            if data.get("code") == 0 and (data.get("data") or {}).get("token"):
                payload = data["data"]
                hosts = payload.get("host_server_list") or []
                if hosts:
                    host = hosts[0]
                    return payload["token"], host["host"], int(host.get("wss_port") or 443)
            log.debug("getConf 返回 %s，尝试 getDanmuInfo", data.get("code"))
        except Exception as exc:  # noqa: BLE001
            log.debug("getConf 失败：%s", exc)

        data = await self._get_json(DANMU_INFO.format(room=self.real_room_id))
        if data.get("code") != 0:
            raise RuntimeError(
                f"获取弹幕服务器失败：{data.get('code')}（可在控制台填 SESSDATA 后重试）"
            )
        payload = data["data"]
        hosts = payload.get("host_list") or []
        if not hosts:
            raise RuntimeError("弹幕服务器列表为空")
        return payload.get("token", ""), hosts[0]["host"], int(hosts[0].get("wss_port") or 443)

    # ---------- WS ----------
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="bili-danmaku")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._task = None
        self.connected = False
        self.status = "已停止"

    async def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                self.status = f"连接失败：{exc}"
                log.warning("弹幕连接失败：%s", exc)
                self.connected = False
                if not config.get("bilibili.reconnect", True):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.8, 30)

    async def _connect_once(self) -> None:
        if not self.room_id:
            self.status = "未配置直播间号"
            await asyncio.sleep(5)
            return
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        await self._ensure_buvid()
        real = await self._resolve_room()
        token, host, port = await self._danmu_info()
        url = f"wss://{host}:{port}/sub"
        self.status = "连接中…"
        log.info("连接弹幕服务器 %s room=%s", url, real)
        async with self._session.ws_connect(url, headers=self._headers(), heartbeat=None, timeout=20) as ws:
            self._ws = ws
            auth = {
                "uid": self._uid(),
                "roomid": real,
                "protover": 3,
                "platform": "web",
                "type": 2,
                "key": token,
            }
            if config.get("bilibili.buvid3"):
                auth["buvid"] = config.get("bilibili.buvid3")
            await ws.send_bytes(self._pack(auth))
            self.connected = True
            self.status = f"已连接房间 {real}"
            self._last_error = ""
            hb = asyncio.create_task(self._heartbeat(ws))
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        await self._handle_packet(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                hb.cancel()
                self.connected = False
                self._ws = None
                if not self._stop.is_set():
                    self.status = "连接断开，重连中…"

    @staticmethod
    def _pack(body: dict) -> bytes:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        # 包头 16 字节：总长(4) + 头长(2) + 协议版本(2) + 操作(4) + 序号(4)
        return struct.pack(">IHHII", 16 + len(data), 16, 1, 7, 1) + data

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        body = b"[object Object]"
        packet = struct.pack(">IHHII", 16 + len(body), 16, 1, 2, 1) + body
        while not ws.closed:
            try:
                await ws.send_bytes(packet)
            except Exception:  # noqa: BLE001
                return
            await asyncio.sleep(30)

    async def _handle_packet(self, data: bytes) -> None:
        if len(data) < 16:
            return
        packet_len, header_len, protover, operation = _parse_header(data)
        body = data[header_len:packet_len]
        if protover in (2, 3):
            try:
                await self._handle_packet(_decode_body(protover, body))
            except Exception as exc:  # noqa: BLE001
                log.debug("解压失败：%s", exc)
            return
        if operation == 5:
            # 可能是一整包多条命令
            offset = 0
            while offset + 16 <= len(data):
                plen, hlen, pver, op = _parse_header(data[offset:])
                if plen <= 0:
                    break
                chunk = data[offset : offset + plen]
                if pver in (2, 3):
                    await self._handle_packet(_decode_body(pver, chunk[hlen:]))
                else:
                    self._handle_command(chunk[hlen:])
                offset += plen
            return
        if operation == 8:
            try:
                payload = json.loads(body.decode("utf-8"))
                log.info("弹幕认证成功：%s", payload.get("code"))
            except Exception:  # noqa: BLE001
                pass

    def _handle_command(self, body: bytes) -> None:
        try:
            cmd = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return
        name = cmd.get("cmd", "").split(":")[0]
        try:
            if name == "DANMU_MSG":
                info = cmd["info"]
                text = info[1]
                user = (info[2][1] or "").strip()
                uid = int(info[2][0] or 0)
                medal = None
                try:
                    medal = {"name": info[3][1], "level": info[3][0]}
                except Exception:  # noqa: BLE001
                    pass
                self._emit(DanmakuEvent("danmaku", user=user, text=text, uid=uid, meta={"medal": medal}))
            elif name == "SUPER_CHAT_MESSAGE":
                data = cmd.get("data", {})
                user = ((data.get("user_info") or {}).get("uname") or "").strip()
                self._emit(
                    DanmakuEvent(
                        "super_chat",
                        user=user,
                        text=(data.get("message") or "").strip(),
                        price=float(data.get("price") or 0),
                    )
                )
            elif name == "SEND_GIFT" and config.get("bilibili.read_gifts", True):
                data = cmd.get("data", {})
                self._emit(
                    DanmakuEvent(
                        "gift",
                        user=(data.get("uname") or "").strip(),
                        text=(data.get("giftName") or "").strip(),
                        price=float(data.get("price") or 0) * int(data.get("num") or 1) / 1000,
                        meta={"num": data.get("num") or 1},
                    )
                )
            elif name == "GUARD_BUY" and config.get("bilibili.read_gifts", True):
                data = cmd.get("data", {})
                self._emit(
                    DanmakuEvent(
                        "guard",
                        user=(data.get("username") or "").strip(),
                        text=(data.get("gift_name") or "舰长").strip(),
                        price=float(data.get("price") or 0) / 1000,
                    )
                )
            elif name == "INTERACT_WORD" and config.get("bilibili.read_enter", True):
                data = cmd.get("data", {})
                msg_type = int(data.get("msg_type") or 0)
                if msg_type == 1:
                    kind = "enter"
                elif msg_type == 2:
                    kind = "follow"
                else:
                    return
                self._emit(DanmakuEvent(kind, user=(data.get("uname") or "").strip()))
            elif name in ("WATCHED_CHANGE", "ONLINE_RANK_COUNT", "ROOM_REAL_TIME_MESSAGE_UPDATE"):
                data = cmd.get("data", {}) or {}
                online = data.get("count") or data.get("online") or 0
                watched = data.get("num") or 0
                try:
                    online = int(online)
                except (TypeError, ValueError):
                    online = 0
                try:
                    watched = int(watched)
                except (TypeError, ValueError):
                    watched = 0
                if online or watched:
                    self._emit(DanmakuEvent("watching", meta={"online": online, "watched": watched}))
        except Exception as exc:  # noqa: BLE001
            log.debug("解析消息失败 %s：%s", name, exc)

    def _emit(self, event: DanmakuEvent) -> None:
        ignore = [u.lower() for u in (config.get("bilibili.ignore_users") or [])]
        if event.user and event.user.lower() in ignore:
            return
        if event.kind == "danmaku" and not event.text.strip():
            return
        if event.kind != "watching":
            log.info("[%s] %s: %s", event.kind, event.user, event.text[:40])
        asyncio.create_task(self.on_event(event))

    def state(self) -> dict:
        return {
            "room_id": self.room_id,
            "real_room_id": self.real_room_id,
            "connected": self.connected,
            "status": self.status,
            "error": self._last_error,
        }
