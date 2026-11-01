"""验证“空闲讲书”：把空闲阈值调到几秒，等导演自动开讲并打印书卡与语音。"""
from __future__ import annotations

import asyncio
import sys

import aiohttp

BASE = "http://127.0.0.1:8765"


async def main() -> int:
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ws = await session.ws_connect(f"{BASE}/ws")
        await ws.receive_json()
        await session.post(f"{BASE}/api/config", json={"director": {"idle_book_after": 4}})
        print("[*] 空闲阈值设为 4 秒，等待自动讲书 …")

        book = None
        got_speech = False
        deadline = asyncio.get_event_loop().time() + 80
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=40)
            except asyncio.TimeoutError:
                break
            t = msg.get("type")
            if t == "book":
                book = msg.get("book")
                print(f"[书卡] 《{book['title']}》 {book['author']} {book['score']}分 "
                      f"{book['raters']}人评 {'/'.join(book['tags'][:4])} {book['words']/10000:.0f}万字")
                if book.get("quotes"):
                    print(f"       读者短评：{book['quotes'][0]}")
            elif t == "speech":
                clips = msg.get("clips", [])
                print(f"[语音] {len(clips)} 句：")
                for c in clips:
                    print(f"    - {c['text']} ({c['duration']}s, {len(c.get('words') or [])} 词边界)")
                got_speech = True
                await ws.send_json({"type": "ack", "id": msg["id"]})
                break
        await ws.close()
        await session.post(f"{BASE}/api/config", json={"director": {"idle_book_after": 30}})

    state = await (await aiohttp.ClientSession().get(f"{BASE}/api/state")).json()
    print(f"[*] books={state['director']['stats']['books']} errors={state['director']['stats']['errors']}")
    print(f"[*] 已讲：{[p['title'] for p in state['played']]}")
    return 0 if (book and got_speech) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
