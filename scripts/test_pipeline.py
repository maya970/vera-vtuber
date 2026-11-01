"""端到端自检：连接舞台 WS，模拟一条弹幕，验证 DeepSeek/兜底话术 → 语音 → 字幕 的完整链路。"""
from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8765"


async def main() -> int:
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        state = await (await session.get(f"{BASE}/api/state")).json()
        print(f"[*] 目录 {state['catalog']['count']} 本，DeepSeek {'已配置' if state['brain_ready'] else '未配置（用兜底话术）'}")

        ws = await session.ws_connect(f"{BASE}/ws")
        init = await ws.receive_json()
        print(f"[*] WS 已连接，发送 init：manifest={bool(init.get('manifest'))}")
        if init.get("manifest"):
            m = init["manifest"]
            print(f"    形象：{m.get('name')}，{len(m['layers'])} 层，画布 {m['canvas']['width']}x{m['canvas']['height']}")

        payload = {"user": "测试观众", "text": "有没有类似《诡秘之主》的小说？"}
        async with session.post(f"{BASE}/api/danmaku/test", json=payload) as resp:
            print(f"[*] 发送弹幕：{payload['text']} -> {resp.status}")

        got_speech = False
        async with session.get(f"{BASE}/api/state") as _:
            pass
        deadline = asyncio.get_event_loop().time() + 75
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=40)
            except asyncio.TimeoutError:
                print("[!] 等待消息超时")
                break
            t = msg.get("type")
            if t == "danmaku":
                print(f"[>] 弹幕入列：{msg.get('user')}：{msg.get('text')}")
            elif t == "speech":
                clips = msg.get("clips", [])
                total = sum(float(c.get("duration") or 0) for c in clips)
                print(f"[<] 语音 {len(clips)} 句，共 {total:.1f}s：")
                for c in clips:
                    words = c.get("words") or []
                    print(f"    - {c['text']}  ({c['duration']}s, {c['file']}, {len(words)} 词边界)")
                got_speech = True
                await ws.send_json({"type": "ack", "id": msg["id"]})
                break
            elif t == "status":
                print(f"[~] 状态 busy={msg.get('busy')} thinking={msg.get('thinking')}")
        await ws.close()

    if not got_speech:
        print("[x] 没有收到语音消息")
        return 1

    # 校验音频文件
    import pathlib

    audio_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "audio"
    files = sorted(audio_dir.glob("*.mp3"))
    print(f"[*] 音频缓存 {len(files)} 个，最新：{files[-1].name if files else '无'}")
    if not files:
        return 1
    print("[+] 链路自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
