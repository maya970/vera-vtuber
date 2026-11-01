"""本地模拟 OpenAI/DeepSeek 流式接口，用来在没有 Key 时验证大脑链路。

启动：python scripts/mock_llm.py --port 9911
然后控制台把 base_url 改成 http://127.0.0.1:9911/v1，Key 随便填。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from aiohttp import web

REPLY = (
    "这本我熟。它最妙的地方是，把日常写得像一场慢慢收紧的梦。"
    "适合夜里读，读到第三页就会想把灯调暗一点。"
    "先说好，不剧透——你要是喜欢，回来跟我聊聊那只猫。"
)


async def chat(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    stream = bool(body.get("stream"))
    user_text = ""
    for message in body.get("messages", []):
        if message.get("role") == "user":
            user_text = str(message.get("content", ""))
    print(f"[mock] stream={stream} user={user_text[:60]}")

    if not stream:
        return web.json_response(
            {
                "id": "mock",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "deepseek-chat"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "在线"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
        )

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)
    pieces = [REPLY[i : i + 9] for i in range(0, len(REPLY), 9)]
    for piece in pieces:
        chunk = {
            "id": "mock",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "deepseek-chat",
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        await resp.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        await asyncio.sleep(0.05)
    done = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "deepseek-chat",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    await resp.write(f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8"))
    await resp.write(b"data: [DONE]\n\n")
    await resp.write_eof()
    return resp


async def models(_: web.Request) -> web.Response:
    return web.json_response({"object": "list", "data": [{"id": "deepseek-chat", "object": "model"}]})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9911)
    args = parser.parse_args()
    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_get("/v1/models", models)
    print(f"[mock] listening http://127.0.0.1:{args.port}/v1")
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
