"""用 Edge DevTools 协议按真实时间等待后截图（比 --screenshot 的虚拟时间可靠）。

用法：
    python scripts/shot.py --url http://127.0.0.1:8765/stage --wait 16 --out docs/preview.png
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_edge() -> str:
    for path in EDGE_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit("找不到 msedge.exe")


async def wait_for_devtools(port: int, timeout: float = 20) -> str:
    start = time.time()
    async with aiohttp.ClientSession() as session:
        while time.time() - start < timeout:
            try:
                async with session.get(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
                    data = await resp.json()
                    return data["webSocketDebuggerUrl"]
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.3)
    raise SystemExit("Edge DevTools 没有就绪")


class CDP:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse):
        self.ws = ws
        self.seq = 0
        self.session_id = ""

    async def send(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self.seq += 1
        msg = {"id": self.seq, "method": method, "params": params or {}}
        sid = session if session is not None else self.session_id
        if sid:
            msg["sessionId"] = sid
        await self.ws.send_json(msg)
        while True:
            reply = await self.ws.receive_json()
            if reply.get("id") == self.seq:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/stage")
    parser.add_argument("--wait", type=float, default=14.0)
    parser.add_argument("--out", default="docs/preview.png")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()

    edge = find_edge()
    port = 9333
    profile = Path(tempfile.gettempdir()) / "vera-edge-shot"
    shutil.rmtree(profile, ignore_errors=True)
    proc = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu-sandbox",
            "--enable-unsafe-swiftshader",
            "--use-angle=swiftshader",
            "--hide-scrollbars",
            "--autoplay-policy=no-user-gesture-required",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = await wait_for_devtools(port)
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(ws_url, max_msg_size=64 * 1024 * 1024)
            cdp = CDP(ws)
            target = await cdp.send("Target.createTarget", {"url": "about:blank"})
            attached = await cdp.send("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
            cdp.session_id = attached["sessionId"]
            await cdp.send("Page.enable")
            await cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {"width": args.width, "height": args.height, "deviceScaleFactor": 1, "mobile": False},
            )
            await cdp.send("Page.navigate", {"url": args.url})
            print(f"[*] 已打开 {args.url}，等待 {args.wait:.1f}s …")
            await asyncio.sleep(args.wait)
            shot = await cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(base64.b64decode(shot["data"]))
            print(f"[+] 截图已保存 {out}（{out.stat().st_size / 1024:.0f} KB）")
            await ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
