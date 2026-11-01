"""YouTube 公开直播聊天。有自己的 API Key 时走官方接口，否则读公开直播页。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Awaitable, Callable

import aiohttp

from ..config import config
from ..danmaku import DanmakuEvent
from .parse import youtube_page, youtube_video_id

log = logging.getLogger("vera.youtube")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# YouTube 网页里公开带着的客户端钥匙，不是用户的密钥。
_WEB_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_API = "https://www.googleapis.com/youtube/v3"

Emit = Callable[[DanmakuEvent], Awaitable[None]]
Status = Callable[[str, bool], None]


def _headers(video_id: str = "") -> dict:
    headers = {
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": "CONSENT=YES+; SOCS=CAISNQgD",
    }
    if video_id:
        headers["Referer"] = f"https://www.youtube.com/live_chat?v={video_id}"
        headers["Origin"] = "https://www.youtube.com"
    return headers


def _continuation(blob: str) -> tuple[str, int]:
    for key in ("invalidationContinuationData", "timedContinuationData"):
        index = blob.find(f'"{key}"')
        if index < 0:
            continue
        chunk = blob[index:index + 1200]
        found = re.search(r'"continuation"\s*:\s*"([^"]+)"', chunk)
        if not found:
            continue
        wait = re.search(r'"timeoutMs"\s*:\s*(\d+)', chunk)
        return found.group(1), int(wait.group(1)) if wait else 2000
    return "", 2000


def _runs(node: dict) -> str:
    parts = []
    for run in (node or {}).get("runs") or []:
        if run.get("text"):
            parts.append(run["text"])
        else:
            shortcuts = ((run.get("emoji") or {}).get("shortcuts") or [])
            if shortcuts:
                parts.append(shortcuts[0])
    return "".join(parts).strip()


def _events_from_actions(actions: list) -> list[DanmakuEvent]:
    found: list[DanmakuEvent] = []
    for action in actions or []:
        item = (action.get("addChatItemAction") or {}).get("item") or {}
        paid = item.get("liveChatPaidMessageRenderer")
        text_node = item.get("liveChatTextMessageRenderer") or paid
        if not text_node:
            continue
        text = _runs(text_node.get("message") or {})
        author = ((text_node.get("authorName") or {}).get("simpleText") or "").strip() or "观众"
        if paid:
            amount = ((paid.get("purchaseAmountText") or {}).get("simpleText") or "").strip()
            price = 0.0
            digits = re.search(r"[\d.]+", amount.replace(",", ""))
            if digits:
                try:
                    price = float(digits.group(0))
                except ValueError:
                    price = 0.0
            body = text or amount or "超级留言"
            found.append(DanmakuEvent("super_chat", user=author, text=body, price=price, meta={"platform": "youtube"}))
            continue
        if text:
            found.append(DanmakuEvent("danmaku", user=author, text=text, meta={"platform": "youtube"}))
    return found


async def _resolve_video(session: aiohttp.ClientSession, target: str) -> str:
    video = youtube_video_id(target)
    if video:
        return video
    page = youtube_page(target)
    if not page:
        raise RuntimeError("请填写 YouTube 直播网址、视频号，或 @频道")
    async with session.get(page, headers=_headers(), allow_redirects=True) as resp:
        html = await resp.text(errors="replace")
        final = str(resp.url)
    video = youtube_video_id(final) or youtube_video_id(html[:8000])
    if not video:
        match = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        video = match.group(1) if match else ""
    if not video:
        raise RuntimeError("这个频道现在没有公开直播")
    return video


async def _sleep(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.2, seconds))
        return True
    except TimeoutError:
        return False


async def _run_api(session: aiohttp.ClientSession, video_id: str, key: str, emit: Emit, stop: asyncio.Event, status: Status) -> None:
    params = {"part": "liveStreamingDetails", "id": video_id, "key": key}
    async with session.get(f"{_API}/videos", params=params, headers=_headers(video_id)) as resp:
        data = await resp.json(content_type=None)
    if data.get("error"):
        raise RuntimeError(data["error"].get("message") or "YouTube 接口拒绝了这把钥匙")
    items = data.get("items") or []
    chat_id = ((items[0].get("liveStreamingDetails") or {}).get("activeLiveChatId") if items else "") or ""
    if not chat_id:
        raise RuntimeError("这个视频现在没有公开直播聊天")
    status("YouTube · 已连接", True)
    page = ""
    while not stop.is_set():
        query = {
            "part": "snippet,authorDetails",
            "liveChatId": chat_id,
            "key": key,
        }
        if page:
            query["pageToken"] = page
        async with session.get(f"{_API}/liveChat/messages", params=query, headers=_headers(video_id)) as resp:
            payload = await resp.json(content_type=None)
        if payload.get("error"):
            raise RuntimeError(payload["error"].get("message") or "读取 YouTube 聊天失败")
        for item in payload.get("items") or []:
            snippet = item.get("snippet") or {}
            author = ((item.get("authorDetails") or {}).get("displayName") or "").strip() or "观众"
            text = (snippet.get("displayMessage") or "").strip()
            if not text:
                continue
            kind = "super_chat" if snippet.get("superChatDetails") else "danmaku"
            price = float((snippet.get("superChatDetails") or {}).get("amountMicros") or 0) / 1_000_000
            await emit(DanmakuEvent(kind, user=author, text=text, price=price, meta={"platform": "youtube"}))
        page = payload.get("nextPageToken") or ""
        wait = int(payload.get("pollingIntervalMillis") or 5000) / 1000
        if await _sleep(stop, min(max(wait, 1), 15)):
            return


async def _run_page(session: aiohttp.ClientSession, video_id: str, emit: Emit, stop: asyncio.Event, status: Status) -> None:
    url = f"https://www.youtube.com/live_chat?is_embed=1&v={video_id}"
    async with session.get(url, headers=_headers(video_id)) as resp:
        html = await resp.text(errors="replace")
    key_match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    ver_match = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    key = key_match.group(1) if key_match else _WEB_KEY
    version = ver_match.group(1) if ver_match else "2.20251001.01.00"
    continuation, wait_ms = _continuation(html)
    if not continuation:
        raise RuntimeError("这个地址现在没有公开的直播聊天")
    status("YouTube · 已连接", True)
    endpoint = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={key}"
    while not stop.is_set():
        body = {
            "context": {"client": {"hl": "zh-CN", "gl": "US", "clientName": "WEB", "clientVersion": version}},
            "continuation": continuation,
        }
        async with session.post(endpoint, json=body, headers={**_headers(video_id), "Content-Type": "application/json"}) as resp:
            raw = await resp.text(errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("YouTube 聊天没有返回可识别的内容") from exc
        live = ((payload.get("continuationContents") or {}).get("liveChatContinuation") or {})
        for event in _events_from_actions(live.get("actions") or []):
            await emit(event)
        blob = json.dumps(live.get("continuations") or [], ensure_ascii=False)
        continuation, wait_ms = _continuation(blob)
        if not continuation:
            raise RuntimeError("YouTube 直播聊天已结束")
        if await _sleep(stop, min(max(wait_ms / 1000, 1), 12)):
            return


async def run(target: str, emit: Emit, stop: asyncio.Event, status: Status) -> None:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        video_id = await _resolve_video(session, target)
        status(f"YouTube · 连接 {video_id}", False)
        key = str(config.get("youtube.api_key") or "").strip()
        if key:
            await _run_api(session, video_id, key, emit, stop, status)
        else:
            await _run_page(session, video_id, emit, stop, status)
