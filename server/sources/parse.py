"""把用户粘贴的网址或名字收成各平台认的那一个标识。"""
from __future__ import annotations

import re

_VIDEO = re.compile(r"(?:v=|youtu\.be/|youtube\.com/(?:live|shorts|embed)/)([A-Za-z0-9_-]{11})")
_VIDEO_BARE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_video_id(target: str) -> str:
    text = (target or "").strip()
    match = _VIDEO.search(text)
    if match:
        return match.group(1)
    if _VIDEO_BARE.match(text):
        return text
    return ""


def youtube_page(target: str) -> str:
    """不是视频号时，当成频道直播页去打开。"""
    text = (target or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("@"):
        return f"https://www.youtube.com/{text}/live"
    return f"https://www.youtube.com/@{text}/live"


def twitch_login(target: str) -> str:
    text = (target or "").strip()
    match = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", text, re.I)
    name = match.group(1) if match else text.lstrip("#").split("/")[0].split("?")[0]
    name = name.lower()
    if name in {"videos", "directory", "settings", "subscriptions", "popout"}:
        return ""
    return name


def kick_slug(target: str) -> str:
    text = (target or "").strip().strip("/")
    match = re.search(r"kick\.com/([A-Za-z0-9_]+)", text, re.I)
    slug = match.group(1) if match else text.split("/")[-1].split("?")[0]
    slug = slug.lower()
    if slug in {"video", "browse", "categories", "dashboard"}:
        return ""
    return slug


def douyu_room(target: str) -> str:
    text = (target or "").strip()
    match = re.search(r"douyu\.com/(?:topic/)?(\d+)", text, re.I)
    if match:
        return match.group(1)
    digits = re.search(r"\d+", text)
    return digits.group(0) if digits else ""


def bili_room(target: str) -> int:
    text = (target or "").strip()
    match = re.search(r"live\.bilibili\.com/(\d+)", text, re.I)
    if match:
        return int(match.group(1))
    digits = re.search(r"\d+", text)
    return int(digits.group(0)) if digits else 0
