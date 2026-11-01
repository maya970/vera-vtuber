"""独立测试 B 站弹幕 WebSocket：逐个尝试弹幕服务器与代理，找出能稳定握手的一条。"""
from __future__ import annotations

import asyncio
import json
import struct
import sys

import aiohttp
import brotli

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
PROXY = "http://127.0.0.1:7890"


def pack(auth: dict) -> bytes:
    body = json.dumps(auth, separators=(",", ":")).encode()
    return struct.pack(">IHHII", 16 + len(body), 16, 1, 7, 1) + body


async def attempt(host: str, port: int, proxy: str | None, real: int, token: str) -> bool:
    label = f"{host}:{port} proxy={bool(proxy)}"
    headers = {
        "User-Agent": UA,
        "Referer": f"https://live.bilibili.com/{real}",
        "Origin": "https://live.bilibili.com",
    }
    try:
        connector = aiohttp.TCPConnector(force_close=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.ws_connect(
                f"wss://{host}:{port}/sub", headers=headers, proxy=proxy, timeout=12, heartbeat=None
            ) as ws:
                await ws.send_bytes(
                    pack({"uid": 0, "roomid": real, "protover": 3, "platform": "web", "type": 2, "key": token})
                )
                deadline = asyncio.get_event_loop().time() + 16
                danmaku = 0
                authed = False
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=6)
                    except asyncio.TimeoutError:
                        print(f"  {label}: 等待超时（authed={authed}, danmaku={danmaku}）")
                        return authed
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        data = msg.data
                        plen, hlen, pver, op = struct.unpack(">IHHI", data[:12])
                        body = data[hlen:plen]
                        if pver == 3:
                            body = brotli.decompress(body)
                        elif pver == 2:
                            import zlib

                            body = zlib.decompress(body)
                        if op == 8:
                            authed = True
                            print(f"  {label}: 认证 {body[:80]!r}")
                        elif op == 5:
                            if b"DANMU_MSG" in body:
                                danmaku += 1
                                try:
                                    cmd = json.loads(body.decode("utf-8", errors="replace"))
                                    info = cmd.get("info", [])
                                    print(f"  {label}: 弹幕 {info[2][1]}: {info[1]}")
                                except Exception:  # noqa: BLE001
                                    print(f"  {label}: 收到弹幕包")
                            if danmaku >= 3:
                                return True
                        else:
                            print(f"  {label}: op={op} len={len(body)} {body[:100]!r}")
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        print(f"  {label}: 关闭 close={ws.close_code}")
                        return authed
                return authed or danmaku > 0
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: 异常 {type(exc).__name__} {exc}")
        return False
    return False


async def main(room: int = 6) -> int:
    headers = {"User-Agent": UA, "Referer": f"https://live.bilibili.com/{room}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.live.bilibili.com/room/v1/Danmu/getConf?room_id={room}&platform=pc&player=web",
            headers=headers,
        ) as r:
            conf = await r.json()
        token = conf["data"]["token"]
        hosts = conf["data"]["host_server_list"]
        seen: list[tuple[str, int]] = []
        for h in hosts:
            item = (h["host"], int(h.get("wss_port") or 443))
            if item not in seen:
                seen.append(item)
        subjects = seen[:3] + [("broadcastlv.chat.bilibili.com", 443)]
        for host, port in subjects:
            for proxy in (None, PROXY):
                if await attempt(host, port, proxy, room, token):
                    print(f"[+] 可用：{host}:{port} proxy={bool(proxy)}")
                    return 0
    print("[x] 所有组合都失败")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6)))
